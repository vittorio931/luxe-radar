import json
from pathlib import Path
from urllib.parse import urlparse

from app_web import CATALOG_BATCH_SIZE, app
from marketplaces.catalog import get_sites, invalidate_catalog_cache
from marketplaces.connectors import get_available_connectors


REQUIRED = {
    "name", "url", "category", "country", "currency", "enabled", "status",
    "connector_type", "supports_search", "supports_price", "supports_image",
    "supports_reference", "notes",
}
# V3.5 : expansion fashion/retail — le catalogue active les 24 connecteurs
# du registre (les anciennes 7 références historiques ont été élargies).
ACTIVE = {
    "1688", "21RUN", "67behaviour", "ASOS", "AliExpress", "Alltricks",
    "Cdiscount", "Courir", "DHgate", "Deporvillage", "Direct Running",
    "Ekosport", "Footshop", "Grailed", "Hardloop", "JD Sports",
    "MisterRunning", "Running Point", "SSENSE", "Spartoo", "Vinted",
    "Zalando", "eBay", "i-Run",
}


def main():
    path = Path(__file__).parent / "marketplaces" / "sites.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    sites = raw.get("sites") or []
    assert len(sites) >= 1000, len(sites)
    assert all(REQUIRED.issubset(site) for site in sites)
    domains = [(urlparse(site["url"]).hostname or "").lower().removeprefix("www.") for site in sites]
    assert all(domains) and len(domains) == len(set(domains))
    active = {site["name"] for site in sites if site["enabled"] or site["status"] == "active"}
    assert active == ACTIVE, active
    assert all(not site["enabled"] for site in sites if site["name"] not in ACTIVE)
    priority = {"DHgate", "AliExpress", "Alibaba", "GOAT", "StockX", "Depop", "ASOS", "Zalando", "Farfetch", "SSENSE"}
    tested = {site["name"] for site in sites if (site.get("verification") or {}).get("query") == "Nike Trail"}
    assert priority.issubset(tested), priority - tested
    assert all(site.get("verification", {}).get("source") for site in sites if site["name"] not in ACTIVE)

    invalidate_catalog_cache()
    normalized = get_sites()
    assert len(normalized) == len(sites)
    connectors = set(get_available_connectors())
    assert ACTIVE.issubset(connectors), connectors
    client = app.test_client()
    first_response = client.get("/api/catalog?offset=0")
    assert len(first_response.data) < 30_000
    first = first_response.get_json()
    assert len(first["sites"]) == CATALOG_BATCH_SIZE == 50
    assert first["total_catalog"] == len(sites) and first["has_more"] is True
    assert len({site["domain"] for site in first["sites"]}) == CATALOG_BATCH_SIZE
    assert {site["name"] for site in first["sites"] if site["status"] == "active"} == ACTIVE
    assert first["status_counts"]["active"] == len(ACTIVE) and first["categories"]
    second = client.get(f"/api/catalog?offset={CATALOG_BATCH_SIZE}").get_json()
    first_domains = {site["domain"] for site in first["sites"]}
    second_domains = {site["domain"] for site in second["sites"]}
    assert len(second["sites"]) == CATALOG_BATCH_SIZE and first_domains.isdisjoint(second_domains)
    active_page = client.get("/api/catalog?status=active").get_json()
    assert active_page["total"] == len(ACTIVE) and all(site["enabled"] for site in active_page["sites"])
    ebay_page = client.get("/api/catalog?q=ebay").get_json()
    assert ebay_page["total"] >= 1 and any(site["name"] == "eBay" for site in ebay_page["sites"])
    accent_page = client.get("/api/catalog?q=vetements").get_json()
    assert accent_page["total"] >= 700
    assert client.get("/api/catalog?offset=bad").status_code == 400
    assert client.get("/api/catalog?status=made_up").status_code == 400
    print(f"OK - Catalogue massif: {len(sites)} domaines uniques, {len(ACTIVE)} actifs protégés.")


if __name__ == "__main__":
    main()
