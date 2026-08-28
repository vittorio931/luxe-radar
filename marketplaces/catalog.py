"""Catalogue central, validé et chargeable en O(n) pour des milliers de sites."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


CATALOG_PATH = Path(__file__).with_name("sites.json")
VALID_STATUSES = {"active", "off", "to_test", "blocked", "non_implemented"}
VALID_CONNECTOR_TYPES = {"dedicated", "shopify", "json", "playwright", "unimplemented"}
VALID_METHODS = {"official_api", "public_feed", "public_json", "public_html", "generic_retail_adapter", "dedicated_connector"}


@dataclass(frozen=True)
class MarketplaceDefinition:
    id: str
    name: str
    domain: str
    country: str
    categories: tuple[str, ...]
    tier: int
    enabled: bool
    method: str
    connector: str
    health_state: str
    cooldown_until: float
    last_success: float | None
    last_failure: float | None
    avg_latency: float | None
    success_rate: float
    results_rate: float
    pagination_support: bool
    max_concurrency: int
    notes: str


def _definition(site) -> MarketplaceDefinition:
    status = str(site.get("status") or "to_test")
    connector_type = str(site.get("connector_type") or "unimplemented")
    method = str(site.get("method") or {
        "dedicated": "dedicated_connector", "shopify": "public_json",
        "json": "public_json", "playwright": "public_html",
    }.get(connector_type, "generic_retail_adapter"))
    if method not in VALID_METHODS:
        method = "generic_retail_adapter"
    category = str(site.get("category") or "Autres")
    tier = site.get("tier")
    if tier not in {1, 2, 3, 4}:
        tier = 1 if status == "active" and connector_type == "dedicated" else 2 if status == "active" else 4
    health_state = str(site.get("health_state") or {
        "active": "HEALTHY", "blocked": "COOLDOWN", "to_test": "EXPERIMENTAL",
        "off": "DISABLED", "non_implemented": "DISABLED",
    }.get(status, "EXPERIMENTAL")).upper()
    return MarketplaceDefinition(
        id=str(site.get("id") or site["name"]).strip().casefold().replace(" ", "-"),
        name=site["name"], domain=str(site.get("domain") or urlparse(site.get("base_url") or "").netloc).casefold().removeprefix("www."), country=str(site.get("country") or ""),
        categories=tuple(str(x) for x in (site.get("categories") or [category]) if str(x)),
        tier=int(tier), enabled=bool(site.get("enabled")), method=method,
        connector=str(site.get("connector") or site.get("connector_type") or ""),
        health_state=health_state, cooldown_until=float(site.get("cooldown_until") or 0),
        last_success=site.get("last_success"), last_failure=site.get("last_failure"),
        avg_latency=site.get("avg_latency"), success_rate=float(site.get("success_rate") or 0),
        results_rate=float(site.get("results_rate") or 0),
        pagination_support=bool(site.get("pagination_support") or site.get("supports_pagination")),
        max_concurrency=max(1, min(int(site.get("max_concurrency") or 1), 4)),
        notes=str(site.get("notes") or ""),
    )


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


def get_definitions(enabled=None):
    definitions = [_definition(site) for site in load_catalog()]
    if enabled is not None:
        definitions = [item for item in definitions if item.enabled is bool(enabled)]
    return definitions


def get_definition(name):
    site = get_site(name)
    return _definition(site) if site else None


def definitions_json(enabled=None):
    return [asdict(item) for item in get_definitions(enabled=enabled)]


def invalidate_catalog_cache():
    load_catalog.cache_clear()
