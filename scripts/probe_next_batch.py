"""Probe conforme du deuxième lot de dix boutiques."""

import concurrent.futures
import json
from pathlib import Path
from urllib.parse import urljoin

import requests


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "marketplaces" / "sites.json"
OUTPUT = ROOT / ".luxe_radar" / "next_batch_probe.json"
HEADERS = {"User-Agent": "Mozilla/5.0 LUXE-RADAR-Connector-Test/1.0", "Accept": "text/html,application/json"}


def candidates():
    sites = json.loads(CATALOG.read_text(encoding="utf-8"))["sites"]
    return [site for site in sites if site.get("status") == "non_implemented" and not site.get("enabled")][:10]


def probe(site):
    base = site["url"].rstrip("/")
    row = {"name": site["name"], "url": base, "query": "Nike Trail", "price_max_eur": 50}
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        home = session.get(base, timeout=15, allow_redirects=True)
        sample = home.text[:500000].casefold()
        row.update({"home_status": home.status_code, "blocked": home.status_code in {401, 403, 429} or "captcha" in sample or "cf-chl" in sample})
    except requests.RequestException as exc:
        row.update({"error": type(exc).__name__, "result_count": 0})
        return row
    try:
        response = session.get(urljoin(base + "/", "products.json"), params={"limit": 250}, timeout=20)
        products = response.json().get("products", []) if response.status_code == 200 else []
        matches = []
        for product in products:
            title = str(product.get("title") or "")
            if not all(token in title.casefold() for token in ("nike", "trail")):
                continue
            prices = []
            for variant in product.get("variants") or []:
                try:
                    prices.append(float(variant.get("price")))
                except (TypeError, ValueError):
                    pass
            if prices and min(prices) <= 50:
                matches.append({"title": title, "price": min(prices), "handle": product.get("handle")})
        row.update({
            "products_status": response.status_code, "shopify_public": bool(products),
            "catalog_count": len(products), "result_count": len(matches), "results": matches[:10],
        })
    except (requests.RequestException, ValueError) as exc:
        row.update({"shopify_public": False, "result_count": 0, "products_error": type(exc).__name__})
    return row


def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(probe, candidates()))
    OUTPUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
