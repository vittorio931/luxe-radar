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
import unicodedata
from urllib.parse import quote, quote_plus, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    or os.environ.get("LUXE_RADAR_ENV", "").lower() == "production"
)
HTTP_TIMEOUT = (2.5 if IS_RENDER else 4.0, 5.5 if IS_RENDER else 10.0)
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


class _PublicRetailBase(MarketplaceConnector):
    name = "Marketplace"  # ignoré par l'auto-loader
    display_name = "Marketplace"
    enabled = True
    currency = "EUR"
    base_url = ""
    search_template = ""
    allowed_path_hints = ()

    def build_search_url(self, query, page=1):
        return self.search_template.format(
            q=quote_plus(str(query or "").strip()),
            slug=quote(str(query or "").strip().replace(" ", "-"), safe="-"),
            page=max(1, int(page or 1)),
        )

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

        url = self.build_search_url(query, page=page)
        print(f"[{self.name}] Recherche publique : {query} | page={page}")
        session = _session()
        try:
            response = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
            if response.status_code != 200:
                print(f"[{self.name}] HTTP {response.status_code} -> ignoré")
                return []
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return []
            text = response.text or ""
            # Contrôles d'accès explicites : aucun contournement.
            low = _norm(text[:12000])
            if any(marker in low for marker in ("captcha", "access denied", "verify you are human", "cf challenge")):
                print(f"[{self.name}] Contrôle d'accès détecté -> route ignorée")
                return []
            raw = parse_jsonld_products(text, self.base_url)
            if not raw:
                raw = parse_html_cards(text, self.base_url, self.allowed_path_hints)
            results = []
            seen = set()
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
            print(f"[{self.name}] {len(results)} resultats retenus")
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
    search_template = "https://www.footshop.eu/en/search?q={q}&page={page}"
    allowed_path_hints = ("/en/", "/product", "/sneakers", "/shoes")


class JDSportsConnector(_PublicRetailBase):
    name = "JD Sports"
    display_name = "JD Sports"
    base_url = "https://www.jdsports.fr"
    search_template = "https://www.jdsports.fr/search/{slug}/?page={page}"
    allowed_path_hints = ("/product/", "/produit/", "/p/")
