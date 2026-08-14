"""Réinsère les connecteurs dédiés OFF sans modifier les sites actifs."""

import json
from pathlib import Path
from urllib.parse import urlparse


PATH = Path(__file__).resolve().parents[1] / "marketplaces" / "sites.json"
SITES = [
    {
        "name": "Vestiaire Collective", "url": "https://www.vestiairecollective.com",
        "category": "Luxe", "country": "FR", "currency": "EUR",
        "notes": "Connecteur dédié désactivé ; accès 403 observé. Aucun contournement tenté.",
    },
    {
        "name": "1688", "url": "https://www.1688.com",
        "category": "Grossistes mode", "country": "CN", "currency": "CNY",
        "notes": "Connecteur dédié désactivé et non validé pour une recherche conforme.",
    },
]


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    existing = {(urlparse(site["url"]).hostname or "").lower().removeprefix("www.") for site in data["sites"]}
    for base in SITES:
        domain = (urlparse(base["url"]).hostname or "").lower().removeprefix("www.")
        if domain in existing:
            continue
        site = dict(base)
        site.update({
            "base_url": base["url"], "domain": domain, "enabled": False, "status": "off",
            "connector_type": "dedicated", "supports_search": False, "supports_price": False,
            "supports_image": False, "supports_reference": False,
            "capabilities": {"search": False, "price": False, "image": False, "reference": False},
            "verification": {"source": "LUXE RADAR existing dedicated connector", "http_status": None},
        })
        data["sites"].append(site)
        existing.add(domain)
    for site in data["sites"]:
        capabilities = site.get("capabilities") or {}
        site.setdefault("notes", "Connecteur dédié existant conservé.")
        site.setdefault("country", "")
        site.setdefault("currency", "")
        site.setdefault("supports_search", bool(capabilities.get("search", False)))
        site.setdefault("supports_price", bool(capabilities.get("price", False)))
        site.setdefault("supports_image", bool(capabilities.get("image", False)))
        site.setdefault("supports_reference", bool(capabilities.get("reference", False)))
    data["sites"].sort(key=lambda site: (not site.get("enabled"), site["category"], site["name"].casefold()))
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
