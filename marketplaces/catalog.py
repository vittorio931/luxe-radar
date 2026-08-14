"""Catalogue central, validé et chargeable en O(n) pour des milliers de sites."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("sites.json")
VALID_STATUSES = {"active", "off", "to_test", "blocked", "non_implemented"}
VALID_CONNECTOR_TYPES = {"dedicated", "shopify", "json", "playwright", "unimplemented"}


def _normalized_site(raw):
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    url = str(raw.get("base_url") or raw.get("url") or "").strip()
    if not name or not url:
        return None
    status = str(raw.get("status") or ("active" if raw.get("enabled") else "off")).lower()
    if status not in VALID_STATUSES:
        status = "to_test"
    connector_type = str(raw.get("connector_type") or raw.get("mode") or "playwright").lower()
    if connector_type not in VALID_CONNECTOR_TYPES:
        connector_type = "playwright"
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    supports_search = bool(raw.get("supports_search", capabilities.get("search", False)))
    supports_price = bool(raw.get("supports_price", capabilities.get("price", False)))
    supports_image = bool(raw.get("supports_image", capabilities.get("image", False)))
    supports_reference = bool(raw.get("supports_reference", capabilities.get("reference", False)))
    site = dict(raw)
    site.update({
        "name": name,
        "display_name": str(raw.get("display_name") or name),
        "base_url": url.rstrip("/"),
        "url": url.rstrip("/"),
        "category": str(raw.get("category") or "Autres"),
        "status": status,
        "enabled": status == "active" and bool(raw.get("enabled", True)),
        "connector_type": connector_type,
        "mode": str(raw.get("mode") or connector_type),
        "currency": str(raw.get("currency") or "EUR").upper(),
        "country": str(raw.get("country") or ""),
        "supports_search": supports_search,
        "supports_price": supports_price,
        "supports_image": supports_image,
        "supports_reference": supports_reference,
        "capabilities": {
            "search": supports_search,
            "price": supports_price,
            "image": supports_image,
            "reference": supports_reference,
        },
    })
    return site


@lru_cache(maxsize=1)
def load_catalog():
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"sites": []}
    sites = []
    seen = set()
    for raw in data.get("sites", []):
        site = _normalized_site(raw)
        key = site["name"].casefold() if site else ""
        if not site or key in seen:
            continue
        seen.add(key)
        sites.append(site)
    return tuple(sites)


def get_sites(status=None):
    sites = load_catalog()
    if status is None:
        return [dict(site) for site in sites]
    allowed = {status} if isinstance(status, str) else set(status)
    return [dict(site) for site in sites if site["status"] in allowed]


def get_site(name):
    wanted = str(name or "").casefold()
    return next((dict(site) for site in load_catalog() if site["name"].casefold() == wanted), None)


def get_categories():
    groups = {}
    for site in load_catalog():
        groups.setdefault(site["category"], []).append(site["name"])
    return groups


def invalidate_catalog_cache():
    load_catalog.cache_clear()
