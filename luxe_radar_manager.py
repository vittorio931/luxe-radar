from __future__ import annotations

"""
LUXE RADAR MANAGER
==================
Gestionnaire local et non-destructif pour le projet LUXE RADAR.

Objectifs :
- sauvegardes ZIP + rollback ;
- diagnostic et compilation ;
- architecture de connecteurs auto-découverte ;
- filtre qualité universel (Trail Blazers, bourrage de marques, annonces "ne pas acheter") ;
- connecteur configurable pour Shopify, API JSON et pages Playwright ;
- ajout/activation/désactivation de sites depuis un fichier JSON ;
- synchronisation du registry ;
- tests locaux et test réseau optionnel ;
- menu interactif simple.

Le script ne contourne pas les connexions, CAPTCHA, paywalls ou protections anti-bot.
Il utilise uniquement les accès publics / API autorisées que le site expose.
"""

import argparse
import ast
import contextlib
import datetime as _dt
import hashlib
import importlib
import ipaddress
import json
import os
import pprint
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "LUXE RADAR MANAGER"
APP_VERSION = "1.0.0"
STATE_DIR = ".luxe_radar"
BACKUP_DIR = "backups"
MANAGED_VERSION = "2026.08"

MANAGED_FILES = (
    "marketplaces/connectors/authenticity.py",
    "marketplaces/connectors/quality_filters.py",
    "marketplaces/connectors/universal.py",
    "marketplaces/connectors/__init__.py",
    "marketplaces/sites.json",
)

EXCLUDED_DIRS = {
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    STATE_DIR,
}

# ---------------------------------------------------------------------------
# FICHIERS INSTALLES
# ---------------------------------------------------------------------------

AUTHENTICITY_PY = r'''from __future__ import annotations

import re
import unicodedata

# Ces marqueurs servent uniquement à prévenir l'utilisateur. Ils ne certifient
# jamais qu'un article est authentique ou contrefait.
HIGH_RISK_PHRASES = (
    "1:1",
    "1 1 quality",
    "replica",
    "replique",
    "réplique",
    "counterfeit",
    "contrefacon",
    "contrefaçon",
    "super fake",
    "mirror quality",
    "aaa quality",
    "unauthorized authentic",
    "ua batch",
    "clone",
)

MEDIUM_RISK_PHRASES = (
    "dupe",
    "inspired by",
    "inspire",
    "inspiré",
    "inspired",
    "same style",
    "same design",
)

# Prudence de source : ce niveau signifie seulement que l'authenticité n'est
# pas vérifiée par LUXE RADAR. Il ne signifie pas que les produits sont faux.
SOURCE_REVIEW_RECOMMENDED = {
    "dhgate",
    "1688",
    "hacoo",
    "aliexpress",
}


def _norm(value):
    text = "" if value is None else str(value)
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("-", " ").replace("_", " ").replace("’", "'")
    text = text.replace(":", " ")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(text, phrase):
    text_n = _norm(text)
    phrase_n = _norm(phrase)
    if not text_n or not phrase_n:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase_n) + r"(?![a-z0-9])", text_n) is not None


def authenticity_assessment(item, marketplace=""):
    """Retourne un niveau de prudence d'authenticité et une explication.

    Niveaux : faible, modere, eleve. Ils décrivent uniquement les signaux
    visibles dans l'annonce/la source et ne constituent pas une expertise.
    """
    if not isinstance(item, dict):
        return "modere", "Authenticité non vérifiable automatiquement", ""

    title = str(item.get("titre") or item.get("title") or "")
    description = " ".join(
        str(item.get(key) or "")
        for key in ("description", "texte", "condition", "etat")
    )
    full = f"{title} {description}".strip()

    high = [phrase for phrase in HIGH_RISK_PHRASES if _has(full, phrase)]
    if high:
        signal = ", ".join(high[:3])
        return (
            "eleve",
            "Risque élevé de contrefaçon : signal explicite dans l'annonce",
            signal,
        )

    medium = [phrase for phrase in MEDIUM_RISK_PHRASES if _has(full, phrase)]
    if medium:
        signal = ", ".join(medium[:3])
        return (
            "modere",
            "Authenticité à vérifier : formulation ambiguë dans l'annonce",
            signal,
        )

    source = str(marketplace or item.get("marketplace") or "").strip().lower()
    if source in SOURCE_REVIEW_RECOMMENDED:
        return (
            "modere",
            "Authenticité non vérifiée par LUXE RADAR sur cette source",
            "source à vérifier",
        )

    return (
        "faible",
        "Aucun signal explicite détecté — authenticité non garantie",
        "",
    )


def annotate_authenticity(item, marketplace=""):
    if not isinstance(item, dict):
        return item
    level, warning, signals = authenticity_assessment(item, marketplace=marketplace)
    item["risque_contrefacon"] = level
    item["alerte_authenticite"] = warning
    item["signaux_authenticite"] = signals
    return item
'''

QUALITY_FILTERS_PY = r'''from __future__ import annotations

import os
import re
import unicodedata

from .authenticity import annotate_authenticity

# Filtre volontairement conservateur : il retire surtout les faux positifs
# évidents. Il ne prétend jamais certifier l'authenticité d'un produit.

BRAND_ALIASES = {
    "nike": ("nike",),
    "adidas": ("adidas",),
    "jordan": ("jordan", "air jordan"),
    "hoka": ("hoka", "hoka one one"),
    "under armour": ("under armour", "underarmour"),
    "asics": ("asics",),
    "puma": ("puma",),
    "new balance": ("new balance",),
    "timberland": ("timberland",),
    "salomon": ("salomon",),
    "saucony": ("saucony",),
    "reebok": ("reebok",),
    "on": ("on running", "on cloud", "onrunning"),
    "lacoste": ("lacoste",),
    "ralph lauren": ("ralph lauren", "polo ralph lauren"),
    "stone island": ("stone island",),
    "the north face": ("the north face", "north face"),
    "arcteryx": ("arc teryx", "arcteryx"),
    "patagonia": ("patagonia",),
    "carhartt": ("carhartt", "carhartt wip"),
    "moncler": ("moncler",),
    "gucci": ("gucci",),
    "prada": ("prada",),
    "dior": ("dior",),
    "balenciaga": ("balenciaga",),
    "versace": ("versace",),
    "burberry": ("burberry",),
    "off white": ("off white", "offwhite"),
    "supreme": ("supreme",),
    "stussy": ("stussy",),
    "palace": ("palace",),
    "fear of god": ("fear of god", "essentials"),
    "amiri": ("amiri",),
    "cp company": ("c p company", "cp company"),
}

NO_BUY_PHRASES = (
    "ne pas acheter",
    "ne pas achete",
    "n achetez pas",
    "n achete pas",
    "do not buy",
    "dont buy",
    "don't buy",
    "fake listing",
    "scam listing",
    "annonce test",
    "test annonce",
)

EXPLICIT_FAKE_PHRASES = (
    "counterfeit",
    "contrefacon",
    "contrefaçon",
    "super fake",
    "mirror quality",
    "aaa quality",
    "unauthorized authentic",
)


def _norm(value):
    text = "" if value is None else str(value)
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("-", " ").replace("_", " ").replace("’", "'")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(text, phrase):
    text_n = _norm(text)
    phrase_n = _norm(phrase)
    if not text_n or not phrase_n:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase_n) + r"(?![a-z0-9])", text_n) is not None


def _detected_brands(text):
    found = set()
    for canonical, aliases in BRAND_ALIASES.items():
        if any(_has(text, alias) for alias in aliases):
            found.add(canonical)
    return found


def evaluate_result(item, query="", marketplace=""):
    """Retourne (keep: bool, reason: str|None)."""
    if not isinstance(item, dict):
        return False, "résultat non structuré"

    title = str(item.get("titre") or item.get("title") or "").strip()
    description = str(item.get("description") or item.get("texte") or "").strip()
    full = f"{title} {description}".strip()
    q = _norm(query)
    t = _norm(title)

    if not title:
        return False, "titre vide"

    for phrase in NO_BUY_PHRASES:
        if _has(full, phrase):
            return False, f"annonce à ignorer ({phrase})"

    # Nike Trail != Portland Trail Blazers.
    q_tokens = set(re.findall(r"[a-z0-9]+", q))
    if "trail" in q_tokens and "blazers" not in q_tokens:
        if "trail blazers" in t or ("trail" in t and "portland" in t and "blazers" in t):
            return False, "faux positif Portland Trail Blazers"

    # Bourrage de marques : 5 marques distinctes dans un titre court/normal est
    # presque toujours du SEO/spam pour une recherche produit spécifique.
    # On laisse passer les recherches explicitement de lots/bundles.
    allow_bundle = any(word in q_tokens for word in {"lot", "bundle", "pack", "ensemble"})
    brands = _detected_brands(title)
    query_brands = _detected_brands(query)

    if not allow_bundle and len(brands) >= 5:
        # Le titre doit contenir beaucoup plus de marques que la requête.
        if len(brands - query_brands) >= 3:
            return False, f"bourrage de marques ({len(brands)} marques)"

    # Autre signal de keyword stuffing : longue suite de marques concurrentes.
    tokens = t.split()
    if not allow_bundle and len(tokens) >= 10 and len(brands) >= 4:
        return False, f"titre SEO multi-marques ({len(brands)} marques)"

    return True, None


def filter_results(results, query="", marketplace=""):
    kept = []
    debug = os.environ.get("LUXE_RADAR_DEBUG_FILTERS") == "1"
    for item in results or []:
        ok, reason = evaluate_result(item, query=query, marketplace=marketplace)
        if ok:
            kept.append(annotate_authenticity(item, marketplace=marketplace))
        elif debug:
            title = item.get("titre") if isinstance(item, dict) else repr(item)
            print(f"[QUALITE] Rejet {marketplace}: {title} -> {reason}")
    return kept
'''

UNIVERSAL_PY = r'''from __future__ import annotations

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
'''

CONNECTORS_INIT_PY = r'''from __future__ import annotations

"""Auto-loader des connecteurs LUXE RADAR.

Tout fichier Python du dossier connectors contenant une classe héritant de
MarketplaceConnector peut être détecté automatiquement. Les fichiers backup,
test et helpers sont ignorés.
"""

import importlib
import inspect
import os
import pkgutil
from pathlib import Path

from .base import MarketplaceConnector
from .quality_filters import filter_results
from .universal import load_configured_connectors

_SKIP_MODULES = {
    "__init__",
    "base",
    "quality_filters",
    "universal",
}


def _debug(message):
    if os.environ.get("LUXE_RADAR_DEBUG_CONNECTORS") == "1":
        print(f"[CONNECTEURS] {message}")


def _should_skip(module_name):
    low = module_name.lower()
    if module_name in _SKIP_MODULES:
        return True
    return any(token in low for token in ("backup", "stable", "old", "test", "smoke"))


def _native_connectors():
    found = {}
    package_path = Path(__file__).resolve().parent
    prefix = __name__ + "."

    for info in pkgutil.iter_modules([str(package_path)]):
        module_name = info.name
        if _should_skip(module_name):
            continue
        try:
            module = importlib.import_module(prefix + module_name)
        except Exception as exc:
            _debug(f"Import ignoré {module_name}: {exc}")
            continue

        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is MarketplaceConnector:
                continue
            try:
                is_connector = issubclass(cls, MarketplaceConnector)
            except Exception:
                is_connector = False
            if not is_connector or cls.__module__ != module.__name__:
                continue
            try:
                instance = cls()
            except Exception as exc:
                _debug(f"Classe ignorée {cls.__name__}: {exc}")
                continue
            name = str(getattr(instance, "name", "") or "").strip()
            if not name or name == "Marketplace":
                continue
            found[name] = instance
    return found


class _QualityProxy:
    def __init__(self, connector):
        self._connector = connector

    def __getattr__(self, name):
        return getattr(self._connector, name)

    def search(self, query, price_max=None, limit=20, **kwargs):
        results = self._connector.search(query=query, price_max=price_max, limit=limit, **kwargs)
        return filter_results(
            results,
            query=query,
            marketplace=str(getattr(self._connector, "name", "")),
        )


def _all_raw_connectors():
    found = _native_connectors()
    for connector in load_configured_connectors():
        name = str(getattr(connector, "name", "") or "").strip()
        if name and name not in found:
            found[name] = connector
    return found


def _aliases(name, connector):
    values = {str(name).strip().lower()}
    for attr in ("name", "display_name"):
        value = str(getattr(connector, attr, "") or "").strip().lower()
        if value:
            values.add(value)
    return values


def get_available_connectors():
    result = {}
    for name, connector in _all_raw_connectors().items():
        if getattr(connector, "enabled", True):
            result[name] = _QualityProxy(connector)
    return result


def get_connector(name):
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for canonical, connector in _all_raw_connectors().items():
        if wanted in _aliases(canonical, connector):
            return _QualityProxy(connector)
    return None


def get_all_connectors(include_disabled=True):
    result = {}
    for name, connector in _all_raw_connectors().items():
        if include_disabled or getattr(connector, "enabled", True):
            result[name] = _QualityProxy(connector)
    return result
'''

DEFAULT_SITES_JSON = {
    "version": 1,
    "managed_by": APP_NAME,
    "sites": [],
    "notes": [
        "Les connecteurs configurables utilisent uniquement des pages/API publiques.",
        "mode=shopify : aucune sélection CSS nécessaire dans la plupart des boutiques Shopify.",
        "mode=json : renseigner search_url + chemins JSON.",
        "mode=playwright : renseigner search_url + selectors.card/title/price/link/image.",
        "Pour une devise autre que EUR, renseigner fx_rate_to_eur ou laisser le site désactivé.",
    ],
}

# ---------------------------------------------------------------------------
# UTILITAIRES
# ---------------------------------------------------------------------------

class ManagerError(RuntimeError):
    pass


def now_stamp():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def info(msg):
    print(f"[INFO] {msg}")


def ok(msg):
    print(f"[OK]   {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def fail(msg):
    print(f"[ERREUR] {msg}")


def sha256(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_project_root(start: Path | None = None) -> Path:
    candidates = []
    if start:
        candidates.append(start.resolve())
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve().parent)

    checked = set()
    for base in candidates:
        for p in [base, *base.parents]:
            if p in checked:
                continue
            checked.add(p)
            if (p / "radar_engine.py").exists() and (p / "marketplaces").is_dir():
                return p
    raise ManagerError(
        "Projet introuvable. Mets luxe_radar_manager.py dans le dossier qui contient "
        "radar_engine.py puis relance-le."
    )


def state_path(root: Path) -> Path:
    p = root / STATE_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_path(root: Path) -> Path:
    p = state_path(root) / BACKUP_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = content.replace("\r\n", "\n")
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManagerError(f"JSON invalide {path}: {exc}") from exc


def save_json(path: Path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def project_python(root: Path) -> Path:
    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


def run_command(cmd, cwd: Path, timeout=120, check=False, capture=False, env=None):
    kwargs = {
        "cwd": str(cwd),
        "timeout": timeout,
        "text": True,
        "env": env or os.environ.copy(),
    }
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result = subprocess.run([str(x) for x in cmd], **kwargs)
    if check and result.returncode != 0:
        output = result.stdout if capture else ""
        raise ManagerError(f"Commande échouée ({result.returncode}): {' '.join(map(str, cmd))}\n{output}")
    return result


def iter_backup_files(root: Path, include_secrets=False):
    special_files = {".gitignore", ".dockerignore", ".env.example", "Procfile"}
    source_suffixes = {".py", ".html", ".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".js", ".css", ".svg", ".webmanifest", ".vtt"}
    public_asset_suffixes = {".png", ".webp", ".jpg", ".jpeg", ".ico"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.name.lower() in {".env"} and not include_secrets:
            continue
        if rel.suffix.lower() in {".pyc", ".pyo"}:
            continue
        # Sources/configs/templates principalement ; garde aussi quelques fichiers utiles.
        is_public_asset = rel.parts and rel.parts[0] == "static" and rel.suffix.lower() in public_asset_suffixes
        if rel.suffix.lower() not in source_suffixes and rel.name not in special_files and rel.name != ".env" and not is_public_asset:
            continue
        yield path, rel


# ---------------------------------------------------------------------------
# SAUVEGARDES / RESTAURATION
# ---------------------------------------------------------------------------

def create_backup(root: Path, label="manual", include_secrets=False) -> Path:
    backup_dir = backups_path(root)
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_") or "backup"
    zip_path = backup_dir / f"{now_stamp()}_{safe_label}.zip"
    files = list(iter_backup_files(root, include_secrets=include_secrets))
    manifest = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "root_name": root.name,
        "include_secrets": include_secrets,
        "files": [],
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, rel in files:
            zf.write(path, arcname=str(rel).replace("\\", "/"))
            manifest["files"].append({
                "path": str(rel).replace("\\", "/"),
                "sha256": sha256(path),
                "size": path.stat().st_size,
            })
        zf.writestr(".luxe_radar_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    ok(f"Sauvegarde créée : {zip_path.name} ({len(files)} fichiers)")
    return zip_path


def list_backups(root: Path):
    items = sorted(backups_path(root).glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not items:
        print("Aucune sauvegarde.")
        return []
    for i, p in enumerate(items, 1):
        size_mb = p.stat().st_size / (1024 * 1024)
        dt = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M:%S")
        print(f"{i:>2}. {p.name}  ({size_mb:.2f} Mo, {dt})")
    return items


def _read_backup_manifest(zip_path: Path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            return json.loads(zf.read(".luxe_radar_manifest.json").decode("utf-8"))
        except Exception:
            return {"files": []}


def restore_backup(root: Path, zip_path: Path, make_safety_backup=True):
    if not zip_path.exists():
        raise ManagerError(f"Sauvegarde introuvable : {zip_path}")
    if make_safety_backup:
        create_backup(root, label="avant_restoration")
    manifest = _read_backup_manifest(zip_path)
    backed = {str(x.get("path")) for x in manifest.get("files", []) if isinstance(x, dict)}

    # Retire uniquement les fichiers gérés créés après la sauvegarde.
    for rel in MANAGED_FILES:
        if rel not in backed:
            path = root / rel
            if path.exists() and path.is_file():
                path.unlink()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.filename == ".luxe_radar_manifest.json" or member.is_dir():
                continue
            target = (root / member.filename).resolve()
            if root.resolve() not in target.parents and target != root.resolve():
                raise ManagerError("Archive de sauvegarde invalide (path traversal détecté).")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    ok(f"Restauration terminée depuis {zip_path.name}")


# ---------------------------------------------------------------------------
# INSTALLATION DE L'ARCHITECTURE
# ---------------------------------------------------------------------------

def ensure_structure(root: Path):
    required = [
        root / "radar_engine.py",
        root / "marketplaces",
        root / "marketplaces" / "connectors",
        root / "marketplaces" / "connectors" / "base.py",
    ]
    missing = [str(p.relative_to(root)) for p in required if not p.exists()]
    if missing:
        raise ManagerError("Fichiers/dossiers requis manquants : " + ", ".join(missing))


def install_framework(root: Path):
    ensure_structure(root)
    connectors = root / "marketplaces" / "connectors"
    atomic_write_text(connectors / "authenticity.py", AUTHENTICITY_PY)
    atomic_write_text(connectors / "quality_filters.py", QUALITY_FILTERS_PY)
    atomic_write_text(connectors / "universal.py", UNIVERSAL_PY)
    atomic_write_text(connectors / "__init__.py", CONNECTORS_INIT_PY)

    sites_path = root / "marketplaces" / "sites.json"
    if not sites_path.exists():
        save_json(sites_path, DEFAULT_SITES_JSON)
    else:
        current = load_json(sites_path, DEFAULT_SITES_JSON)
        if not isinstance(current, dict):
            current = DEFAULT_SITES_JSON.copy()
        current.setdefault("version", 1)
        current.setdefault("sites", [])
        current["managed_by"] = APP_NAME
        save_json(sites_path, current)

    state = load_json(state_path(root) / "state.json", {})
    state.update({
        "framework_version": MANAGED_VERSION,
        "manager_version": APP_VERSION,
        "installed_at": _dt.datetime.now().isoformat(timespec="seconds"),
    })
    save_json(state_path(root) / "state.json", state)
    ok("Architecture universelle installée / mise à jour")


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------

def _find_assignment_span(text: str, variable: str):
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == variable for t in targets):
                value = node.value
                start = (node.lineno, node.col_offset)
                end = (node.end_lineno, node.end_col_offset)
                return start, end, value
    return None


def _line_offsets(text: str):
    offsets = [0]
    for m in re.finditer("\n", text):
        offsets.append(m.end())
    return offsets


def _absolute_offset(offsets, line, col):
    return offsets[line - 1] + col


def discover_connector_names(root: Path, include_disabled=False):
    py = project_python(root)
    code = (
        "import json; "
        "from marketplaces.connectors import get_all_connectors; "
        f"d=get_all_connectors(include_disabled={str(bool(include_disabled))}); "
        "print(json.dumps({k:{'enabled':bool(getattr(v,'enabled',True)),'display':str(getattr(v,'display_name',k))} for k,v in d.items()}))"
    )
    result = run_command([py, "-c", code], cwd=root, timeout=40, capture=True)
    if result.returncode != 0:
        raise ManagerError("Impossible de découvrir les connecteurs :\n" + (result.stdout or ""))
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except Exception:
            continue
    raise ManagerError("La découverte des connecteurs n'a pas renvoyé de JSON exploitable.")


def _console_safe(value):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="replace").decode(encoding, errors="replace")


def sync_registry(root: Path):
    # Depuis le catalogue v2, registry.py est une vue dynamique de sites.json.
    # On valide donc les données et la correspondance avec les connecteurs au
    # lieu de régénérer une liste Python statique.
    sites_data = load_json(root / "marketplaces" / "sites.json", DEFAULT_SITES_JSON)
    if int(sites_data.get("version", 1)) >= 2:
        sites = [site for site in sites_data.get("sites", []) if isinstance(site, dict)]
        discovered = discover_connector_names(root, include_disabled=True)
        names = {str(site.get("name") or "").casefold() for site in sites}
        missing = [name for name in discovered if name.casefold() not in names]
        if missing:
            warn("Connecteurs absents du catalogue : " + ", ".join(sorted(missing)))
        active = sum(1 for site in sites if site.get("status") == "active" and site.get("enabled"))
        ok(f"Registry dynamique vérifié : {len(sites)} site(s), {active} actif(s)")
        return
    path = root / "marketplaces" / "registry.py"
    if not path.exists():
        warn("registry.py absent : synchronisation ignorée")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    span = _find_assignment_span(text, "MARKETPLACES")
    if not span:
        warn("MARKETPLACES introuvable dans registry.py : synchronisation ignorée")
        return
    (start_line, start_col), (end_line, end_col), value_node = span
    try:
        # On lit uniquement la valeur de l'affectation, jamais du code arbitraire.
        marketplaces = ast.literal_eval(value_node)
    except Exception as exc:
        warn(f"MARKETPLACES non interprétable : {exc}")
        return
    if not isinstance(marketplaces, list):
        warn("MARKETPLACES n'est pas une liste")
        return

    discovered = discover_connector_names(root, include_disabled=True)
    active_lower = {
        name.lower(): meta
        for name, meta in discovered.items()
        if meta.get("enabled")
    }
    all_lower = {name.lower(): meta for name, meta in discovered.items()}
    seen = set()

    for item in marketplaces:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        low = name.lower()
        seen.add(low)
        if low in active_lower:
            item["enabled"] = True
            item["status"] = "active"
        elif low in all_lower:
            item["enabled"] = False
            item["status"] = "planned"

    # Ajoute les nouveaux sites configurés absents du catalogue.
    sites = load_json(root / "marketplaces" / "sites.json", DEFAULT_SITES_JSON)
    category_by_name = {
        str(s.get("name", "")).strip().lower(): str(s.get("category") or "Mode / autres")
        for s in sites.get("sites", [])
        if isinstance(s, dict)
    }
    for name, meta in discovered.items():
        low = name.lower()
        if low in seen:
            continue
        enabled = bool(meta.get("enabled"))
        marketplaces.append({
            "name": name,
            "category": category_by_name.get(low, "Mode / autres"),
            "enabled": enabled,
            "status": "active" if enabled else "planned",
        })

    formatted_value = pprint.pformat(marketplaces, width=120, sort_dicts=False)
    replacement = f"MARKETPLACES = {formatted_value}"

    # Le registry historique contient des accents. Les colonnes AST sont des
    # offsets UTF-8 (octets), donc on évite de les convertir naïvement en
    # offsets de caractères. On remplace d'abord le bloc entre les deux
    # affectations connues, ce qui est plus robuste.
    pattern = re.compile(
        r"(?ms)^MARKETPLACES\s*=.*?(?=^MARKETPLACE_GROUPS\s*=)",
    )
    if pattern.search(text):
        new_text = pattern.sub(replacement + "\n\n", text, count=1)
    else:
        # Fallback par lignes : remplace l'affectation complète signalée par AST.
        lines = text.splitlines(keepends=True)
        before = "".join(lines[: start_line - 1])
        after = "".join(lines[end_line:])
        new_text = before + replacement + "\n" + after

    atomic_write_text(path, new_text)
    ok(f"Registry synchronisé : {len(active_lower)} connecteur(s) actif(s)")


# ---------------------------------------------------------------------------
# CONFIGURATION DES SITES
# ---------------------------------------------------------------------------

def load_sites(root: Path):
    path = root / "marketplaces" / "sites.json"
    data = load_json(path, DEFAULT_SITES_JSON.copy())
    data.setdefault("sites", [])
    return data


def save_sites(root: Path, data):
    data["managed_by"] = APP_NAME
    data.setdefault("version", 1)
    save_json(root / "marketplaces" / "sites.json", data)


def _assert_public_http_url(url: str):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Le probe accepte uniquement une URL HTTP(S) valide")
    if parsed.username or parsed.password:
        raise ValueError("Les identifiants intégrés à l'URL ne sont pas autorisés")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        }
    except socket.gaierror as exc:
        raise ValueError("Le domaine ne peut pas être résolu") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Les adresses locales, privées ou réservées ne peuvent pas être sondées")
    return parsed


class _PublicHttpRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def probe_site(url: str, timeout=8):
    url = str(url).strip().rstrip("/")
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url
    parsed = _assert_public_http_url(url)
    result = {
        "url": url,
        "host": parsed.netloc,
        "reachable": False,
        "shopify": False,
        "status": None,
        "notes": [],
        "catalog_status": "to_test",
        "connector_type": None,
        "capabilities": {"search": False, "price": False, "image": False, "reference": False},
    }
    headers = {"User-Agent": "Mozilla/5.0 LUXE-RADAR-Manager/1.0"}
    opener = urllib.request.build_opener(_PublicHttpRedirectHandler())
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(512_000).decode("utf-8", errors="ignore")
            result["status"] = getattr(resp, "status", 200)
            result["reachable"] = True
            low = body.lower()
            if "cdn.shopify.com" in low or "shopify-section" in low or "myshopify.com" in low:
                result["shopify"] = True
                result["notes"].append("Signature Shopify détectée dans la page")
    except Exception as exc:
        result["notes"].append(f"Page principale inaccessible: {exc}")

    # Test public Shopify product endpoint /products.json (sans conclure si bloqué).
    try:
        req = urllib.request.Request(url + "/products.json?limit=1", headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read(512_000).decode("utf-8", errors="ignore"))
            if isinstance(data, dict) and isinstance(data.get("products"), list):
                result["shopify"] = True
                result["notes"].append("Endpoint Shopify products.json détecté")
    except Exception:
        pass
    result["connector_type"] = "shopify" if result["shopify"] else "playwright"
    if result["shopify"] and result["reachable"]:
        result["notes"].append("Candidat Shopify détecté ; une recherche réelle reste obligatoire avant activation")
    return result


def add_site(root: Path, name: str, base_url: str, mode="auto", enable=False, category="Mode / autres", currency="EUR", **kwargs):
    data = load_sites(root)
    name = str(name or "").strip()
    base_url = str(base_url or "").strip().rstrip("/")
    if not name or not base_url:
        raise ManagerError("Nom et URL obligatoires.")
    if not re.match(r"^https?://", base_url, flags=re.I):
        base_url = "https://" + base_url

    if mode == "auto":
        probe = probe_site(base_url)
        mode = "shopify" if probe.get("shopify") else "playwright"
        if mode == "playwright" and enable:
            warn("Site non détecté Shopify : création Playwright désactivée tant que les sélecteurs ne sont pas renseignés.")
            enable = False

    existing = None
    for site in data["sites"]:
        if isinstance(site, dict) and str(site.get("name", "")).strip().lower() == name.lower():
            existing = site
            break
    site = existing if existing is not None else {}
    site.update({
        "name": name,
        "display_name": name,
        "base_url": base_url,
        "mode": mode,
        "connector_type": mode,
        "enabled": bool(enable),
        "status": "active" if enable else "to_test",
        "category": category,
        "currency": currency.upper(),
        "country": str(kwargs.get("country") or site.get("country") or ""),
        "capabilities": site.get("capabilities") or {
            "search": bool(enable),
            "price": bool(enable),
            "image": False,
            "reference": False,
        },
        "timeout": int(kwargs.get("timeout", 12)),
        "confidence": int(kwargs.get("confidence", 55)),
    })

    if mode == "shopify":
        site.setdefault("price_divisor", 100)
    elif mode in {"json", "api", "json_api"}:
        site.update({
            "search_url": kwargs.get("search_url") or site.get("search_url") or "",
            "items_path": kwargs.get("items_path") or site.get("items_path") or "",
            "title_path": kwargs.get("title_path") or site.get("title_path") or "title",
            "price_path": kwargs.get("price_path") or site.get("price_path") or "price",
            "url_path": kwargs.get("url_path") or site.get("url_path") or "url",
            "image_path": kwargs.get("image_path") or site.get("image_path") or "image",
            "price_divisor": _coerce_number(kwargs.get("price_divisor"), site.get("price_divisor", 1)),
        })
    elif mode in {"playwright", "html", "browser"}:
        site.setdefault("search_url", kwargs.get("search_url") or "")
        site.setdefault("selectors", {
            "card": "",
            "title": "",
            "price": "",
            "link": "a",
            "image": "img",
        })
        site.setdefault("headless", True)
        site.setdefault("wait_ms", 1800)

    fx = kwargs.get("fx_rate_to_eur")
    if fx is not None:
        site["fx_rate_to_eur"] = float(fx)
    if existing is None:
        data["sites"].append(site)
    save_sites(root, data)
    ok(f"Site {'mis à jour' if existing else 'ajouté'} : {name} (mode={mode}, enabled={site['enabled']})")
    return site


def _coerce_number(value, default):
    if value is None or value == "":
        return default
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except Exception:
        return default


def set_site_enabled(root: Path, name: str, enabled: bool):
    data = load_sites(root)
    for site in data["sites"]:
        if isinstance(site, dict) and str(site.get("name", "")).strip().lower() == name.strip().lower():
            site["enabled"] = bool(enabled)
            site["status"] = "active" if enabled else "off"
            save_sites(root, data)
            ok(f"{name} : {'activé' if enabled else 'désactivé'}")
            return
    raise ManagerError(f"Site configuré introuvable : {name}")


def remove_site(root: Path, name: str):
    data = load_sites(root)
    before = len(data["sites"])
    data["sites"] = [
        s for s in data["sites"]
        if not (isinstance(s, dict) and str(s.get("name", "")).strip().lower() == name.strip().lower())
    ]
    if len(data["sites"]) == before:
        raise ManagerError(f"Site configuré introuvable : {name}")
    save_sites(root, data)
    ok(f"Site supprimé de la configuration : {name}")


def list_sites(root: Path):
    data = load_sites(root)
    sites = data.get("sites", [])
    if not sites:
        print("Aucun site configurable ajouté pour l'instant.")
    else:
        print("\nSites configurables :")
        for i, s in enumerate(sites, 1):
            status = str(s.get("status") or ("active" if s.get("enabled") else "off")).upper()
            connector_type = s.get("connector_type") or s.get("mode")
            print(f"{i:>2}. [{status:<7}] {s.get('name')} | {connector_type} | {s.get('base_url')}")
    try:
        connectors = discover_connector_names(root, include_disabled=True)
        print("\nConnecteurs détectés :")
        for name, meta in sorted(connectors.items(), key=lambda kv: kv[0].lower()):
            status = "ACTIF" if meta.get("enabled") else "OFF"
            print(f" - [{status:<5}] {_console_safe(name)}")
    except Exception as exc:
        warn(str(exc))


# ---------------------------------------------------------------------------
# DIAGNOSTIC / TESTS
# ---------------------------------------------------------------------------

def python_files(root: Path):
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        low = path.name.lower()
        if any(token in low for token in ("backup", "stable", "old")):
            continue
        yield path


def compile_all(root: Path):
    errors = []
    files = list(python_files(root))
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            errors.append((path.relative_to(root), str(exc)))
    if errors:
        for rel, err in errors:
            fail(f"{rel}: {err}")
        raise ManagerError(f"{len(errors)} fichier(s) Python ne compilent pas.")
    ok(f"Compilation : {len(files)} fichier(s) Python OK")


def import_smoke(root: Path):
    py = project_python(root)
    checks = [
        "import radar_engine; print('radar_engine OK')",
        "from marketplaces.connectors import get_connector,get_available_connectors; print('connectors OK', list(get_available_connectors().keys()))",
    ]
    for code in checks:
        res = run_command([py, "-c", code], cwd=root, timeout=40, capture=True)
        if res.returncode != 0:
            raise ManagerError("Test d'import échoué :\n" + (res.stdout or ""))
        if res.stdout:
            print(res.stdout.strip())
    ok("Imports principaux OK")


def run_smoke_files(root: Path):
    py = project_python(root)
    tests = [
        root / "test_radar_engine_smoke.py",
        root / "test_asos_connector.py",
        root / "test_cdiscount_connector.py",
        root / "test_search_intent_v27.py",
        root / "test_sources_risk_v28.py",
        root / "test_infinite_scroll.py",
        root / "test_catalog_massive.py",
        root / "test_security_ux.py",
        root / "test_image_search.py",
        root / "test_billing_stripe.py",
        root / "test_behaviour67_smoke.py",
    ]
    ran = 0
    for test in tests:
        if not test.exists():
            continue
        info(f"Test : {test.name}")
        res = run_command([py, test], cwd=root, timeout=120, capture=True)
        if res.stdout:
            print(res.stdout.rstrip())
        if res.returncode != 0:
            raise ManagerError(f"Échec du test {test.name}")
        ran += 1
    if ran:
        ok(f"{ran} smoke test(s) OK")
    else:
        warn("Aucun smoke test trouvé (ce n'est pas bloquant)")


def quality_self_test(root: Path):
    py = project_python(root)
    code = r'''from marketplaces.connectors.quality_filters import evaluate_result,filter_results
cases=[
({"titre":"Nike Dri-FIT Trail Running T-Shirt"},"Nike Trail",True),
({"titre":"Short Nike Portland Trail Blazers Statement Edition"},"Nike Trail",False),
({"titre":"tshirt nike trail NEW hoka under adidas L asics puma armour balance timberland"},"Nike Trail",False),
({"titre":"Nike x Off White Trail shoe"},"Nike Trail",True),
]
for item,q,expected in cases:
    got,_=evaluate_result(item,q,"eBay")
    assert got==expected,(item,got,expected)
suspicious=filter_results([{"titre":"Nike Trail replica 1:1"}],"Nike Trail","DHgate")
assert suspicious and suspicious[0]["risque_contrefacon"]=="eleve"
print("quality filters OK")'''
    res = run_command([py, "-c", code], cwd=root, timeout=30, capture=True)
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        raise ManagerError("Auto-test filtre qualité échoué :\n" + (res.stdout or ""))
    ok("Filtre anti-faux-positifs / anti-keyword-stuffing OK")


def network_test(root: Path, query="Nike Trail", price=50, limit=10):
    py = project_python(root)
    code = textwrap.dedent(f'''
        from radar_engine import rechercher_multi_marketplaces
        r = rechercher_multi_marketplaces(marque={query!r}, prix_max={float(price)!r}, plateformes=None, limite={int(limit)!r})
        print("TOTAL:", len(r))
        for x in r:
            print(x.get("marketplace"), "|", x.get("titre"), "|", x.get("prix"), "EUR |", x.get("categorie"), "| score", x.get("score"))
    ''')
    info(f"Test réseau : {query} <= {price} EUR")
    res = run_command([py, "-c", code], cwd=root, timeout=240, capture=False)
    if res.returncode != 0:
        raise ManagerError("Test réseau échoué")
    ok("Test réseau terminé")


def collect_catalogue(root: Path, seeds=None, price=None, sources="", dry_run=False,
                      budget=None, stats=False, seed_query=""):
    """Collecte de catalogue profond (seeds -> index + traces)."""
    py = project_python(root)
    args = [py, str(root / "collector.py")]
    if stats:
        args.append("--stats")
        if seed_query:
            args += ["--seed-query", seed_query]
        res = run_command(args, cwd=root, timeout=120, capture=False)
        if res.returncode != 0:
            raise ManagerError("Collector stats échoué")
        return
    for seed in (seeds or []):
        args += ["--seed", seed]
    if price is not None:
        args += ["--price", str(price)]
    if sources:
        args += ["--sources", sources]
    if dry_run:
        args.append("--dry-run")
    if budget is not None:
        args += ["--budget", str(budget)]
    info(f"Collecte de catalogue profond (dry_run={dry_run})")
    res = run_command(args, cwd=root, timeout=3600, capture=False)
    if res.returncode != 0:
        raise ManagerError("Collecte catalogue échouée")
    ok("Collecte catalogue terminée")


def doctor(root: Path):
    print(f"\n=== {APP_NAME} {APP_VERSION} ===")
    print(f"Projet : {root}")
    print(f"Python : {project_python(root)}")
    print(f"Manager : {Path(__file__).resolve()}")

    checks = {
        "radar_engine.py": root / "radar_engine.py",
        "app_web.py": root / "app_web.py",
        "marketplaces/registry.py": root / "marketplaces" / "registry.py",
        "connectors/base.py": root / "marketplaces" / "connectors" / "base.py",
        "connectors/__init__.py": root / "marketplaces" / "connectors" / "__init__.py",
        "templates/index.html": root / "templates" / "index.html",
        ".env": root / ".env",
    }
    print("\nFichiers :")
    for label, path in checks.items():
        print(f" - {'OK ' if path.exists() else 'ABS'} {label}")

    py = project_python(root)
    modules = ["requests", "flask", "playwright", "dotenv"]
    print("\nDépendances :")
    for module in modules:
        code = f"import {module}; print('OK')"
        res = run_command([py, "-c", code], cwd=root, timeout=15, capture=True)
        print(f" - {'OK ' if res.returncode == 0 else 'ABS'} {module}")

    try:
        connectors = discover_connector_names(root, include_disabled=True)
        print("\nConnecteurs :")
        for name, meta in sorted(connectors.items()):
            print(f" - {'ACTIF' if meta.get('enabled') else 'OFF  '} {_console_safe(name)}")
    except Exception as exc:
        warn(f"Connecteurs non inspectables : {exc}")

    state = load_json(state_path(root) / "state.json", {})
    if state:
        print("\nEtat manager :")
        print(json.dumps(state, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# DEPENDANCES / SERVEUR
# ---------------------------------------------------------------------------

def install_dependencies(root: Path):
    py = project_python(root)
    packages = ["requests", "flask", "python-dotenv", "playwright"]
    info("Installation/mise à jour des dépendances Python principales...")
    run_command([py, "-m", "pip", "install", *packages], cwd=root, timeout=240, check=True)
    ok("Dépendances Python installées")
    # Ne télécharge Chromium que si l'utilisateur a demandé explicitement --with-deps.
    info("Vérification du navigateur Playwright Chromium...")
    res = run_command([py, "-m", "playwright", "install", "chromium"], cwd=root, timeout=300)
    if res.returncode == 0:
        ok("Chromium Playwright disponible")
    else:
        warn("Chromium Playwright n'a pas pu être installé automatiquement")


def run_app(root: Path):
    py = project_python(root)
    app = root / "app_web.py"
    if not app.exists():
        raise ManagerError("app_web.py introuvable")
    print("\nServeur LUXE RADAR. Ctrl+C pour arrêter.\n")
    try:
        subprocess.run([str(py), str(app)], cwd=str(root))
    except KeyboardInterrupt:
        print("\nServeur arrêté.")


# ---------------------------------------------------------------------------
# SETUP COMPLET
# ---------------------------------------------------------------------------

def setup(root: Path, with_deps=False, run_network=False):
    print(f"\n=== INSTALLATION SÉCURISÉE {APP_NAME} ===")
    ensure_structure(root)
    backup = create_backup(root, label="avant_upgrade")
    try:
        if with_deps:
            install_dependencies(root)
        install_framework(root)
        compile_all(root)
        import_smoke(root)
        quality_self_test(root)
        sync_registry(root)
        compile_all(root)
        run_smoke_files(root)
        if run_network:
            network_test(root)
    except Exception:
        fail("L'upgrade a échoué. Restauration automatique de la sauvegarde...")
        try:
            restore_backup(root, backup, make_safety_backup=False)
            ok("Ancienne version restaurée automatiquement")
        except Exception as rollback_exc:
            fail(f"Rollback automatique impossible : {rollback_exc}")
        raise
    state = load_json(state_path(root) / "state.json", {})
    state["last_successful_backup"] = str(backup)
    state["last_successful_setup"] = _dt.datetime.now().isoformat(timespec="seconds")
    save_json(state_path(root) / "state.json", state)
    print("\n" + "=" * 64)
    ok("UPGRADE TERMINÉ : projet compilé, imports et filtres validés")
    print("=" * 64)
    print("Le mode 'Toutes' peut maintenant découvrir automatiquement les nouveaux connecteurs actifs.")


# ---------------------------------------------------------------------------
# MENU INTERACTIF
# ---------------------------------------------------------------------------

def prompt_bool(label, default=False):
    suffix = "[O/n]" if default else "[o/N]"
    value = input(f"{label} {suffix} : ").strip().lower()
    if not value:
        return default
    return value in {"o", "oui", "y", "yes", "1"}


def interactive_add_site(root: Path):
    print("\n=== AJOUTER UN SITE ===")
    name = input("Nom du site : ").strip()
    url = input("URL principale (ex: https://exemple.com) : ").strip()
    print("Mode : 1=auto  2=shopify  3=json/API  4=playwright")
    choice = input("Choix [1] : ").strip() or "1"
    mode = {"1": "auto", "2": "shopify", "3": "json", "4": "playwright"}.get(choice, "auto")
    enable = prompt_bool("Activer immédiatement ?", default=(mode in {"auto", "shopify"}))
    category = input("Catégorie [Mode / autres] : ").strip() or "Mode / autres"
    currency = input("Devise [EUR] : ").strip().upper() or "EUR"
    kwargs = {}
    if currency != "EUR":
        fx = input("Taux fixe vers EUR (ex 0.00908), vide pour désactiver le calcul : ").strip()
        if fx:
            kwargs["fx_rate_to_eur"] = float(fx)
    if mode == "json":
        kwargs["search_url"] = input("URL API avec {query} : ").strip()
        kwargs["items_path"] = input("Chemin liste JSON (ex: data.items) : ").strip()
        kwargs["title_path"] = input("Chemin titre [title] : ").strip() or "title"
        kwargs["price_path"] = input("Chemin prix [price] : ").strip() or "price"
        kwargs["url_path"] = input("Chemin URL [url] : ").strip() or "url"
        kwargs["image_path"] = input("Chemin image [image] : ").strip() or "image"
        kwargs["price_divisor"] = input("Diviseur prix [1] : ").strip() or "1"
    site = add_site(root, name, url, mode=mode, enable=enable, category=category, currency=currency, **kwargs)
    if site.get("enabled"):
        sync_registry(root)
    if site.get("mode") == "playwright":
        warn("Pour un site Playwright, renseigne les sélecteurs dans marketplaces/sites.json avant activation.")


def interactive_restore(root: Path):
    items = list_backups(root)
    if not items:
        return
    value = input("Numéro de la sauvegarde à restaurer (vide=annuler) : ").strip()
    if not value:
        return
    try:
        index = int(value) - 1
        selected = items[index]
    except Exception:
        warn("Choix invalide")
        return
    if prompt_bool(f"Restaurer {selected.name} ?", default=False):
        restore_backup(root, selected)


def menu(root: Path):
    while True:
        print(f"\n{'=' * 60}\n{APP_NAME} {APP_VERSION}\nProjet : {root}\n{'=' * 60}")
        print("1. UPGRADE COMPLET sécurisé")
        print("2. Diagnostic")
        print("3. Tester tout (sans réseau)")
        print("4. Tester en vrai sur Internet")
        print("5. Ajouter un site")
        print("6. Lister les sites/connecteurs")
        print("7. Activer un site configurable")
        print("8. Désactiver un site configurable")
        print("9. Sauvegarder")
        print("10. Restaurer une sauvegarde")
        print("11. Synchroniser le registry")
        print("12. Lancer LUXE RADAR")
        print("0. Quitter")
        choice = input("\nChoix : ").strip()
        try:
            if choice == "1":
                setup(root, with_deps=False, run_network=False)
            elif choice == "2":
                doctor(root)
            elif choice == "3":
                compile_all(root); import_smoke(root); quality_self_test(root); run_smoke_files(root)
            elif choice == "4":
                query = input("Recherche [Nike Trail] : ").strip() or "Nike Trail"
                price = float(input("Prix max [50] : ").strip() or "50")
                limit = int(input("Nombre de résultats [20] : ").strip() or "20")
                network_test(root, query=query, price=price, limit=limit)
            elif choice == "5":
                interactive_add_site(root)
            elif choice == "6":
                list_sites(root)
            elif choice == "7":
                set_site_enabled(root, input("Nom exact : ").strip(), True); sync_registry(root)
            elif choice == "8":
                set_site_enabled(root, input("Nom exact : ").strip(), False); sync_registry(root)
            elif choice == "9":
                create_backup(root, label="manual")
            elif choice == "10":
                interactive_restore(root)
            elif choice == "11":
                sync_registry(root)
            elif choice == "12":
                run_app(root)
            elif choice == "0":
                return
            else:
                warn("Choix inconnu")
        except KeyboardInterrupt:
            print("\nAction annulée.")
        except Exception as exc:
            fail(str(exc))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description="Gestionnaire complet LUXE RADAR")
    parser.add_argument("--root", help="Dossier du projet (détection automatique par défaut)")
    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", help="Backup + architecture + tests + registry")
    p_setup.add_argument("--with-deps", action="store_true", help="Installer aussi dépendances + Chromium")
    p_setup.add_argument("--network", action="store_true", help="Faire un test réseau à la fin")

    sub.add_parser("doctor", help="Diagnostic complet")
    sub.add_parser("test", help="Compilation + imports + smoke tests")
    sub.add_parser("backup", help="Créer une sauvegarde")
    sub.add_parser("backups", help="Lister les sauvegardes")
    sub.add_parser("sites", help="Lister sites et connecteurs")
    sub.add_parser("sync-registry", help="Synchroniser registry.py")
    sub.add_parser("run", help="Lancer app_web.py")

    p_restore = sub.add_parser("restore", help="Restaurer une sauvegarde ZIP")
    p_restore.add_argument("zip", help="Chemin ZIP ou nom dans .luxe_radar/backups")

    p_probe = sub.add_parser("probe", help="Détecter le type d'un site")
    p_probe.add_argument("url")

    p_add = sub.add_parser("add-site", help="Ajouter un site configurable")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--mode", default="auto", choices=["auto", "shopify", "json", "playwright"])
    p_add.add_argument("--enable", action="store_true")
    p_add.add_argument("--category", default="Mode / autres")
    p_add.add_argument("--currency", default="EUR")
    p_add.add_argument("--fx-rate-to-eur", type=float)
    p_add.add_argument("--search-url")
    p_add.add_argument("--items-path")
    p_add.add_argument("--title-path")
    p_add.add_argument("--price-path")
    p_add.add_argument("--url-path")
    p_add.add_argument("--image-path")
    p_add.add_argument("--price-divisor", type=float)

    p_enable = sub.add_parser("enable-site")
    p_enable.add_argument("name")
    p_disable = sub.add_parser("disable-site")
    p_disable.add_argument("name")
    p_remove = sub.add_parser("remove-site")
    p_remove.add_argument("name")

    p_net = sub.add_parser("network-test")
    p_net.add_argument("--query", default="Nike Trail")
    p_net.add_argument("--price", type=float, default=50)
    p_net.add_argument("--limit", type=int, default=20)

    p_collect = sub.add_parser(
        "collect",
        help="Collecte de catalogue profond (seeds -> index + traces)",
    )
    p_collect.add_argument("--seed", action="append", dest="seeds", help="Seed 'query|prix' (répétable).")
    p_collect.add_argument("--price", type=float, default=None, help="Prix max par défaut.")
    p_collect.add_argument("--sources", default="", help="Sources séparées par des virgules.")
    p_collect.add_argument("--dry-run", action="store_true", help="Audit sans écrire index/traces.")
    p_collect.add_argument("--budget", type=float, default=None, help="Budget secondes par seed.")
    p_collect.add_argument("--stats", action="store_true", help="Afficher les statistiques collector.")
    p_collect.add_argument("--seed-query", default="", help="Filtrer les stats par seed.")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    root_hint = Path(args.root) if getattr(args, "root", None) else None
    root = find_project_root(root_hint)
    cmd = args.command

    if not cmd:
        menu(root)
        return 0
    if cmd == "setup":
        setup(root, with_deps=args.with_deps, run_network=args.network)
    elif cmd == "doctor":
        doctor(root)
    elif cmd == "test":
        compile_all(root); import_smoke(root); quality_self_test(root); run_smoke_files(root)
    elif cmd == "backup":
        create_backup(root, label="manual")
    elif cmd == "backups":
        list_backups(root)
    elif cmd == "restore":
        candidate = Path(args.zip)
        if not candidate.is_absolute():
            local = backups_path(root) / candidate
            candidate = local if local.exists() else (root / candidate)
        restore_backup(root, candidate.resolve())
    elif cmd == "sites":
        list_sites(root)
    elif cmd == "sync-registry":
        sync_registry(root)
    elif cmd == "run":
        run_app(root)
    elif cmd == "probe":
        print(json.dumps(probe_site(args.url), ensure_ascii=False, indent=2))
    elif cmd == "add-site":
        site = add_site(
            root,
            args.name,
            args.url,
            mode=args.mode,
            enable=args.enable,
            category=args.category,
            currency=args.currency,
            fx_rate_to_eur=args.fx_rate_to_eur,
            search_url=args.search_url,
            items_path=args.items_path,
            title_path=args.title_path,
            price_path=args.price_path,
            url_path=args.url_path,
            image_path=args.image_path,
            price_divisor=args.price_divisor,
        )
        sync_registry(root)
        print(json.dumps(site, ensure_ascii=False, indent=2))
    elif cmd == "enable-site":
        set_site_enabled(root, args.name, True); sync_registry(root)
    elif cmd == "disable-site":
        set_site_enabled(root, args.name, False); sync_registry(root)
    elif cmd == "remove-site":
        remove_site(root, args.name); sync_registry(root)
    elif cmd == "network-test":
        network_test(root, args.query, args.price, args.limit)
    elif cmd == "collect":
        collect_catalogue(
            root,
            seeds=args.seeds,
            price=args.price,
            sources=args.sources,
            dry_run=args.dry_run,
            budget=args.budget,
            stats=args.stats,
            seed_query=args.seed_query,
        )
    else:
        parser.error(f"Commande inconnue : {cmd}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManagerError as exc:
        fail(str(exc))
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nAction annulée.")
        raise SystemExit(130)
