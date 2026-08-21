"""Connecteurs retail publics et prudents pour LUXE RADAR.

Ces connecteurs lisent uniquement les pages de recherche/catégorie publiques.
Ils ne contournent ni CAPTCHA, ni authentification, ni contrôle d'accès.
Ils privilégient le JSON-LD Product/ItemList, puis un parseur HTML conservateur.
Une page sans cartes exploitables renvoie simplement [].
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote, quote_plus, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector
from ..source_health import registry as _source_health

IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    or os.environ.get("LUXE_RADAR_ENV", "").lower() == "production"
)
HTTP_TIMEOUT = (2.5 if IS_RENDER else 4.0, 5.5 if IS_RENDER else 10.0)
# V4.1 : budget temps réel par recherche publique. Une source lente ne doit
# plus faire attendre le pipeline 25-30 s : on dépasse la deadline et on
# renvoie ce qu'on a, sans essayer d'autres routes.
SEARCH_BUDGET_SECONDS = 8.0 if IS_RENDER else 14.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
}

# Évite de re-frapper en boucle une source publique qui vient de refuser la requête.
# Ce cache est uniquement mémoire/processus et expire automatiquement.
_SOURCE_COOLDOWN_UNTIL = {}
_SOURCE_COOLDOWN_SECONDS = 600 if IS_RENDER else 90


_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_PRICE_RE = re.compile(r"(?<!\d)([0-9]{1,5}(?:[.,][0-9]{2})?)\s*(?:€|EUR)(?![A-Z])", re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_IMG_RE = re.compile(r'<img[^>]+(?:src|data-src|data-original)=["\']([^"\']+)["\']', re.I)
_TITLE_RE = re.compile(r'<(?:h2|h3|h4)[^>]*>(.*?)</(?:h2|h3|h4)>', re.I | re.S)
_ALT_RE = re.compile(r'<img[^>]+alt=["\']([^"\']+)["\']', re.I | re.S)
_BLOCK_RE = re.compile(r'<(?:article|li)\b[^>]*>(.*?)</(?:article|li)>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_DIV_OPEN_RE = re.compile(r"<div\b[^>]*>", re.I)
_DIV_CLOSE_RE = re.compile(r"</div\s*>", re.I)
_DIV_CARD_MARKERS = (
    "product-card",
    "product_card",
    "tp-product-card",
    "productcard",
    "product-tile",
    "product-item",
    "display_product",
)
_DATA_NAME_RE = re.compile(r'data-(?:cnstrc|tp-gtm)-item-name="([^"]*)"', re.I)
_DATA_PRICE_RE = re.compile(r'data-(?:cnstrc|tp-gtm)-item-price="([0-9.,]+)"', re.I)


def _norm(value):
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("-", " ").replace("_", " ").replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s']", " ", text)).strip()


def _safe_float(value):
    try:
        if isinstance(value, str):
            value = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        return float(value)
    except (TypeError, ValueError):
        return None


def _session():
    retry_budget = 0 if IS_RENDER else 1
    retry = Retry(
        total=retry_budget,
        connect=retry_budget,
        read=retry_budget,
        status=retry_budget,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


def _same_public_host(candidate_url, base_url):
    """Autorise uniquement les redirections HTTP(S) restant sur le marchand.

    Certains frontends exposent dans leur HTML des placeholders JS du type
    `${searchAction}`. `requests` essayait auparavant de les résoudre comme un
    vrai nom DNS. On refuse simplement ces cibles au lieu de les suivre.
    """
    try:
        candidate = urlparse(candidate_url)
        base = urlparse(base_url)
    except Exception:
        return False
    if candidate.scheme not in {"http", "https"} or not candidate.netloc:
        return False
    if any(marker in candidate.netloc for marker in ("$", "{", "}", "%7b", "%7d")):
        return False
    host = candidate.hostname.casefold().removeprefix("www.") if candidate.hostname else ""
    base_host = base.hostname.casefold().removeprefix("www.") if base.hostname else ""
    return bool(host and base_host and (host == base_host or host.endswith("." + base_host)))


def _safe_public_get(session, url, base_url, *, max_redirects=3):
    current = url
    for _hop in range(max_redirects + 1):
        response = session.get(current, timeout=HTTP_TIMEOUT, allow_redirects=False)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = str(response.headers.get("Location") or "").strip()
        if not location:
            return response
        candidate = urljoin(current, location)
        if not _same_public_host(candidate, base_url):
            return None
        current = candidate
    return None


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _image_value(value):
    if isinstance(value, list):
        value = next((x for x in value if x), None)
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if not value:
        return None
    value = html_lib.unescape(str(value).strip())
    if value.startswith("//"):
        value = "https:" + value
    return value


def _offers(value):
    if isinstance(value, list):
        for offer in value:
            if isinstance(offer, dict):
                yield offer
    elif isinstance(value, dict):
        # AggregateOffer: use lowPrice if no direct price.
        yield value
        for key in ("offers", "itemOffered"):
            child = value.get(key)
            if isinstance(child, list):
                for offer in child:
                    if isinstance(offer, dict):
                        yield offer


def parse_jsonld_products(text, base_url):
    results = []
    seen = set()
    for raw in _JSONLD_RE.findall(text or ""):
        try:
            data = json.loads(html_lib.unescape(raw.strip()))
        except Exception:
            continue
        for node in _walk_json(data):
            node_type = node.get("@type")
            types = {str(x) for x in node_type} if isinstance(node_type, list) else {str(node_type)}
            if "Product" not in types:
                continue
            title = " ".join(str(node.get("name") or "").split())
            if not title:
                continue
            offer = next(iter(_offers(node.get("offers"))), {})
            price = _safe_float(offer.get("price") or offer.get("lowPrice") or node.get("price"))
            currency = str(offer.get("priceCurrency") or node.get("priceCurrency") or "EUR").upper()
            url = str(offer.get("url") or node.get("url") or "").strip()
            if url:
                url = urljoin(base_url, html_lib.unescape(url))
            image = _image_value(node.get("image"))
            if price is None or price <= 0 or not url:
                continue
            key = (url, round(price, 2))
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "titre": title,
                "prix": round(price, 2),
                "devise_originale": currency or "EUR",
                "image": image,
                "lien": url,
                "reference": str(node.get("sku") or node.get("mpn") or "").strip() or None,
                "disponible": "OutOfStock" not in str(offer.get("availability") or ""),
            })
    return results


def _clean_html_text(fragment):
    return " ".join(html_lib.unescape(_TAG_RE.sub(" ", fragment or "")).split())


def parse_html_cards(text, base_url, allowed_path_hints=()):
    """Fallback conservateur pour pages sans JSON-LD Product.

    On ne produit une carte que si le bloc contient lien + titre + prix EUR.
    """
    host = urlparse(base_url).netloc.casefold().removeprefix("www.")
    results = []
    seen = set()
    blocks = _BLOCK_RE.findall(text or "")
    for block in blocks[:600]:
        price_match = _PRICE_RE.search(_clean_html_text(block))
        href_match = _HREF_RE.search(block)
        if not price_match or not href_match:
            continue
        href = html_lib.unescape(href_match.group(1)).strip()
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        item_host = parsed.netloc.casefold().removeprefix("www.")
        if not item_host or (item_host != host and not item_host.endswith("." + host)):
            continue
        if allowed_path_hints and not any(hint in parsed.path.casefold() for hint in allowed_path_hints):
            continue
        title_match = _TITLE_RE.search(block)
        alt_match = _ALT_RE.search(block)
        title = _clean_html_text(title_match.group(1)) if title_match else ""
        if not title and alt_match:
            title = " ".join(html_lib.unescape(alt_match.group(1)).split())
        if not title or len(title) < 4:
            continue
        price = _safe_float(price_match.group(1))
        if price is None or price <= 0:
            continue
        img_match = _IMG_RE.search(block)
        image = urljoin(base_url, html_lib.unescape(img_match.group(1))) if img_match else None
        key = (url, round(price, 2))
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "titre": title,
            "prix": round(price, 2),
            "devise_originale": "EUR",
            "image": image,
            "lien": url,
            "reference": None,
            "disponible": True,
        })
    return results


def _balanced_div_block(text, start):
    """Fin du <div> ouvert à `start` (index du '<div'), en comptant l'imbrication."""
    length = len(text)
    limit = min(length, start + 40000)
    i = start
    depth = 0
    while i < limit:
        open_match = _DIV_OPEN_RE.search(text, i, limit)
        close_match = _DIV_CLOSE_RE.search(text, i, limit)
        if close_match is None:
            return limit
        if open_match is not None and open_match.start() < close_match.start():
            depth += 1
            i = open_match.end()
        else:
            depth -= 1
            i = close_match.end()
            if depth == 0:
                return i
    return limit


def parse_product_div_cards(text, base_url, allowed_path_hints=()):
    """Cartes produit encodées en <div> (Shopify/React/Magento sans JSON-LD).

    Conservateur : on ne produit une carte que si le bloc contient un lien
    produit + un titre + un prix EUR, sinon elle est rejetée.
    """
    host = urlparse(base_url).netloc.casefold().removeprefix("www.")
    results = []
    seen = set()
    text = text or ""
    for match in _DIV_OPEN_RE.finditer(text):
        tag = match.group(0)
        class_match = re.search(r'class=["\']([^"\']*)["\']', tag, re.I)
        class_name = (class_match.group(1) or "").casefold() if class_match else ""
        if not any(marker in class_name for marker in _DIV_CARD_MARKERS):
            continue
        end = _balanced_div_block(text, match.start())
        block = text[match.end():end]
        full = tag + block
        price = None
        data_price = _DATA_PRICE_RE.search(full)
        if data_price:
            price = _safe_float(data_price.group(1))
        if price is None:
            price_match = _PRICE_RE.search(_clean_html_text(block))
            if price_match:
                price = _safe_float(price_match.group(1))
        if price is None or price <= 0:
            continue
        href = None
        for href_match in _HREF_RE.finditer(block):
            candidate = urljoin(base_url, html_lib.unescape(href_match.group(1)).strip())
            parsed = urlparse(candidate)
            item_host = parsed.netloc.casefold().removeprefix("www.")
            if not item_host or (item_host != host and not item_host.endswith("." + host)):
                continue
            if allowed_path_hints and not any(hint in parsed.path.casefold() for hint in allowed_path_hints):
                continue
            href = candidate
            break
        if not href:
            continue
        title = ""
        name_match = _DATA_NAME_RE.search(full)
        if name_match:
            title = " ".join(html_lib.unescape(name_match.group(1)).split())
        if not title:
            anchor = re.search(r'<a\b[^>]*title="([^"]*)"', block, re.I)
            if anchor:
                title = " ".join(html_lib.unescape(anchor.group(1)).split())
        if not title:
            heading = _TITLE_RE.search(block)
            if heading:
                title = _clean_html_text(heading.group(1))
        if not title:
            alt_match = _ALT_RE.search(block)
            if alt_match:
                title = " ".join(html_lib.unescape(alt_match.group(1)).split())
        if not title or len(title) < 4:
            continue
        img_match = _IMG_RE.search(block)
        image = urljoin(base_url, html_lib.unescape(img_match.group(1))) if img_match else None
        key = (href, round(price, 2))
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "titre": title,
            "prix": round(price, 2),
            "devise_originale": "EUR",
            "image": image,
            "lien": href,
            "reference": None,
            "disponible": True,
        })
    return results


class _PublicRetailBase(MarketplaceConnector):
    name = "Marketplace"  # ignoré par l'auto-loader
    display_name = "Marketplace"
    enabled = True
    currency = "EUR"
    base_url = ""
    search_template = ""
    # V3.5.0 : plusieurs boutiques changent parfois de route de recherche.
    # On peut fournir 2-3 routes publiques candidates, sans aucun contournement.
    search_templates = ()
    allowed_path_hints = ()

    supports_pagination = True
    expansion_page_size = 50
    expansion_recall_cap = 120
    max_pages = 3
    empty_pages_threshold = 3
    cooldown_seconds = 0.3

    def build_search_urls(self, query, page=1):
        values = self.search_templates or ((self.search_template,) if self.search_template else ())
        q = quote_plus(str(query or "").strip())
        slug = quote(str(query or "").strip().replace(" ", "-"), safe="-")
        page = max(1, int(page or 1))
        urls = []
        for template in values:
            if not template:
                continue
            url = str(template).format(q=q, slug=slug, page=page)
            if url not in urls:
                urls.append(url)
        return urls

    def build_search_url(self, query, page=1):
        urls = self.build_search_urls(query, page=page)
        return urls[0] if urls else self.base_url

    def search(self, query, price_max=None, limit=20, page=1):
        query = str(query or "").strip()
        if not query:
            return []
        try:
            limit = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            limit = 20
        try:
            page = max(1, min(int(page or 1), 50))
        except (TypeError, ValueError):
            page = 1
        price_max_f = _safe_float(price_max)
        if price_max_f is not None and price_max_f <= 0:
            return []

        now = time.monotonic()
        blocked_until = float(_SOURCE_COOLDOWN_UNTIL.get(self.name, 0) or 0)
        if blocked_until > now:
            remaining = max(1, int(blocked_until - now))
            print(f"[{self.name}] pause temporaire active ({remaining}s) -> requête ignorée")
            return []
        if blocked_until:
            _SOURCE_COOLDOWN_UNTIL.pop(self.name, None)

        urls = self.build_search_urls(query, page=page)
        print(f"[{self.name}] Recherche publique : {query} | page={page}")
        session = _session()
        try:
            started = time.monotonic()
            deadline = started + SEARCH_BUDGET_SECONDS
            raw = []
            last_status = None
            # Sur Render on limite à 2 routes candidates pour rester rapide.
            route_budget = 2 if IS_RENDER else 3
            for route_index, url in enumerate(urls[:route_budget], start=1):
                if time.monotonic() > deadline:
                    print(f"[{self.name}] budget de recherche atteint ({SEARCH_BUDGET_SECONDS:g}s) -> réponse partielle")
                    break
                try:
                    response = _safe_public_get(session, url, self.base_url)
                except requests.RequestException as exc:
                    print(f"[{self.name}] Route {route_index} indisponible : {exc}")
                    continue
                if response is None:
                    print(f"[{self.name}] redirection publique invalide/hors domaine -> route ignorée")
                    continue
                last_status = response.status_code
                if response.status_code != 200:
                    if response.status_code in {400, 403, 429}:
                        _source_health.record_http(self.name, response.status_code)
                        print(f"[{self.name}] route {route_index} HTTP {response.status_code} -> ignorée")
                    continue
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    continue
                text = response.text or ""
                low = _norm(text[:12000])
                if any(marker in low for marker in ("captcha", "access denied", "verify you are human", "cf challenge")):
                    print(f"[{self.name}] Contrôle d'accès détecté -> route ignorée")
                    continue
                raw = parse_jsonld_products(text, self.base_url)
                if not raw:
                    raw = parse_html_cards(text, self.base_url, self.allowed_path_hints)
                if not raw:
                    raw = parse_product_div_cards(text, self.base_url, self.allowed_path_hints)
                print(
                    f"[{self.name}][ROUTE {route_index}] "
                    f"page={page} cartes_parsées={len(raw)} "
                    f"route_ms={int((time.monotonic() - started) * 1000)}"
                )
                if raw:
                    break
            if not raw and last_status in {400, 403, 429}:
                _SOURCE_COOLDOWN_UNTIL[self.name] = time.monotonic() + _SOURCE_COOLDOWN_SECONDS
                _source_health.record_blocked(self.name, "routes publiques refusees")
                print(f"[{self.name}] aucune route publique exploitable -> pause temporaire")

            # --- Fallback navigateur si le connecteur en a un configuré ---
            # Se déclenche quand HTTP ne donne rien (0 cartes) quelle que soit la cause.
            _has_browser = (
                getattr(self, "browser_search_template", None)
                or getattr(self, "browser_search_input_sel", None)
            )
            if not raw and _has_browser:
                try:
                    from .browser_fallback import search_via_browser, browser_available
                    if browser_available():
                        browser_results = search_via_browser(self, query, price_max, limit)
                        if browser_results:
                            return browser_results
                except Exception as _bf_err:
                    print(f"[{self.name}] fallback navigateur echoue: {_bf_err}")

            # --- Fallback navigateur global: si tout echoue et qu'un selecteur cartes existe ---
            if not raw and getattr(self, "browser_card_sel", None) and not _has_browser:
                try:
                    from .browser_fallback import search_via_browser, browser_available
                    if browser_available():
                        fallback_url = urls[0] if urls else self.base_url
                        self.browser_search_template = fallback_url.replace(quote(query), "{q}")
                        browser_results = search_via_browser(self, query, price_max, limit)
                        if browser_results:
                            return browser_results
                except Exception as _bf_err:
                    print(f"[{self.name}] fallback navigateur global echoue: {_bf_err}")

            results = []
            seen = set()
            parsed = 0
            for item in raw:
                title = str(item.get("titre") or "").strip()
                price = _safe_float(item.get("prix"))
                link = str(item.get("lien") or "").strip()
                if not title or price is None or price <= 0 or not link:
                    continue
                if price_max_f is not None and price > price_max_f:
                    continue
                key = (link, round(price, 2))
                if key in seen:
                    continue
                seen.add(key)
                parsed += 1
                results.append({
                    "marketplace": self.name,
                    "titre": title,
                    "prix": round(price, 2),
                    "prix_original": round(price, 2),
                    "prix_compare_original": None,
                    "devise_originale": str(item.get("devise_originale") or "EUR").upper(),
                    "devise": "EUR",
                    "lien": link,
                    "image": item.get("image"),
                    "modele": None,
                    "reference": item.get("reference"),
                    "vendor": None,
                    "type_produit_site": None,
                    "disponible": bool(item.get("disponible", True)),
                    "reduction_pourcent": None,
                    "categorie": "A VERIFIER",
                    "score": 72,
                    "score_match": 78,
                    "score_confiance": 58,
                    "score_affaire": 52,
                    "alertes": ["Vérifier disponibilité, taille et frais sur le site marchand"],
                    "raisons": ["Carte produit lue depuis une page retail publique"],
                })
                if len(results) >= limit:
                    break
            print(
                f"[{self.name}] raw={len(raw)} parsed={parsed} retenus={len(results)} "
                f"durée={time.monotonic() - started:.2f}s"
            )
            return results
        except requests.RequestException as exc:
            print(f"[{self.name}] Réseau indisponible : {exc}")
            return []
        finally:
            session.close()

    def search_page(self, query, price_max=None, limit=20, page=1):
        return self.search(query=query, price_max=price_max, limit=limit, page=page)


class SpartooConnector(_PublicRetailBase):
    name = "Spartoo"
    display_name = "Spartoo"
    base_url = "https://www.spartoo.com"
    search_template = "https://www.spartoo.com/recherche.php?keywords={q}&page={page}"
    allowed_path_hints = ("-x", "/modele-", "/product", "/produit")


class FootshopConnector(_PublicRetailBase):
    name = "Footshop"
    display_name = "Footshop"
    base_url = "https://www.footshop.eu"
    search_templates = (
        "https://www.footshop.eu/en/search?search_query={q}&page={page}",
        "https://www.footshop.eu/en/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/en/", "/product", "/sneakers", "/shoes")
    # Browser fallback: React SPA, recherche côté client
    browser_search_template = "https://www.footshop.eu/en/search?search_query={q}"
    browser_card_sel = "[itemprop='itemListElement']"
    browser_title_sel = "h3"
    browser_price_sel = "[class*='rice']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 5000


class JDSportsConnector(_PublicRetailBase):
    name = "JD Sports"
    display_name = "JD Sports"
    base_url = "https://www.jdsports.fr"
    search_template = "https://www.jdsports.fr/search/{slug}/?page={page}"
    allowed_path_hints = ("/product/", "/produit/", "/p/")
    # Browser fallback: Playwright contourne le WAF
    browser_search_template = "https://www.jdsports.fr/search/{q}/"
    browser_card_sel = "[class*='productListItem']"
    browser_title_sel = "img"
    browser_price_sel = "[class*='rice']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 5000


# V3.5.0 — FASHION / RUNNING EXPANSION
# Boutiques vérifiées comme actives publiquement en août 2026. Les routes
# ci-dessous restent volontairement conservatrices : 200 + carte produit
# exploitable, sinon la source renvoie [] et passe en cooldown.

class IRunConnector(_PublicRetailBase):
    name = "i-Run"
    display_name = "i-Run"
    base_url = "https://www.i-run.fr"
    # V4.2 : la route ?q= fonctionne (200, 60 cartes) ; ?search= mène vers une
    # redirection hors domaine. On la garde en dernier recours seulement.
    search_templates = (
        "https://www.i-run.fr/recherche.html?q={q}&page={page}",
        "https://www.i-run.fr/recherche.html?keywords={q}&page={page}",
        "https://www.i-run.fr/recherche.html?search={q}&page={page}",
    )
    allowed_path_hints = ("/chaussures", "/vetements", "/running", "/trail", ".html")


class DirectRunningConnector(_PublicRetailBase):
    name = "Direct Running"
    display_name = "Direct Running"
    base_url = "https://direct-running.fr"
    search_templates = (
        "https://direct-running.fr/recherche?controller=search&s={q}&page={page}",
        "https://direct-running.fr/recherche?controller=search&search_query={q}&page={page}",
    )
    allowed_path_hints = ("/chaussures", "/vetements", "/running", "/trail", "/produit", "/product")


class AlltricksConnector(_PublicRetailBase):
    name = "Alltricks"
    display_name = "Alltricks"
    base_url = "https://www.alltricks.fr"
    # V4.2 : /search?q= renvoyait 403 ; la route réelle du formulaire est
    # /search?s= (200, cartes en <div class="productCard" ...>).
    search_templates = (
        "https://www.alltricks.fr/search?s={q}&page={page}",
        "https://www.alltricks.fr/recherche?s={q}&page={page}",
    )
    allowed_path_hints = ("/running", "/chauss", "/vetement", "/product", "/produit", "/p-")
    # Browser fallback: HTTP 403, Playwright contourne
    browser_search_template = "https://www.alltricks.fr/search?s={q}"
    browser_card_sel = ".productCard_link-wrapper"
    browser_title_sel = "h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 5000


class DeporvillageConnector(_PublicRetailBase):
    name = "Deporvillage"
    display_name = "Deporvillage"
    base_url = "https://www.deporvillage.fr"
    # V4.2 : /search et /recherche renvoyaient 404 ; la vraie route Magento est
    # /catalogsearch/result?q= (200, cartes en <div data-testid="product-card">).
    # Les URLs produits sont des slugs sans préfixe stable : la garde titre+prix+lien
    # du parseur de cartes <div> suffit, pas de hint de chemin ici.
    search_templates = (
        "https://www.deporvillage.fr/catalogsearch/result?q={q}&page={page}",
    )
    allowed_path_hints = ()
    # Browser fallback: HTTP 403, Playwright contourne
    browser_search_template = "https://www.deporvillage.fr/catalogsearch/result?q={q}"
    browser_card_sel = "[class*='card-component-wrapper'], [class*='Card-module']"
    browser_title_sel = "[class*='card-title'], [class*='card-subtitle'], h2, h3"
    browser_price_sel = "[class*='price'], [class*='Price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class RunningPointConnector(_PublicRetailBase):
    name = "Running Point"
    display_name = "Running Point"
    base_url = "https://www.running-point.fr"
    search_templates = (
        "https://www.running-point.fr/search?q={q}&page={page}",
        "https://www.running-point.fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/chauss", "/vetement", "/running", "/product", "/products/")


class HardloopConnector(_PublicRetailBase):
    name = "Hardloop"
    display_name = "Hardloop"
    base_url = "https://www.hardloop.fr"
    search_templates = (
        "https://www.hardloop.fr/search?q={q}&page={page}",
        "https://www.hardloop.fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/produits/", "/chauss", "/vetement", "/running", "/trail")
    # Browser fallback: Next.js SPA, recherche 100% JS via barre de recherche
    browser_search_input_sel = "input[type='search']"
    browser_card_sel = "[class*='productCard']"
    browser_title_sel = "[class*='productName'], [class*='ProductName']"
    browser_price_sel = "[class*='rice']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 8000


class EkosportConnector(_PublicRetailBase):
    name = "Ekosport"
    display_name = "Ekosport"
    base_url = "https://www.ekosport.fr"
    search_templates = (
        "https://www.ekosport.fr/search?q={q}&page={page}",
        "https://www.ekosport.fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/chauss", "/vetement", "/running", "/trail", "/p-")
    # Browser fallback: Angular SPA, Cloudflare protège l'accès
    browser_search_template = "https://www.ekosport.fr/search?q={q}&page=1"
    browser_card_sel = "[class*='product'], [class*='item'], article"
    browser_title_sel = "[class*='name'], [class*='title'], h2, h3, a"
    browser_price_sel = "[class*='rice']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 8000


class CourirConnector(_PublicRetailBase):
    name = "Courir"
    display_name = "Courir"
    base_url = "https://www.courir.com"
    search_templates = (
        "https://www.courir.com/fr/search?q={q}&page={page}",
        "https://www.courir.com/fr/search?cgid=root&q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/p/", "/fr/product", "/chauss", "/sneaker", "/vetement")


class TwentyOneRunConnector(_PublicRetailBase):
    name = "21RUN"
    display_name = "21RUN"
    base_url = "https://21run.com"
    search_templates = (
        "https://21run.com/fr/search?q={q}&page={page}",
        "https://21run.com/fr/catalogsearch/result/?q={q}&p={page}",
    )
    allowed_path_hints = ("/fr/", "/chauss", "/vetement", "/running", "/product")


class MisterRunningConnector(_PublicRetailBase):
    name = "MisterRunning"
    display_name = "MisterRunning"
    base_url = "https://www.misterrunning.com"
    search_templates = (
        "https://www.misterrunning.com/en/search/?q={q}&p={page}",
        "https://www.misterrunning.com/en/search/?term={q}&p={page}",
    )
    allowed_path_hints = ("/running", "/shoes", "/apparel", "/en/")
    # Browser fallback: Clerk.io, recherche JS
    browser_search_template = "https://www.misterrunning.com/en/search/?keywords={q}"
    browser_card_sel = "[class*='inner_item']"
    browser_title_sel = "[class*='name']"
    browser_price_sel = "[class*='rice']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 8000


# ---------------------------------------------------------------------------
# Brand direct stores — Phase 1
# ---------------------------------------------------------------------------

class NikeConnector(_PublicRetailBase):
    name = "Nike"
    display_name = "Nike"
    base_url = "https://www.nike.fr"
    enabled = False  # SPA React: browser finds 0 cards, selectors need real inspection
    search_templates = (
        "https://www.nike.fr/fr/search?q={q}&page={page}",
        "https://www.nike.com/fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/t/", "/product", "/p/")
    browser_search_template = "https://www.nike.fr/fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title']"
    browser_price_sel = "[class*='product-price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 8000


class AdidasConnector(_PublicRetailBase):
    name = "Adidas"
    display_name = "Adidas"
    base_url = "https://www.adidas.fr"
    enabled = False  # SPA React: browser finds 0 cards
    search_templates = (
        "https://www.adidas.fr/search?q={q}&page={page}",
        "https://www.adidas.fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/t/", "/product", "/p/")
    browser_search_template = "https://www.adidas.fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title']"
    browser_price_sel = "[class*='product-price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 8000


class NewBalanceConnector(_PublicRetailBase):
    name = "New Balance Store"
    display_name = "New Balance Store"
    base_url = "https://www.newbalance.fr"
    enabled = False  # SPA React: browser finds 0 cards
    search_templates = (
        "https://www.newbalance.fr/fr/search?q={q}&page={page}",
        "https://www.newbalance.fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/p/")
    browser_search_template = "https://www.newbalance.fr/fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-title']"
    browser_price_sel = "[class*='product-price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class OnRunningConnector(_PublicRetailBase):
    name = "On Store"
    display_name = "On Store"
    base_url = "https://www.on-running.com"
    enabled = False  # SPA React: browser finds 0 cards
    search_templates = (
        "https://www.on-running.com/fr-fr/search?q={q}&page={page}",
        "https://www.on-running.com/fr-fr/search?query={q}&page={page}",
    )
    allowed_path_hints = ("/fr-fr/", "/product", "/p/")
    browser_search_template = "https://www.on-running.com/fr-fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class SalomonStoreConnector(_PublicRetailBase):
    name = "Salomon Store"
    display_name = "Salomon Store"
    base_url = "https://www.salomon.com"
    search_templates = (
        "https://www.salomon.com/fr-fr/search.html?q={q}&page={page}",
        "https://www.salomon.com/fr-fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr-fr/", "/product", "/p/")
    browser_search_template = "https://www.salomon.com/fr-fr/search.html?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class VejaStoreConnector(_PublicRetailBase):
    name = "Veja Store"
    display_name = "Veja Store"
    base_url = "https://www.veja-store.com"
    enabled = False  # SPA React: browser finds 0 cards
    search_templates = (
        "https://www.veja-store.com/fr/recherche?q={q}&page={page}",
        "https://www.veja-store.com/fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/chaussure", "/sneaker")


class PumaConnector(_PublicRetailBase):
    name = "Puma"
    display_name = "Puma"
    base_url = "https://www.puma.com"
    enabled = False  # SPA React: browser finds 0 cards
    search_templates = (
        "https://www.puma.com/fr/fr/search?q={q}&page={page}",
        "https://www.puma.com/fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/fr/", "/product", "/p/")
    browser_search_template = "https://www.puma.com/fr/fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class ConverseConnector(_PublicRetailBase):
    name = "Converse"
    display_name = "Converse"
    base_url = "https://www.converse.fr"
    enabled = False  # SPA React: browser finds 0 cards
    search_templates = (
        "https://www.converse.fr/search?q={q}&page={page}",
        "https://www.converse.fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/chaussure", "/p/")
    browser_search_template = "https://www.converse.fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


# ---------------------------------------------------------------------------
# Multi-brand retailers — Phase 2
# ---------------------------------------------------------------------------

class FootLockerConnector(_PublicRetailBase):
    name = "Foot Locker"
    display_name = "Foot Locker"
    base_url = "https://www.footlocker.fr"
    search_templates = (
        "https://www.footlocker.fr/fr/search?query={q}&page={page}",
        "https://www.footlocker.fr/fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/chaussure", "/p/")
    browser_search_template = "https://www.footlocker.fr/fr/search?query={q}"
    browser_card_sel = "[class*='ProductCard']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='product-card__price'], [class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class SneakersnstuffConnector(_PublicRetailBase):
    name = "Sneakersnstuff"
    display_name = "Sneakersnstuff"
    base_url = "https://sneakersnstuff.com"
    enabled = False  # Browser finds 0 cards
    search_templates = (
        "https://sneakersnstuff.com/fr/search?q={q}&page={page}",
        "https://sneakersnstuff.com/fr/search?q={q}",
    )
    allowed_path_hints = ("/fr/", "/product", "/sneaker")
    browser_search_template = "https://sneakersnstuff.com/fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class EndClothingConnector(_PublicRetailBase):
    name = "End Clothing"
    display_name = "End Clothing"
    base_url = "https://www.endclothing.com"
    enabled = False  # Browser finds 0 cards
    search_templates = (
        "https://www.endclothing.com/fr/search?q={q}&page={page}",
        "https://www.endclothing.com/fr/search?query={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/p/")
    browser_search_template = "https://www.endclothing.com/fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class CettireConnector(_PublicRetailBase):
    name = "Cettire"
    display_name = "Cettire"
    base_url = "https://www.cettire.com"
    enabled = False  # HTTP 403 blocked
    search_templates = (
        "https://www.cettire.com/fr/search?q={q}&page={page}",
        "https://www.cettire.com/fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/p/")


class TheOutnetConnector(_PublicRetailBase):
    name = "The Outnet"
    display_name = "The Outnet"
    base_url = "https://www.theoutnet.com"
    enabled = False  # HTTP 200 but parses 0 cards (JS-rendered)
    search_templates = (
        "https://www.theoutnet.com/fr/fr/search?q={q}&page={page}",
        "https://www.theoutnet.com/fr/fr/search?query={q}&page={page}",
    )
    allowed_path_hints = ("/fr/fr/", "/product", "/p/")


# ---------------------------------------------------------------------------
# French fashion / streetwear — Phase 3
# ---------------------------------------------------------------------------

class RoujeConnector(_PublicRetailBase):
    name = "Rouje"
    display_name = "Rouje"
    base_url = "https://rouje.com"
    enabled = False  # HTTP 200 but parses 0 cards (JS-rendered)
    search_templates = (
        "https://rouje.com/fr/recherche?q={q}&page={page}",
        "https://rouje.com/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/fr/", "/product", "/chaussure")


class RepresentConnector(_PublicRetailBase):
    name = "Represent"
    display_name = "Represent"
    base_url = "https://representclo.com"
    enabled = False  # HTTP 200 but parses 0 cards (JS-rendered)
    search_templates = (
        "https://representclo.com/search?q={q}&page={page}",
        "https://representclo.com/collections/all?q={q}&page={page}",
    )
    allowed_path_hints = ("/collections/", "/product", "/p/")


class KithConnector(_PublicRetailBase):
    name = "Kith"
    display_name = "Kith"
    base_url = "https://kith.com"
    search_templates = (
        "https://kith.com/search?q={q}&page={page}",
        "https://kith.com/collections/all?q={q}&page={page}",
    )
    allowed_path_hints = ("/collections/", "/product", "/p/")
    browser_search_template = "https://kith.com/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class AsphaltgoldConnector(_PublicRetailBase):
    name = "Asphaltgold"
    display_name = "Asphaltgold"
    base_url = "https://www.asphaltgold.com"
    enabled = False  # Browser finds 0 cards
    search_templates = (
        "https://www.asphaltgold.com/search?q={q}&page={page}",
        "https://www.asphaltgold.com/de/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/search", "/product", "/p/")


class BSTNConnector(_PublicRetailBase):
    name = "BSTN"
    display_name = "BSTN"
    base_url = "https://www.bstn.com"
    enabled = False  # Browser finds 0 cards
    search_templates = (
        "https://www.bstn.com/de/search?q={q}&page={page}",
        "https://www.bstn.com/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/de/", "/product", "/p/")
    browser_search_template = "https://www.bstn.com/de/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class EinhalbConnector(_PublicRetailBase):
    name = "43einhalb"
    display_name = "43einhalb"
    base_url = "https://www.43einhalb.com"
    enabled = False  # HTTP 403 blocked
    search_templates = (
        "https://www.43einhalb.com/search?q={q}&page={page}",
        "https://www.43einhalb.com/de/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/search", "/product", "/p/")
    browser_search_template = "https://www.43einhalb.com/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class LacedConnector(_PublicRetailBase):
    name = "Laced"
    display_name = "Laced"
    base_url = "https://www.laced.fr"
    enabled = False  # SSL error
    search_templates = (
        "https://www.laced.fr/search?q={q}&page={page}",
        "https://www.laced.fr/fr/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/search", "/product", "/p/")
    browser_search_template = "https://www.laced.fr/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class GaleriesLafayetteConnector(_PublicRetailBase):
    name = "Galeries Lafayette"
    display_name = "Galeries Lafayette"
    base_url = "https://www.galerieslafayette.com"
    enabled = False  # HTTP 403 blocked
    search_templates = (
        "https://www.galerieslafayette.com/c/search?q={q}&page={page}",
        "https://www.galerieslafayette.com/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/c/", "/product", "/p/")
    browser_search_template = "https://www.galerieslafayette.com/c/search?q={q}"
    browser_card_sel = "[class*='product-card']"
    browser_title_sel = "[class*='product-card__title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 8000


class LaRedouteConnector(_PublicRetailBase):
    name = "La Redoute"
    display_name = "La Redoute"
    base_url = "https://www.laredoute.fr"
    enabled = False  # HTTP 403 blocked
    search_templates = (
        "https://www.laredoute.fr/recherche/pageresults.aspx?SearchType=Keyword&keywords={q}&page={page}",
        "https://www.laredoute.fr/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/pplp/", "/product", "/p/")
    browser_search_template = "https://www.laredoute.fr/recherche/pageresults.aspx?SearchType=Keyword&keywords={q}"
    browser_card_sel = "[class*='product-card'], [class*='productitem']"
    browser_title_sel = "[class*='product-card__title'], [class*='product-title'], h3"
    browser_price_sel = "[class*='price']"
    browser_link_sel = "a"
    browser_image_sel = "img"
    browser_wait_ms = 6000


class BazarChicConnector(_PublicRetailBase):
    name = "BazarChic"
    display_name = "BazarChic"
    base_url = "https://www.bazarchic.com"
    search_templates = (
        "https://www.bazarchic.com/recherche?q={q}&page={page}",
        "https://www.bazarchic.com/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/recherche", "/product", "/p/")


class CocoonCenterConnector(_PublicRetailBase):
    name = "Cocooncenter"
    display_name = "Cocooncenter"
    base_url = "https://www.cocooncenter.com"
    search_templates = (
        "https://www.cocooncenter.com/recherche?q={q}&page={page}",
        "https://www.cocooncenter.com/search?q={q}&page={page}",
    )
    allowed_path_hints = ("/recherche", "/product", "/p/")


class MerlinConnector(_PublicRetailBase):
    name = "Merlin"
    display_name = "Merlin"
    base_url = "https://www.merlin-pc.com"
    search_templates = (
        "https://www.merlin-pc.com/search?q={q}&page={page}",
        "https://www.merlin-pc.com/recherche?q={q}&page={page}",
    )
    allowed_path_hints = ("/search", "/product", "/p/")
