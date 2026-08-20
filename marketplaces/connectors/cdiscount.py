"""Connecteur public Cdiscount pour LUXE RADAR.

Le connecteur utilise uniquement des pages publiques Cdiscount et n'essaie
jamais de contourner CAPTCHA, authentification, 403/429 ou autre contrôle
d'accès. Il combine deux parseurs : JSON-LD lorsqu'il est présent, puis les
ancres produit HTML comme fallback.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import html as html_lib
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.cdiscount.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
    "Accept-Encoding": "identity",
}
IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    or os.environ.get("LUXE_RADAR_ENV", "").lower() == "production"
)
HTTP_TIMEOUT = (2.5, 5) if IS_RENDER else (4, 14)
_MAX_ITEMS = 120
_MAX_ROUTES = 6 if IS_RENDER else 12

_STOPWORDS = {
    "de", "du", "des", "le", "la", "les", "un", "une", "pour", "avec", "et",
    "homme", "femme", "men", "mens", "women", "womens", "taille", "size",
}

_TYPE_ALIASES = {
    "tshirt": ("t shirt", "tshirt", "tee shirt", "teeshirt", "tee", "maillot"),
    "pantalon": ("pantalon", "pantalons", "pants", "trousers", "jogger", "joggers", "jogging"),
    "short": ("short", "shorts", "bermuda"),
    "veste": ("veste", "jacket", "blouson", "manteau", "coupe vent", "windbreaker", "anorak"),
    "sweat": ("sweat", "sweatshirt", "hoodie", "sweat a capuche"),
    "chaussures": ("chaussure", "chaussures", "basket", "baskets", "sneakers", "shoes", "trainers"),
    "ensemble": (
        "ensemble", "ensemble complet", "set", "set complet", "tracksuit", "track suit",
        "survetement", "survêtement", "co ord", "co-ord", "coord", "two piece",
        "two-piece", "2 piece", "2-piece", "matching set", "jogging set",
        "sweat set", "hoodie set", "lot de deux", "haut et bas",
        "two piece set", "2 piece set", "2 pcs", "2pcs", "2 pieces", "2 pièces",
        "ensemble 2 pieces", "ensemble 2 pièces", "set 2 pieces", "set 2 pièces",
        "hoodie and joggers", "hoodie joggers", "hoodie and pants", "hoodie pants",
        "hoodie sweatpants", "sweatshirt and joggers", "sweatshirt joggers",
        "sweat et pantalon", "sweat pantalon", "top and bottom", "top bottom set",
    ),
}


def _safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " ".join(text.split())
    text = re.sub(
        r"\b(?:essantials|essencials|essensials|essentails)\b",
        "essentials",
        text,
    )
    return text


def _dedupe(values):
    out, seen = [], set()
    for value in values:
        key = _norm(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _detect_type(query):
    qn = _norm(query)
    candidates = []
    for type_name, aliases in _TYPE_ALIASES.items():
        for alias in aliases:
            an = _norm(alias)
            candidates.append((len(an), type_name, an))
    candidates.sort(reverse=True)
    for _, type_name, alias in candidates:
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", qn):
            return type_name
    return None


def _query_variants(query):
    query = " ".join(str(query or "").split())
    if not query:
        return []
    variants = [query]
    qn = _norm(query)
    type_name = _detect_type(query)
    tokens = set(qn.split())

    def without_type():
        if not type_name:
            return qn
        aliases = sorted(_TYPE_ALIASES[type_name], key=lambda x: len(_norm(x)), reverse=True)
        base = qn
        for alias in aliases:
            an = _norm(alias)
            if an and re.search(rf"(?<![a-z0-9]){re.escape(an)}(?![a-z0-9])", base):
                base = re.sub(rf"(?<![a-z0-9]){re.escape(an)}(?![a-z0-9])", " ", base)
                break
        return " ".join(base.split())

    base = without_type()
    if type_name == "tshirt":
        # La page large ``Nike Trail`` de Cdiscount contient réellement des
        # tee-shirts Trail ; on l'essaie aussi, puis le filtre global impose le type.
        variants.extend([base, f"{base} t-shirt", f"{base} tee-shirt"])
    elif type_name == "pantalon":
        variants.extend([base, f"pantalon {base}", f"{base} jogging"])
    elif type_name == "veste":
        variants.extend([base, f"veste {base}", f"{base} jacket"])
    elif type_name == "ensemble":
        variants.extend([base, f"{base} ensemble", f"{base} tracksuit", f"{base} survetement"])
    elif type_name:
        variants.append(base)
    elif "nike" in tokens and "trail" in tokens:
        variants.extend(["Nike Trail", "Nike running trail"])

    if "essentials" in tokens:
        if type_name == "ensemble":
            variants.extend([
                "Essentials Fear of God tracksuit",
                "Fear of God Essentials ensemble",
                "Essentials Fear of God",
            ])
        else:
            variants.extend(["Fear of God Essentials", "Essentials Fear of God", "FOG Essentials"])

    return _dedupe(v for v in variants if _norm(v))[:8]


def _slug(query):
    """Slug de recherche Cdiscount.

    Les pages publiques indexées par Cdiscount utilisent ``r-nike%2Btrail.html``
    plutôt que ``r-nike+trail.html``. Le + fait partie du terme de recherche et
    doit donc être encodé en ``%2B``.
    """
    tokens = _norm(query).split()
    return "%2B".join(quote(token, safe="") for token in tokens)


def _candidate_routes(query):
    """Routes publiques Cdiscount, choisies selon l'intention produit."""
    slug = _slug(query)
    type_name = _detect_type(query)

    if type_name == "chaussures":
        roots = ("chaussures", "le-sport", "pret-a-porter")
    elif type_name in {"tshirt", "veste", "sweat", "pantalon", "short", "ensemble"}:
        roots = ("pret-a-porter", "le-sport", "chaussures")
    else:
        roots = ("le-sport", "pret-a-porter", "chaussures")

    urls = [f"{BASE_URL}/{root}/r-{slug}.html" for root in roots]
    # Route de recherche générique historique : utile si une catégorie ne sait
    # pas résoudre le terme. Un 404/403 est simplement ignoré.
    urls.append(f"{BASE_URL}/search/10/{slug}.html")
    return urls[:_MAX_ROUTES]


def _session():
    retry_budget = 0 if IS_RENDER else 1
    retry = Retry(
        total=retry_budget,
        connect=retry_budget,
        read=retry_budget,
        status=retry_budget,
        backoff_factor=0.35,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def _download(url):
    session = _session()
    started = time.perf_counter()
    try:
        response = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        # Ne jamais insister face à un contrôle d'accès/anti-bot.
        if response.status_code in {401, 403, 429}:
            return {"url": response.url, "status": response.status_code, "html": "", "elapsed": time.perf_counter()-started}
        return {
            "url": response.url,
            "status": response.status_code,
            "html": response.text if response.status_code == 200 else "",
            "elapsed": time.perf_counter()-started,
        }
    except requests.RequestException as exc:
        return {"url": url, "status": None, "html": "", "error": str(exc), "elapsed": time.perf_counter()-started}
    finally:
        session.close()


class _CdiscountHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.ldjson = []
        self._anchor = None
        self._script_ld = False
        self._script_chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs}
        if tag.lower() == "a":
            self._anchor = {
                "href": attrs.get("href") or "",
                "title": attrs.get("title") or "",
                "aria": attrs.get("aria-label") or "",
                "text": [],
                "image": None,
                "img_alt": None,
            }
        elif tag.lower() == "img" and self._anchor is not None:
            self._anchor["image"] = (
                attrs.get("src") or attrs.get("data-src") or attrs.get("data-lazy")
                or attrs.get("data-original") or self._anchor.get("image")
            )
            self._anchor["img_alt"] = attrs.get("alt") or self._anchor.get("img_alt")
        elif tag.lower() == "script":
            script_type = str(attrs.get("type") or "").lower()
            if "ld+json" in script_type:
                self._script_ld = True
                self._script_chunks = []

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor["text"].append(data)
        if self._script_ld:
            self._script_chunks.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self._anchor is not None:
            self._anchor["text"] = " ".join(" ".join(self._anchor["text"]).split())
            self.anchors.append(self._anchor)
            self._anchor = None
        elif tag == "script" and self._script_ld:
            payload = "".join(self._script_chunks).strip()
            if payload:
                self.ldjson.append(payload)
            self._script_ld = False
            self._script_chunks = []


def _normalize_url(value):
    value = html_lib.unescape(str(value or "")).strip()
    if not value:
        return None
    url = urljoin(BASE_URL + "/", value)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "cdiscount.com" and not host.endswith(".cdiscount.com"):
        return None
    return url.split("#", 1)[0]


def _is_product_url(url):
    path = (urlparse(url or "").path or "").lower()
    # Ex.: /le-sport/.../tee-shirt-homme.../f-121030403-aacna35988.html
    return bool(re.search(r"/f-[^/]+\.html$", path))


def _parse_number(value):
    value = str(value or "").replace("\xa0", " ").strip().replace(" ", "")
    if not value:
        return None
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")
    return _safe_float(value)


def _extract_price(text):
    text = html_lib.unescape(str(text or "")).replace("\xa0", " ")
    preferred = re.findall(
        r"(?:à\s+partir\s+de|a\s+partir\s+de|prix\s+actuel|maintenant|now)\s*[:\-]?\s*([0-9]{1,6}(?:[.,][0-9]{1,2})?)\s*€",
        text,
        flags=re.IGNORECASE,
    )
    if preferred:
        return _parse_number(preferred[-1])
    all_prices = re.findall(r"([0-9]{1,6}(?:[.,][0-9]{1,2})?)\s*€", text)
    if all_prices:
        # Sur Cdiscount, le prix courant est généralement le dernier prix de la carte.
        return _parse_number(all_prices[-1])
    return None


def _extract_title(anchor):
    for key in ("title", "aria", "img_alt", "text"):
        value = html_lib.unescape(str(anchor.get(key) or ""))
        value = " ".join(value.split())
        if len(value) < 5:
            continue
        value = re.sub(r"^Sponsorisé\s*\?\s*", "", value, flags=re.IGNORECASE)
        # Coupe avant les métadonnées de carte, sans casser "Nike Dri-FIT".
        value = re.split(
            r"\s+(?:Disponible\s+en|Livraison\s+gratuite|Prix\s+de\s+comparaison|Baisse\s+de\s+prix|Prix\s+le\s+\+\s+bas|Couleur\(s\)\s*:|à\s+partir\s+de|a\s+partir\s+de)\b",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" -|,")
        # Un aria-label peut être juste "Voir le produit" : on l'ignore.
        if len(value) >= 8 and _norm(value) not in {"voir le produit", "voir", "ajouter"}:
            return value
    return None


_PRODUCT_HREF_RE = re.compile(
    r'''href\s*=\s*(["'])(?P<href>[^"']*/f-[^"']+?\.html(?:\?[^"']*)?)\1''',
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>", flags=re.DOTALL)


def _strip_html(value):
    value = re.sub(r"<script\b.*?</script>", " ", str(value or ""), flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = _TAG_RE.sub(" ", value)
    return " ".join(html_lib.unescape(value).replace("\xa0", " ").split())


def _window_products(html):
    """Fallback live pour les cartes Cdiscount dont le prix est hors de <a>.

    Les logs réels V2.6 montraient 6 pages HTTP 200 mais 0 ancre exploitable.
    Cdiscount sépare fréquemment le lien/titre du bloc de prix. On découpe donc
    le HTML entre deux liens produit successifs et associe le prix du bloc au
    produit courant, sans appeler d'endpoint privé.
    """
    matches = list(_PRODUCT_HREF_RE.finditer(html or ""))
    out = []
    seen = set()
    for idx, match in enumerate(matches):
        url = _normalize_url(match.group("href"))
        if not url or not _is_product_url(url) or url in seen:
            continue
        seen.add(url)
        start = max(0, (html or "").rfind("<a", max(0, match.start() - 1200), match.start() + 1))
        if start <= 0:
            start = match.start()
        if idx + 1 < len(matches):
            next_pos = matches[idx + 1].start()
            end = min(len(html or ""), next_pos)
        else:
            end = min(len(html or ""), match.end() + 7000)
        block = (html or "")[start:end]
        plain = _strip_html(block)
        price = _extract_price(plain)

        # Priorité aux attributs de l'ancre actuelle, puis au texte avant le prix.
        opening_end = (html or "").find(">", match.end())
        opening = (html or "")[start:opening_end + 1] if opening_end >= 0 else ""
        attrs = {}
        for key in ("title", "aria-label"):
            am = re.search(rf'''\b{re.escape(key)}\s*=\s*(["'])(.*?)\1''', opening, flags=re.IGNORECASE | re.DOTALL)
            if am:
                attrs[key] = html_lib.unescape(am.group(2))
        close = (html or "").find("</a>", opening_end + 1 if opening_end >= 0 else match.end())
        inner = ""
        if close >= 0 and close < end:
            inner = _strip_html((html or "")[opening_end + 1:close])

        title = None
        for candidate in (attrs.get("title"), attrs.get("aria-label"), inner):
            candidate = " ".join(str(candidate or "").split())
            if len(candidate) >= 8 and _norm(candidate) not in {"voir", "voir le produit", "ajouter"}:
                title = re.split(
                    r"\s+(?:Disponible\s+en|Livraison\s+gratuite|Prix\s+de\s+comparaison|à\s+partir\s+de|a\s+partir\s+de)\b",
                    candidate, maxsplit=1, flags=re.IGNORECASE,
                )[0].strip(" -|,")
                break

        if not title:
            # Dernier filet : le début textuel du bloc avant les métadonnées.
            title = re.split(
                r"\s+(?:Couleur\(s\)|Disponible\s+en|Prix\s+de\s+comparaison|à\s+partir\s+de|a\s+partir\s+de)\b",
                plain, maxsplit=1, flags=re.IGNORECASE,
            )[0].strip(" -|,")
            title = re.sub(r"^(?:PUBLICITE|Sponsorisé\??)\s*", "", title, flags=re.IGNORECASE)

        if title and price and price > 0:
            out.append({"url": url, "title": title[:350], "price": price, "image": None})
    return out


def _jsonld_products(payload):
    try:
        data = json.loads(payload)
    except Exception:
        return []
    out = []

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("@type")
        types = set(node_type if isinstance(node_type, list) else [node_type])
        if "Product" in types and node.get("name"):
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("lowPrice") or offers.get("price") if isinstance(offers, dict) else None
            image = node.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            out.append({
                "title": str(node.get("name") or "").strip(),
                "url": node.get("url"),
                "image": image,
                "price": _safe_float(price),
            })
        # ItemList peut contenir des Product sous item/listItem.
        for key in ("itemListElement", "item", "@graph"):
            if key in node:
                walk(node[key])

    walk(data)
    return out


def _score_title(title, query):
    tn = _norm(title)
    qn = _norm(query)
    tokens = [t for t in qn.split() if len(t) >= 2 and t not in _STOPWORDS]
    if not tn or not tokens:
        return 0, False

    if "essentials" in qn.split() and "essentials" in tn.split():
        fog = any(marker in tn for marker in ("fear of god", "fog essentials", "essentials fear of god"))
        concurrents = (
            "adidas", "nike", "reebok", "puma", "asos design", "new balance",
            "under armour", "tommy hilfiger", "calvin klein", "jack jones",
            "jack & jones", "hugo boss", "boss", "ralph lauren", "lacoste",
            "champion", "fila", "ellesse", "abercrombie", "hollister",
        )
        if not fog and any(brand in tn for brand in concurrents):
            return 0, False

    type_name = _detect_type(query)
    if type_name:
        type_tokens = set()
        for alias in _TYPE_ALIASES[type_name]:
            type_tokens.update(_norm(alias).split())
        important = [t for t in tokens if t not in type_tokens]
        if type_name == "ensemble":
            non_mode = (
                "skincare", "skin care", "beauty", "brush", "makeup", "cosmetic",
                "shampoo", "conditioner", "hair care", "haircare", "body wash",
                "shower", "fragrance", "perfume", "parfum", "cologne", "nail",
                "gift set", "toiletry", "serum", "cleanser", "moisturizer", "cream",
            )
            mode = (
                "hoodie", "sweat", "sweatshirt", "crewneck", "jacket", "veste",
                "top", "tee", "t shirt", "shirt", "pantalon", "pants", "trousers",
                "sweatpants", "jogger", "joggers", "short", "shorts", "leggings",
                "skirt", "dress", "pyjama", "pajama", "loungewear", "tracksuit",
                "survetement", "co ord", "coord", "activewear", "sportswear",
            )
            fortes = (
                "tracksuit", "track suit", "survetement", "co ord", "coord",
                "matching set", "jogging set", "sweat set", "hoodie set",
                "hoodie and joggers", "hoodie joggers", "hoodie and pants",
                "hoodie pants", "hoodie sweatpants", "sweatshirt and joggers",
                "sweatshirt joggers", "sweat et pantalon", "sweat pantalon",
                "top and bottom", "top bottom set",
            )
            hauts = ("hoodie", "sweat", "sweatshirt", "crewneck", "jacket", "veste", "top", "tee", "t shirt", "shirt")
            bas = ("pants", "pantalon", "sweatpant", "sweatpants", "jogger", "joggers", "short", "shorts", "leggings", "skirt")
            deux_pieces = any(x in tn for x in hauts) and any(x in tn for x in bas)
            ambigus = ("ensemble", "set", "set complet", "two piece", "2 piece", "2pcs", "2 pcs", "lot de deux")
            type_ok = (
                any(x in tn for x in fortes)
                or deux_pieces
                or (
                    any(x in tn for x in ambigus)
                    and any(x in tn for x in mode)
                    and not any(x in tn for x in non_mode)
                )
            )
        else:
            type_ok = any(
                re.search(rf"(?<![a-z0-9]){re.escape(_norm(alias))}(?![a-z0-9])", tn)
                for alias in _TYPE_ALIASES[type_name]
            )
    else:
        important = tokens
        type_ok = True

    def token_present(token):
        if token == "essentials":
            return any(marker in tn for marker in ("essentials", "fear of god", "fog essentials", "fog"))
        return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", tn) is not None

    present = sum(1 for token in important if token_present(token))
    coverage = present / max(len(important), 1)
    strong = type_ok and present == len(important)
    score = int(round(coverage * 75)) + (15 if type_ok else 0) + (10 if strong else 0)
    return min(score, 100), strong


def _parse_page(html, query, price_max):
    parser = _CdiscountHTMLParser()
    try:
        parser.feed(html or "")
    except Exception:
        pass

    candidates = []
    jsonld_count = 0
    anchor_count = 0

    for payload in parser.ldjson:
        for product in _jsonld_products(payload):
            url = _normalize_url(product.get("url"))
            price = _safe_float(product.get("price"))
            title = str(product.get("title") or "").strip()
            if not url or not _is_product_url(url) or price is None or price <= 0 or not title:
                continue
            jsonld_count += 1
            score, strong = _score_title(title, query)
            candidates.append((url, title, price, product.get("image"), score, strong, "jsonld"))

    for anchor in parser.anchors:
        url = _normalize_url(anchor.get("href"))
        if not url or not _is_product_url(url):
            continue
        title = _extract_title(anchor)
        price = _extract_price(anchor.get("text") or anchor.get("aria") or anchor.get("title"))
        if not title or price is None or price <= 0:
            continue
        anchor_count += 1
        score, strong = _score_title(title, query)
        candidates.append((url, title, price, anchor.get("image"), score, strong, "html"))

    window_count = 0
    for product in _window_products(html):
        url = _normalize_url(product.get("url"))
        price = _safe_float(product.get("price"))
        title = str(product.get("title") or "").strip()
        if not url or price is None or price <= 0 or not title:
            continue
        window_count += 1
        score, strong = _score_title(title, query)
        candidates.append((url, title, price, product.get("image"), score, strong, "html-window"))

    results = []
    seen = set()
    stats = {
        "jsonld": jsonld_count, "ancres": anchor_count, "blocs": window_count,
        "doublons": 0, "hors_budget": 0
    }
    for url, title, price, image, score, strong, source in sorted(
        candidates,
        key=lambda row: (0 if row[5] else 1, -row[4], row[2], row[1]),
    ):
        key = url.lower()
        if key in seen:
            stats["doublons"] += 1
            continue
        seen.add(key)
        if price_max is not None and price > price_max:
            stats["hors_budget"] += 1
            continue
        image_url = str(image).strip() if image else None
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url
        elif image_url and image_url.startswith("/"):
            image_url = urljoin(BASE_URL, image_url)
        results.append({
            "marketplace": "Cdiscount",
            "titre": title,
            "prix": round(price, 2),
            "devise": "EUR",
            "lien": url,
            "image": image_url,
            "modele": None,
            "reference": None,
            "vendor": "Cdiscount",
            "disponible": True,
            "categorie": "A VERIFIER",
            "score": 76,
            "score_match": max(65, score),
            "score_confiance": 70,
            "score_affaire": 55,
            "site_relevance": score,
            "match_requete_fort": strong,
            "alertes": [],
            "raisons": [
                "Données produit récupérées depuis une page publique Cdiscount",
                f"Parseur Cdiscount : {source}",
            ],
        })
    return results, stats


class CdiscountConnector(MarketplaceConnector):
    name = "Cdiscount"
    display_name = "Cdiscount"
    enabled = True
    base_url = BASE_URL
    currency = "EUR"

    supports_pagination = False
    expansion_page_size = 40
    expansion_recall_cap = 40
    max_pages = 1
    cooldown_seconds = 0.4

    def search(self, query, price_max=None, limit=20):
        query = " ".join(str(query or "").split())
        if not query:
            return []
        limit = max(1, min(_safe_int(limit, 20), _MAX_ITEMS))
        price_max = _safe_float(price_max) if price_max is not None else None
        if price_max is not None and price_max <= 0:
            return []

        variants = _query_variants(query)
        # On garde peu de routes : Cdiscount est une source progressive, pas un crawler massif.
        # Round-robin entre les variantes : on préfère essayer la meilleure
        # route de chaque formulation avant de creuser une seule formulation.
        # Pour "t shirt Nike Trail", cela teste donc réellement t-shirt ET
        # tee-shirt, au lieu de consommer tout le budget réseau sur la 1re requête.
        route_lists = [_candidate_routes(variant) for variant in variants]
        urls = []
        seen_urls = set()
        max_depth = max((len(routes) for routes in route_lists), default=0)
        for depth in range(max_depth):
            for routes in route_lists:
                if depth >= len(routes):
                    continue
                url = routes[depth]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                urls.append(url)
                if len(urls) >= _MAX_ROUTES:
                    break
            if len(urls) >= _MAX_ROUTES:
                break

        print(f"[Cdiscount] Recherche : {query}")
        print(f"[Cdiscount][DIAG] variantes: {', '.join(variants)} | routes={len(urls)}")

        pages = []
        with ThreadPoolExecutor(max_workers=min(len(urls), 2 if IS_RENDER else 4)) as executor:
            futures = {executor.submit(_download, url): url for url in urls}
            for future in as_completed(futures):
                info = future.result()
                if info.get("status") != 200:
                    detail = info.get("error") or f"HTTP {info.get('status')}"
                    print(f"[Cdiscount] route ignorée : {detail}")
                pages.append(info)

        all_results = []
        aggregate = {"pages_ok": 0, "jsonld": 0, "ancres": 0, "blocs": 0, "doublons": 0, "hors_budget": 0}
        for page in pages:
            html = page.get("html") or ""
            if not html:
                continue
            aggregate["pages_ok"] += 1
            results, stats = _parse_page(html, query=query, price_max=price_max)
            all_results.extend(results)
            for key in ("jsonld", "ancres", "blocs", "doublons", "hors_budget"):
                aggregate[key] += stats.get(key, 0)

        # Déduplication inter-route + priorité stricte.
        uniques = []
        seen = set()
        for item in sorted(
            all_results,
            key=lambda item: (
                0 if item.get("match_requete_fort") else 1,
                -_safe_int(item.get("site_relevance"), 0),
                _safe_float(item.get("prix"), 999999),
            ),
        ):
            key = str(item.get("lien") or "").lower()
            if not key or key in seen:
                aggregate["doublons"] += 1
                continue
            seen.add(key)
            uniques.append(item)

        retained = uniques[:limit]
        strong = sum(1 for item in retained if item.get("match_requete_fort"))
        print(
            "[Cdiscount][DIAG] "
            f"pages_ok={aggregate['pages_ok']} | jsonld={aggregate['jsonld']} | "
            f"ancres={aggregate['ancres']} | blocs={aggregate['blocs']} | hors_budget={aggregate['hors_budget']} | "
            f"doublons={aggregate['doublons']}"
        )
        print(f"[Cdiscount] {len(retained)} resultats retenus ({strong} correspondance(s) forte(s))")
        if strong:
            preview = " | ".join(
                f"{item.get('titre')} @ {item.get('prix')}€"
                for item in retained if item.get("match_requete_fort")
            )[:900]
            print(f"[Cdiscount][FORTS] {preview}")
        elif retained:
            preview = " | ".join(
                f"{item.get('titre')} [{item.get('site_relevance', 0)}]"
                for item in retained[:8]
            )[:1200]
            print(f"[Cdiscount][SAMPLE] {preview}")

        # --- Fallback navigateur si HTTP ne donne rien (Cloudflare) ---
        if not retained:
            try:
                from marketplaces.connectors.browser_fallback import search_via_browser, browser_available
                if browser_available():
                    self.browser_search_template = "https://www.cdiscount.com/search/10/{q}.html"
                    self.browser_card_sel = "article, [class*='product']"
                    self.browser_title_sel = "[class*='name'], [class*='title'], h2, h3, a"
                    self.browser_price_sel = "[class*='rice']"
                    self.browser_link_sel = "a"
                    self.browser_image_sel = "img"
                    self.browser_wait_ms = 8000
                    browser_results = search_via_browser(self, query, price_max, limit)
                    if browser_results:
                        print(f"[Cdiscount] Fallback navigateur: {len(browser_results)} resultats")
                        return browser_results
            except Exception as _bf_err:
                print(f"[Cdiscount] fallback navigateur echoue: {_bf_err}")

        return retained
