"""Le premier rendu ne doit jamais diversifier des milliers d'offres."""

from unittest.mock import patch

import app_web


def main():
    owner = "performance-owner"
    offers = [
        {
            "titre": f"Balenciaga produit {i}",
            "marketplace": "eBay" if i % 2 else "Vinted",
            "prix": float(100 + i),
            "niveau_identite": "possible",
            "score_identite": 75,
            "lien": f"https://example.test/{i}",
        }
        for i in range(1200)
    ]
    token = app_web._cache_results(
        offers, owner, search_query="Balenciaga", index_total=len(offers)
    )
    seen = []

    def bounded(items, count, diversifie=True):
        seen.append((len(items), count, diversifie))
        return list(items), {}

    with patch.object(app_web, "_selection_diversifiee", side_effect=bounded):
        page = app_web._result_page(token, 0, 50, identity="all", owner=owner)

    assert page["total"] == 1200 and len(page["results"]) == 50, page
    assert seen == [(app_web.DIVERSIFIED_HEAD_SIZE, app_web.DIVERSIFIED_HEAD_SIZE, True)], seen

    mixed = app_web._marketplace_coverage_head(
        [
            *[dict(offers[i], marketplace="eBay") for i in range(100)],
            *[dict(offers[100 + i], marketplace="Vinted") for i in range(10)],
            *[dict(offers[110 + i], marketplace="Grailed") for i in range(10)],
            *[dict(offers[120 + i], marketplace="Zalando") for i in range(10)],
        ],
        head_size=50,
    )
    assert {item["marketplace"] for item in mixed[:50]} == {"eBay", "Vinted", "Grailed", "Zalando"}
    assert len({item["lien"] for item in mixed}) == len(mixed), "offre perdue ou dupliquée"
    print("OK - diversification bornée à 200 offres pour 1 200 résultats.")


if __name__ == "__main__":
    main()
