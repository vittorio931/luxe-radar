"""Enregistre le verdict vérifié du premier lot de 10 sites prioritaires."""

import json
from pathlib import Path
from urllib.parse import urlparse


PATH = Path(__file__).resolve().parents[1] / "marketplaces" / "sites.json"

VERDICTS = [
    ("DHgate", "https://www.dhgate.com", "Marketplaces / grossistes", "CN", "USD", "blocked", "unimplemented", "Recherche publique HTTP 403. Aucun contournement tenté."),
    ("AliExpress", "https://www.aliexpress.com", "Marketplaces / grossistes", "CN", "EUR", "non_implemented", "unimplemented", "Page publique accessible, mais aucun ensemble stable titre/prix/image sous 50 EUR normalisable proprement. API officielle nécessitant des accès développeur."),
    ("Alibaba", "https://www.alibaba.com", "Marketplaces / grossistes", "CN", "USD", "blocked", "unimplemented", "Page de recherche renvoyée avec challenge/non exploitable. Open Platform officielle soumise à compte, approbation et clés."),
    ("GOAT", "https://www.goat.com", "Sneakers", "US", "USD", "blocked", "unimplemented", "Page publique challengée/non exploitable et aucune API produit publique officielle utilisable trouvée."),
    ("StockX", "https://stockx.com", "Sneakers", "US", "USD", "blocked", "unimplemented", "Recherche publique HTTP 403. API officielle disponible seulement après compte développeur approuvé, clé et authentification."),
    ("Depop", "https://www.depop.com", "Seconde main", "GB", "GBP", "to_test", "unimplemented", "Page publique parfois accessible mais structure de résultats instable entre requêtes ; aucun résultat normalisé fiable obtenu."),
    ("ASOS", "https://www.asos.com", "Retailers multimarques", "GB", "GBP", "off", "dedicated", "Connecteur dédié créé mais test headless Nike Trail <= 50 EUR : 0 résultat, cartes non rendues. Reste OFF."),
    ("Zalando", "https://www.zalando.fr", "Retailers multimarques", "DE", "EUR", "to_test", "unimplemented", "Requête HTTP expirée et rendu navigateur vide ; API partenaire officielle destinée à l'intégration vendeurs, pas une recherche catalogue publique validée."),
    ("Farfetch", "https://www.farfetch.com", "Luxe / marketplace", "GB", "EUR", "blocked", "unimplemented", "Recherche publique HTTP 403. Aucun contournement tenté."),
    ("SSENSE", "https://www.ssense.com", "Luxe / retailer", "CA", "EUR", "off", "unimplemented", "Page publique normalisable : 7 produits Nike Trail observés, mais tous au-dessus de 50 EUR ; test demandé retourne 0 résultat exploitable."),
]


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    by_domain = {(urlparse(site["url"]).hostname or "").lower().removeprefix("www."): site for site in data["sites"]}
    for name, url, category, country, currency, status, connector_type, notes in VERDICTS:
        domain = (urlparse(url).hostname or "").lower().removeprefix("www.")
        site = by_domain.get(domain, {})
        site.update({
            "name": name, "url": url, "base_url": url, "domain": domain,
            "category": category, "country": country, "currency": currency,
            "enabled": False, "status": status, "connector_type": connector_type,
            "supports_search": False, "supports_price": False,
            "supports_image": False, "supports_reference": False,
            "capabilities": {"search": False, "price": False, "image": False, "reference": False},
            "notes": notes,
            "verification": {
                "source": url, "query": "Nike Trail", "price_max_eur": 50,
                "result_count": 0, "tested": True,
            },
        })
        if domain not in by_domain:
            data["sites"].append(site)
            by_domain[domain] = site
    data["sites"].sort(key=lambda site: (not site.get("enabled"), site["category"], site["name"].casefold()))
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
