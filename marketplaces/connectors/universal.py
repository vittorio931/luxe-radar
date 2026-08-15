from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests

from .base import MarketplaceConnector

CONFIG_PATH = Path(__file__).resolve().parents[1] / "sites.json"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _norm(value):
    text = "" if value is None else str(value)
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value):
    return re.findall(r"[a-z0-9]+", _norm(value))


def _query_match(title, query):
    title_tokens = set(_tokens(title))
    query_tokens = [t for t in _tokens(query) if t not in {"de", "du", "des", "le", "la", "les", "a", "the", "for", "with"}]
    return bool(title_tokens) and all(t in title_tokens for t in query_tokens)


def _safe_float(value, default=None):
    try:
        if isinstance(value, str):
            value = value.replace("\xa0", " ").replace(" ", "").replace(",", ".")
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if not match:
                return default
            value = match.group(0)
        return float(value)
    except (TypeError, ValueError):
        return default


def _path_get(obj, path, default=None):
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return default
        elif isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            return default
    return cur


def _load_config():
    if not CONFIG_PATH.exists():
        return {"version": 1, "sites": []}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[CONFIG] sites.json invalide: {exc}")
        return {"version": 1, "sites": []}
    if not isinstance(data, dict):
        return {"version": 1, "sites": []}
    data.setdefault("sites", [])
    return data


class ConfiguredSiteConnector(MarketplaceConnector):
    def __init__(self, config):
        self.config = dict(config)
        self.name = str(config.get("name") or "Site configuré")
        self.display_name = str(config.get("display_name") or self.name)
        self.enabled = bool(config.get("enabled", False))
        self.currency = str(config.get("currency") or "EUR").upper()
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        self.mode = str(config.get("mode") or "shopify").lower()
        self.timeout = max(3, int(config.get("timeout", 12)))
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        extra_headers = config.get("headers") or {}
        if isinstance(extra_headers, dict):
            self.session.headers.update({str(k): str(v) for k, v in extra_headers.items()})

    def _to_eur(self, price):
        price = _safe_float(price)
        if price is None:
            return None
        divisor = _safe_float(self.config.get("price_divisor"), 1.0) or 1.0
        price = price / divisor
        if self.currency == "EUR":
            return round(price, 2)
        fx = _safe_float(self.config.get("fx_rate_to_eur"))
        if fx is None or fx <= 0:
            return None
        return round(price * fx, 2)

    def _normalize_item(self, title, price, url, image=None, description=None, extra=None):
        price_eur = self._to_eur(price)
        if not title or price_eur is None or not url:
            return None
        item = {
            "marketplace": self.name,
            "titre": " ".join(str(title).split()),
            "prix": price_eur,
            "devise": "EUR",
            "devise_originale": self.currency,
            "lien": urljoin(self.base_url + "/", str(url)),
            "image": urljoin(self.base_url + "/", str(image)) if image else None,
            "description": description or "",
            "modele": None,
            "categorie": "A VERIFIER",
            "score": 60,
            "score_match": 80,
            "score_confiance": int(self.config.get("confidence", 55)),
            "score_affaire": 50,
            "alertes": ["Résultat provenant d'un connecteur configurable : vérification recommandée"],
            "raisons": [f"Source publique {self.name}"],
        }
        if extra and isinstance(extra, dict):
            item.update(extra)
        return item

    def _shopify_suggest(self, query, limit):
        endpoint = f"{self.base_url}/search/suggest.json"
        params = {
            "q": query,
            "resources[type]": "product",
            "resources[limit]": min(max(limit * 2, 10), 50),
        }
        try:
            response = self.session.get(endpoint, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return []
        candidates = []
        possible = [
            _path_get(data, "resources.results.products", []),
            _path_get(data, "resources.results", []),
            data.get("products", []) if isinstance(data, dict) else [],
        ]
        for value in possible:
            if isinstance(value, list):
                candidates.extend(x for x in value if isinstance(x, dict))
        return candidates

    def _shopify_handles_from_search(self, query, limit):
        url = f"{self.base_url}/search?q={quote(query)}&type=product"
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            html = response.text
        except Exception:
            return []
        handles = []
        for match in re.finditer(r"href=[\"']([^\"']*/products/([^?\"'#]+))", html, flags=re.I):
            handle = match.group(2).strip("/")
            if handle and handle not in handles:
                handles.append(handle)
            if len(handles) >= max(limit * 3, 30):
                break
        return handles

    def _shopify_product_json(self, handle):
        if not handle:
            return None
        url = f"{self.base_url}/products/{handle}.js"
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _search_shopify(self, query, price_max, limit):
        results = []
        seen = set()
        candidates = self._shopify_suggest(query, limit)

        # Les suggestions peuvent déjà contenir les données nécessaires.
        for p in candidates:
            title = p.get("title") or p.get("text")
            url = p.get("url") or p.get("handle")
            if url and not str(url).startswith("/") and "/products/" not in str(url):
                url = f"/products/{url}"
            price = p.get("price")
            image = p.get("image") or p.get("featured_image")
            if isinstance(image, dict):
                image = image.get("url") or image.get("src")
            item = self._normalize_item(title, price, url, image=image)
            if item and _query_match(item["titre"], query):
                if price_max is None or item["prix"] <= price_max:
                    key = item["lien"]
                    if key not in seen:
                        seen.add(key)
                        results.append(item)
            if len(results) >= limit:
                return results[:limit]

        # Fallback robuste : découvre les handles puis interroge product.js.
        handles = self._shopify_handles_from_search(query, limit)
        for handle in handles:
            product = self._shopify_product_json(handle)
            if not product:
                continue
            title = product.get("title") or ""
            if not _query_match(title, query):
                continue
            variants = product.get("variants") or []
            prices = []
            for variant in variants:
                if isinstance(variant, dict):
                    value = _safe_float(variant.get("price"))
                    if value is not None:
                        prices.append(value)
            if not prices:
                value = _safe_float(product.get("price"))
                if value is not None:
                    prices.append(value)
            if not prices:
                continue
            price = min(prices)
            images = product.get("images") or []
            image = images[0] if images else product.get("featured_image")
            item = self._normalize_item(
                title,
                price,
                f"/products/{handle}",
                image=image,
                description=product.get("description") or "",
            )
            if not item:
                continue
            if price_max is not None and item["prix"] > price_max:
                continue
            if item["lien"] in seen:
                continue
            seen.add(item["lien"])
            results.append(item)
            if len(results) >= limit:
                break
        return results[:limit]

    def _search_json(self, query, price_max, limit):
        search_url = str(self.config.get("search_url") or "")
        if not search_url:
            return []
        url = search_url.format(
            query=quote(query),
            query_raw=query,
            limit=limit,
        )
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"[{self.name}] JSON indisponible: {exc}")
            return []
        items = _path_get(data, self.config.get("items_path") or "", data)
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            return []
        out = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            title = _path_get(raw, self.config.get("title_path") or "title")
            if not _query_match(title, query):
                continue
            price = _path_get(raw, self.config.get("price_path") or "price")
            link = _path_get(raw, self.config.get("url_path") or "url")
            image = _path_get(raw, self.config.get("image_path") or "image")
            item = self._normalize_item(title, price, link, image=image)
            if not item:
                continue
            if price_max is not None and item["prix"] > price_max:
                continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def _search_playwright(self, query, price_max, limit):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            print(f"[{self.name}] Playwright n'est pas installé")
            return []

        search_url = str(self.config.get("search_url") or "")
        selectors = self.config.get("selectors") or {}
        card_sel = selectors.get("card")
        title_sel = selectors.get("title")
        price_sel = selectors.get("price")
        link_sel = selectors.get("link")
        image_sel = selectors.get("image")
        if not search_url or not card_sel or not title_sel or not price_sel:
            print(f"[{self.name}] Configuration Playwright incomplète")
            return []
        url = search_url.format(query=quote(query), query_raw=query, limit=limit)
        results = []
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(headless=bool(self.config.get("headless", True)))
                page = browser.new_page(viewport={"width": 1360, "height": 900})
                page.goto(url, wait_until="domcontentloaded", timeout=int(self.config.get("navigation_timeout", 30000)))
                page.wait_for_timeout(int(self.config.get("wait_ms", 1800)))
                cards = page.locator(card_sel)
                count = min(cards.count(), max(limit * 4, 40))
                for i in range(count):
                    card = cards.nth(i)
                    try:
                        title = card.locator(title_sel).first.inner_text(timeout=1000).strip()
                        if not _query_match(title, query):
                            continue
                        price_text = card.locator(price_sel).first.inner_text(timeout=1000)
                        price = _safe_float(price_text)
                        link = None
                        if link_sel:
                            el = card.locator(link_sel).first
                            link = el.get_attribute("href")
                        else:
                            link = card.locator("a").first.get_attribute("href")
                        image = None
                        if image_sel:
                            img = card.locator(image_sel).first
                            image = img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("srcset")
                            if image and "," in image:
                                image = image.split(",")[0].strip().split()[0]
                        item = self._normalize_item(title, price, link, image=image)
                        if not item:
                            continue
                        if price_max is not None and item["prix"] > price_max:
                            continue
                        results.append(item)
                        if len(results) >= limit:
                            break
                    except Exception:
                        continue
            except Exception as exc:
                print(f"[{self.name}] Erreur Playwright: {exc}")
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
        return results[:limit]

    def search(self, query, price_max=None, limit=20):
        query = str(query or "").strip()
        if not query or not self.enabled or not self.base_url:
            return []
        try:
            limit = max(1, min(int(limit), 100))
        except Exception:
            limit = 20
        try:
            price_max = float(price_max) if price_max is not None else None
        except Exception:
            price_max = None

        print(f"[{self.name}] Recherche configurable ({self.mode}) : {query}")
        if self.mode == "shopify":
            results = self._search_shopify(query, price_max, limit)
        elif self.mode in {"json", "api", "json_api"}:
            results = self._search_json(query, price_max, limit)
        elif self.mode in {"playwright", "html", "browser"}:
            results = self._search_playwright(query, price_max, limit)
        else:
            print(f"[{self.name}] Mode inconnu : {self.mode}")
            return []
        print(f"[{self.name}] {len(results)} résultats retenus")
        return results[:limit]


def load_configured_connectors():
    data = _load_config()
    connectors = []
    for site in data.get("sites", []):
        if not isinstance(site, dict):
            continue
        connector_type = str(site.get("connector_type") or site.get("mode") or "").lower()
        if connector_type == "dedicated":
            continue
        try:
            connectors.append(ConfiguredSiteConnector(site))
        except Exception as exc:
            print(f"[CONFIG] Site ignoré ({site.get('name')}): {exc}")
    return connectors

# ---------------------------------------------------------------------------
# V2.9.0 - discovery public en vagues
# ---------------------------------------------------------------------------

def load_discovery_site_configs():
    """Retourne les sites catalogués pouvant être sondés sans contrôle d'accès.

    Les sites explicitement marqués ``blocked`` ne sont jamais recontactés par
    ce mécanisme. Le but est d'élargir progressivement la couverture sans
    déclencher des centaines de requêtes en parallèle.
    """
    data = _load_config()
    out = []
    seen = set()
    for raw in data.get("sites", []):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "").lower()
        ctype = str(raw.get("connector_type") or raw.get("mode") or "").lower()
        if status == "blocked" or ctype == "dedicated" or raw.get("enabled"):
            continue
        base_url = str(raw.get("base_url") or raw.get("url") or "").strip().rstrip("/")
        name = str(raw.get("name") or "").strip()
        if not name or not base_url:
            continue
        domain = urlparse(base_url).netloc.casefold()
        if not domain or domain in seen:
            continue
        seen.add(domain)
        # Les Shopify connus utilisent leur connecteur configurable, les autres
        # passent par le lecteur JSON-LD générique.
        out.append(dict(raw))
    return out


def _jsonld_products(value):
    found = []
    if isinstance(value, list):
        for item in value:
            found.extend(_jsonld_products(item))
        return found
    if not isinstance(value, dict):
        return found
    type_value = value.get("@type")
    types = type_value if isinstance(type_value, list) else [type_value]
    if any(str(t).casefold() in {"product", "productgroup"} for t in types if t):
        found.append(value)
    for key in ("@graph", "itemListElement", "mainEntity", "item", "items"):
        if key in value:
            found.extend(_jsonld_products(value.get(key)))
    return found


def _first_offer(product):
    offers = product.get("offers") if isinstance(product, dict) else None
    if isinstance(offers, list):
        offers = next((x for x in offers if isinstance(x, dict)), None)
    return offers if isinstance(offers, dict) else {}


def _generic_jsonld_site_search(config, query, price_max=None, limit=6):
    """Sonde une page de recherche publique et lit uniquement son JSON-LD.

    Aucun CAPTCHA, login ou endpoint privé n'est contourné. Si la page ne
    fournit pas de données produit structurées, le site renvoie simplement 0.
    """
    name = str(config.get("name") or "Site")
    base_url = str(config.get("base_url") or config.get("url") or "").rstrip("/")
    if not base_url:
        return []
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    paths = (
        f"/search?q={quote(query)}",
        f"/search?query={quote(query)}",
        f"/?s={quote(query)}&post_type=product",
    )
    html = None
    final_url = None
    for path in paths:
        try:
            r = session.get(urljoin(base_url + "/", path.lstrip("/")), timeout=(2.0, 3.5), allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code != 200 or not r.text:
            continue
        # Évite les pages de challenge évidentes : on ne tente rien d'autre.
        sample = _norm(r.text[:8000])
        if any(marker in sample for marker in ("captcha", "verify you are human", "access denied", "cloudflare challenge")):
            break
        html = r.text
        final_url = r.url
        break
    if not html:
        return []

    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    )
    products = []
    for raw in scripts:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        products.extend(_jsonld_products(data))

    out = []
    seen = set()
    for product in products:
        title = str(product.get("name") or "").strip()
        if not title or not _query_match(title, query):
            continue
        offer = _first_offer(product)
        price = _safe_float(offer.get("price") or offer.get("lowPrice") or product.get("price"))
        currency = str(offer.get("priceCurrency") or config.get("currency") or "EUR").upper()
        if price is None:
            continue
        if currency != "EUR":
            fx = _safe_float(config.get("fx_rate_to_eur"))
            if not fx or fx <= 0:
                continue
            price_eur = round(price * fx, 2)
        else:
            price_eur = round(price, 2)
        if price_max is not None and price_eur > float(price_max):
            continue
        link = product.get("url") or offer.get("url") or final_url
        image = product.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        link = urljoin(base_url + "/", str(link or ""))
        if not link or link in seen:
            continue
        seen.add(link)
        out.append({
            "marketplace": name,
            "plateforme": name,
            "titre": title,
            "title": title,
            "prix": price_eur,
            "price": price_eur,
            "devise": "EUR",
            "devise_originale": currency,
            "lien": link,
            "url": link,
            "image": urljoin(base_url + "/", str(image)) if image else None,
            "categorie": "A VERIFIER",
            "score": 55,
            "score_match": 65,
            "score_confiance": 45,
            "score_affaire": 50,
            "alertes": ["Source catalogue public : vérification recommandée"],
            "raisons": [f"Produit structuré public trouvé sur {name}"],
        })
        if len(out) >= max(1, int(limit)):
            break
    return out


def discover_catalog_wave(query, price_max=None, cursor=0, site_limit=8, per_site_limit=6):
    """Explore une petite vague du catalogue massif, jamais les 1000 sites à la fois."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    sites = load_discovery_site_configs()
    try:
        cursor = max(0, int(cursor))
    except Exception:
        cursor = 0
    site_limit = max(1, min(int(site_limit or 8), 16))
    batch = sites[cursor:cursor + site_limit]
    next_cursor = cursor + len(batch)
    if not batch:
        return [], next_cursor, False, 0

    def run(site):
        ctype = str(site.get("connector_type") or site.get("mode") or "").lower()
        try:
            if ctype == "shopify":
                cfg = dict(site)
                cfg["enabled"] = True
                cfg.setdefault("mode", "shopify")
                connector = ConfiguredSiteConnector(cfg)
                return connector.search(query=query, price_max=price_max, limit=per_site_limit)
            return _generic_jsonld_site_search(site, query, price_max=price_max, limit=per_site_limit)
        except Exception:
            return []

    results = []
    workers = min(4, len(batch))
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="luxe-discovery") as pool:
        futures = [pool.submit(run, site) for site in batch]
        for future in as_completed(futures):
            try:
                results.extend(future.result() or [])
            except Exception:
                pass
    return results, next_cursor, next_cursor < len(sites), len(batch)
