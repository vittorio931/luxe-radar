"""Enregistre les verdicts du deuxième lot de dix boutiques."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "marketplaces" / "sites.json"
REPORT = ROOT / ".luxe_radar" / "next_batch_probe.json"
SHOPIFY = {"A.EMERY", "Audley Shoes", "Blue Over", "BY FAR", "Carel Paris"}


def main():
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    report = {row["name"]: row for row in json.loads(REPORT.read_text(encoding="utf-8"))}
    for site in data["sites"]:
        row = report.get(site.get("name"))
        if not row:
            continue
        is_shopify = site["name"] in SHOPIFY
        site.update({
            "enabled": False,
            "status": "off" if is_shopify else "non_implemented",
            "connector_type": "shopify" if is_shopify else "unimplemented",
            "supports_search": is_shopify,
            "supports_price": is_shopify,
            "supports_image": is_shopify,
            "supports_reference": False,
            "capabilities": {"search": is_shopify, "price": is_shopify, "image": is_shopify, "reference": False},
            "notes": (
                "Catalogue Shopify public et connecteur générique testés : 0 résultat Nike Trail <= 50 EUR. Reste OFF."
                if is_shopify else
                "Domaine accessible, mais aucun flux produit public compatible détecté ; 0 résultat Nike Trail <= 50 EUR."
            ),
            "verification": {
                "source": site["url"], "query": "Nike Trail", "price_max_eur": 50,
                "result_count": 0, "tested": True, "home_status": row.get("home_status"),
                "products_status": row.get("products_status"),
            },
        })
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
