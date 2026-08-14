from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
import unicodedata
from urllib.parse import quote, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.1688.com"
SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm"
FALLBACK_CNY_EUR = 0.12
RATE_TTL = 60 * 60 * 6
_RATE = {"value": FALLBACK_CNY_EUR, "at": 0.0}
_RATE_LOCK = threading.Lock()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "identity",
}

TYPE_ZH = {
    "ensemble": ("套装", "两件套", "卫衣 长裤 套装"),
    "tshirt": ("T恤", "短袖"),
    "sweat": ("卫衣", "连帽卫衣"),
    "pantalon": ("裤子", "长裤", "运动裤"),
    "short": ("短裤",),
    "veste": ("外套", "夹克"),
    "chaussures": ("鞋", "运动鞋"),
}

TYPE_ALIASES = {
    "ensemble": ("ensemble", "set", "tracksuit", "survetement", "survêtement", "2 piece", "2pcs", "matching set"),
    "tshirt": ("t shirt", "t-shirt", "tshirt", "tee", "tee shirt"),
    "sweat": ("hoodie", "sweat", "sweatshirt"),
    "pantalon": ("pantalon", "pants", "jogger", "joggers", "sweatpants"),
    "short": ("short", "shorts"),
    "veste": ("veste", "jacket", "coat", "windbreaker"),
    "chaussures": ("chaussure", "chaussures", "shoe", "shoes", "sneaker", "sneakers", "trainer", "trainers"),
}


def _norm(value):
    text = "" if value is None else str(value)
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe(values):
    out, seen = [], set()
    for value in values:
        key = str(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _session():
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount("https://", adapter)
    return session


def _refresh_rate():
    try:
        response = requests.get(
            "https://api.frankfurter.dev/v2/rate/CNY/EUR",
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=4,
        )
        response.raise_for_status()
        data = response.json()
        value = _safe_float(data.get("rate"))
        if value and 0.05 < value < 0.30:
            with _RATE_LOCK:
                _RATE.update(value=value, at=time.time())
            print(f"[1688] Taux CNY->EUR actualisé : {value:.5f}")
    except Exception:
        return


def cny_eur_rate():
    with _RATE_LOCK:
        value, age = _RATE["value"], time.time() - _RATE["at"]
    if age > RATE_TTL:
        threading.Thread(target=_refresh_rate, daemon=True).start()
    return value


def _detect_type(query):
    q = _norm(query)
    for kind, aliases in TYPE_ALIASES.items():
        if any(re.search(r"(?<![a-z0-9])" + re.escape(_norm(a)) + r"(?![a-z0-9])", q) for a in aliases):
            return kind
    return None


def _query_variants(query):
    raw = " ".join(str(query or "").split())
    q = re.sub(r"\bessantials\b", "Essentials", raw, flags=re.I)
    kind = _detect_type(q)
    variants = [q]
    if kind:
        base = q
        for alias in TYPE_ALIASES[kind]:
            base = re.sub(r"(?i)(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", " ", base)
        base = " ".join(base.split()) or q
        variants.extend(f"{base} {term}" for term in TYPE_ZH[kind])
    if "essentials" in q.lower():
        variants.append(re.sub(r"(?i)essentials", "Fear of God Essentials", q))
    return _dedupe(variants)[:5]


def _price_min(value):
    if value is None:
        return None
    vals = []
    for token in re.findall(r"\d+(?:[.,]\d+)?", str(value)):
        parsed = _safe_float(token.replace(",", "."))
        if parsed and 0 < parsed < 1_000_000:
            vals.append(parsed)
    return min(vals) if vals else None


def _clean_text(value):
    if value is None:
        return ""
    value = html_lib.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\\u002F", "/").replace("\\/", "/")
    try:
        if "\\u" in value:
            value = bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        pass
    return " ".join(value.split()).strip()


def _walk_json(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json(value)


def _candidate_from_dict(data):
    title = next((data.get(k) for k in ("title", "subject", "offerTitle", "name") if data.get(k)), None)
    price = next((data.get(k) for k in ("price", "offerPrice", "tradePrice", "priceDisplay", "priceInfo") if data.get(k) is not None), None)
    url = next((data.get(k) for k in ("detailUrl", "offerUrl", "url", "detailURL") if data.get(k)), None)
    offer_id = data.get("offerId") or data.get("offerid") or data.get("id")
    if not url and offer_id and str(offer_id).isdigit():
        url = f"https://detail.1688.com/offer/{offer_id}.html"
    if not title or not url:
        return None
    p = _price_min(price)
    if p is None:
        return None
    image = next((data.get(k) for k in ("imageUrl", "image", "imgUrl", "picUrl", "mainImage") if data.get(k)), "")
    if isinstance(image, dict):
        image = image.get("url") or image.get("src") or ""
    seller = data.get("companyName") or data.get("sellerName") or data.get("shopName") or ""
    moq = data.get("minOrderQuantity") or data.get("moq") or data.get("beginAmount")
    return {
        "title": _clean_text(title),
        "price_cny": p,
        "url": urljoin(BASE_URL, _clean_text(url)),
        "image": _clean_text(image),
        "seller": _clean_text(seller),
        "moq": moq,
    }


def _extract_json_candidates(html_text):
    out = []
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html_text or "", flags=re.I | re.S)
    for raw in scripts:
        raw = html_lib.unescape(raw).strip()
        if not raw or raw[0] not in "[{":
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in _walk_json(data):
            candidate = _candidate_from_dict(node)
            if candidate:
                out.append(candidate)
    return out


def _extract_near_offer_links(html_text):
    out = []
    text = html_lib.unescape(html_text or "")
    for match in re.finditer(r"(?:https?:)?//detail\.1688\.com/offer/(\d+)\.html", text, flags=re.I):
        offer_id = match.group(1)
        start, end = max(0, match.start() - 1800), min(len(text), match.end() + 1800)
        block = text[start:end]
        title_m = re.search(r'"(?:title|subject|offerTitle)"\s*:\s*"([^"]{3,300})"', block, flags=re.I)
        price_m = re.search(r'"(?:price|offerPrice|tradePrice|priceDisplay)"\s*:\s*"?([^",}]{1,80})', block, flags=re.I)
        if not title_m or not price_m:
            continue
        price = _price_min(price_m.group(1))
        if price is None:
            continue
        image_m = re.search(r'"(?:imageUrl|imgUrl|picUrl)"\s*:\s*"([^"]+)"', block, flags=re.I)
        out.append({
            "title": _clean_text(title_m.group(1)),
            "price_cny": price,
            "url": f"https://detail.1688.com/offer/{offer_id}.html",
            "image": _clean_text(image_m.group(1)) if image_m else "",
            "seller": "",
            "moq": None,
        })
    return out


def _looks_blocked(text):
    low = str(text or "").lower()
    markers = ("captcha", "verify", "验证", "login.taobao", "login.1688", "访问过于频繁")
    return any(marker in low for marker in markers)


class Ali1688Connector(MarketplaceConnector):
    name = "1688"
    display_name = "1688"
    enabled = True
    currency = "CNY"

    def search(self, query, price_max=None, limit=20):
        query = " ".join(str(query or "").split())
        if not query:
            return []
        try:
            limit = max(1, min(int(limit), 120))
        except Exception:
            limit = 20
        try:
            price_max = float(price_max) if price_max is not None else None
        except Exception:
            price_max = None

        print(f"[1688] Recherche : {query}")
        session = _session()
        raw_candidates = []
        pages_ok = 0
        blocked = 0
        try:
            for variant in _query_variants(query):
                url = f"{SEARCH_URL}?keywords={quote(variant, safe='')}"
                try:
                    response = session.get(url, timeout=(4, 12), allow_redirects=True)
                except requests.RequestException as exc:
                    print(f"[1688] Erreur réseau : {exc}")
                    continue
                if response.status_code != 200:
                    print(f"[1688] HTTP {response.status_code} sur {variant}")
                    continue
                if _looks_blocked(response.text):
                    blocked += 1
                    print("[1688] Vérification/connexion détectée -> route ignorée")
                    continue
                pages_ok += 1
                raw_candidates.extend(_extract_json_candidates(response.text))
                raw_candidates.extend(_extract_near_offer_links(response.text))
        finally:
            session.close()

        rate = cny_eur_rate()
        results, seen = [], set()
        outside = invalid = 0
        for candidate in raw_candidates:
            title = candidate.get("title") or ""
            price_cny = _safe_float(candidate.get("price_cny"))
            url = candidate.get("url") or ""
            if not title or price_cny is None or not url:
                invalid += 1
                continue
            price_eur = round(price_cny * rate, 2)
            if price_max is not None and price_eur > price_max:
                outside += 1
                continue
            key = (re.sub(r"\s+", " ", title.lower()).strip(), url.split("?", 1)[0])
            if key in seen:
                continue
            seen.add(key)
            image = candidate.get("image") or ""
            if image.startswith("//"):
                image = "https:" + image
            results.append({
                "marketplace": self.name,
                "titre": title,
                "prix": price_eur,
                "prix_original": price_cny,
                "devise_originale": "CNY",
                "devise": "EUR",
                "lien": url,
                "image": image,
                "vendeur": candidate.get("seller") or "",
                "moq": candidate.get("moq"),
                "categorie": "A VERIFIER",
                "score": 62,
                "score_match": 75,
                "score_confiance": 45,
                "score_affaire": 55,
                "alertes": [
                    "Authenticité non vérifiée par LUXE RADAR",
                    "Prix converti depuis le CNY ; livraison, taxes et quantité minimale à vérifier",
                ],
                "raisons": ["Produit détecté sur la recherche publique 1688"],
            })
            if len(results) >= limit:
                break

        print(
            f"[1688][DIAG] pages_ok={pages_ok} | blocages={blocked} | "
            f"candidats={len(raw_candidates)} | invalides={invalid} | hors_budget={outside}"
        )
        print(f"[1688] {len(results)} resultats retenus")
        return results
