"""Régression : un token limité à Grailed ne déborde jamais vers eBay."""

from unittest.mock import patch

import app_web
import index_engine


def item(source, number):
    return {
        "titre": f"Nike Trail {source} {number}",
        "marketplace": source,
        "prix": 50 + number,
        "niveau_identite": "fort",
        "score": 80,
        "score_confiance": 80,
        "lien": f"https://example.test/{source}/{number}",
    }


def main():
    owner = "grailed-scope-owner"
    with patch.object(app_web, "_persist_search_session"):
        token = app_web._cache_results(
            [item("Grailed", 1)], owner,
            search_query="Nike Trail", selected_platform="Grailed",
            # Snapshot volontairement périmé : l'index s'enrichit après le token.
            index_total=1,
        )

    calls = []

    def fake_search(_query, **kwargs):
        calls.append(kwargs.get("marketplace"))
        return index_engine.IndexSearch(
            [item("Grailed", 2), item("eBay", 3)], 20, 0.0, "nike trail"
        )

    with patch.object(index_engine, "search", side_effect=fake_search):
        page = app_web._result_page(token, 1, 10, marketplace="Toutes", identity="all", owner=owner)
        forbidden = app_web._result_page(token, 0, 10, marketplace="eBay", identity="all", owner=owner)

    assert calls == ["Grailed"], calls
    assert page["results"] and {row["marketplace"] for row in page["results"]} == {"Grailed"}
    assert forbidden["results"] == [] and forbidden["total"] == 0
    ebay = app_web.get_connector("eBay")
    assert ebay.expansion_page_size == 200 and ebay.max_pages == 10
    print("OK - token Grailed, spillover et filtres API restent strictement Grailed.")


if __name__ == "__main__":
    main()
