"""Régressions du faux zéro résultat au démarrage de l'index."""

from pathlib import Path
import re
import tempfile
from unittest.mock import patch

import app_web
import index_engine
from search_understanding import understand_query


BENCHMARK_TITLES = {
    "Nike": "Nike sneakers lifestyle",
    "Nike Trail": "Nike Trail chaussure running",
    "Nike P-6000": "Nike P-6000 sneakers",
    "Air Force 1": "Nike Air Force 1 chaussures",
    "Adidas Samba": "Adidas Samba OG chaussures",
    "Salomon XT-6": "Salomon XT-6 trail shoes",
    "Stone Island": "Stone Island veste",
    "casquette Nike": "casquette Nike running",
    "New Balance 2002R": "New Balance 2002R sneakers",
    "Asics Gel Kayano": "Asics Gel Kayano running shoes",
}


def _offers(query="Nike Trail", count=120, start=0):
    title = BENCHMARK_TITLES[query]
    slug = re.sub(r"[^a-z0-9]+", "-", query.casefold()).strip("-")
    return [
        {
            "marketplace": ("eBay", "Vinted", "SSENSE")[i % 3],
            "titre": f"{title} modèle {i}",
            "prix": 40 + (i % 10),
            "prix_total": 40 + (i % 10),
            "devise": "EUR",
            "lien": f"https://example.test/{slug}/{start + i}",
            "niveau_identite": "possible",
            "score_identite": 80,
            "score": 75,
            "score_confiance": 85,
            "risque_contrefacon": "faible",
        }
        for i in range(count)
    ]


def _token(html):
    match = re.search(r'"token":\s*"([0-9a-f]{32})"', html)
    assert match, "token de recherche absent du premier rendu"
    return match.group(1)


def _post(client, csrf, query="Nike Trail"):
    return client.post(
        "/",
        data={
            "csrf_token": csrf,
            "marque": query,
            "prix": "500",
            "plateforme": "Toutes",
        },
    )


def main():
    frontend = Path("static/app.js").read_text(encoding="utf-8")
    assert "history.replaceState({lrPage" not in frontend.replace("window.history.replaceState", "")
    assert "if(progressivePending||refreshPending)setTimeout(pollProgressiveSearch" in frontend
    generic_kayano = understand_query("Asics Gel Kayano")
    assert generic_kayano.canonical == "Asics gel kayano"
    assert generic_kayano.model is None

    active_order_probe = ["Cdiscount", "AliExpress", "eBay", "Vinted", "SSENSE", "Grailed"]
    with patch("app_web.source_health.priority_score", side_effect=lambda _name, base: base):
        assert app_web._progressive_source_order("Nike Phenom Elite", active_order_probe)[0] == "eBay"
        assert app_web._progressive_source_order("Balenciaga pantalon", active_order_probe)[0] == "eBay"

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "index.sqlite3"
        offers = _offers()
        assert index_engine.upsert_results(offers, "Nike Trail", path=db) >= 100
        assert index_engine.upsert_results([{
            "marketplace": "eBay",
            "titre": "Nike Trail pantalon de running Dri-FIT",
            "prix": 69,
            "lien": "https://example.test/nike-trail/pants",
            "niveau_identite": "fort",
            "score_identite": 100,
            "score": 90,
            "score_confiance": 95,
            "devise": "EUR",
        }], "Nike Trail", path=db) == 1
        for position, query in enumerate(BENCHMARK_TITLES):
            if query == "Nike Trail":
                continue
            seeded = _offers(query, count=12, start=(position + 1) * 1000)
            assert index_engine.upsert_results(seeded, query, path=db) == 12

        original_search = index_engine.search

        def search_in_test_db(query, **kwargs):
            kwargs["path"] = db
            return original_search(query, **kwargs)

        # Un index réchauffé par la recherche large Nike Trail contient surtout
        # des chaussures. La recherche précise pantalon doit les éliminer.
        precise = original_search("Nike Trail pantalon", identity="all", limit=50, path=db)
        precise_titles = [str(item.get("titre") or "").casefold() for item in precise.results]
        assert any("pantalon" in title for title in precise_titles)
        assert not any("chaussure" in title or "sneaker" in title for title in precise_titles)

        client = app_web.app.test_client()
        client.get("/")
        with client.session_transaction() as browser_session:
            csrf = browser_session["csrf_token"]

        # Dernière barrière API : même un ancien token pollué ne doit jamais
        # exposer une chaussure ou un haut pour une recherche pantalon.
        polluted = [
            {
                "marketplace": "eBay", "titre": "Nike Pegasus Trail 5 chaussures",
                "prix": 80, "devise": "EUR", "lien": "https://example.test/polluted-shoe",
                "niveau_identite": "fort", "score_identite": 100,
                "score": 90, "score_confiance": 95,
            },
            {
                "marketplace": "eBay", "titre": "Nike Trail Running hoodie",
                "prix": 60, "devise": "EUR", "lien": "https://example.test/polluted-top",
                "niveau_identite": "fort", "score_identite": 100,
                "score": 90, "score_confiance": 95,
            },
            {
                "marketplace": "eBay", "titre": "Nike Trail pantalon Dri-FIT",
                "prix": 70, "devise": "EUR", "lien": "https://example.test/clean-pants",
                "niveau_identite": "fort", "score_identite": 100,
                "score": 90, "score_confiance": 95,
            },
        ]
        polluted_token = app_web._cache_results(
            polluted, csrf, pending_sources=[], search_query="Nike Trail pantalon",
        )
        guarded = client.get(
            f"/api/results/{polluted_token}?offset=0&limit=50&identity=all"
        ).get_json()
        assert guarded["total"] == 1
        assert guarded["results"][0]["titre"] == "Nike Trail pantalon Dri-FIT"

        # Index chaud : chaque famille de requête obligatoire doit créer un
        # token immédiatement exploitable, sans dépendre d'une marketplace.
        with patch.object(index_engine, "search", side_effect=search_in_test_db), \
             patch("app_web._progressive_source_order", return_value=[]):
            # Après navigation sur une URL partageable, le formulaire conserve
            # explicitement POST / ; sinon le navigateur envoyait POST /search
            # et Flask répondait 405 à toute deuxième recherche.
            shared = client.get("/search?q=Nike+Trail")
            shared_html = shared.get_data(as_text=True)
            assert shared.status_code == 200
            assert 'method="post" action="/"' in shared_html
            legacy_second_search = client.post("/search?q=Nike+Trail", data={
                "csrf_token": csrf,
                "marque": "Balenciaga",
                "prix": "500",
                "plateforme": "Toutes",
            })
            assert legacy_second_search.status_code == 200
            assert _token(legacy_second_search.get_data(as_text=True))
            for query in BENCHMARK_TITLES:
                response = _post(client, csrf, query)
                html = response.get_data(as_text=True)
                token = _token(html)
                endpoint = client.get(f"/api/results/{token}?offset=0&limit=50").get_json()
                assert response.status_code == 200, query
                assert len(app_web._search_cache[token]["results"]) > 0, query
                assert len(endpoint["results"]) > 0, query
                if query == "Nike Trail":
                    assert len(app_web._search_cache[token]["results"]) >= 100
                    assert endpoint["total"] >= 100

        # Cold-start : le snapshot index_total reste à zéro, puis l'index reçoit
        # les offres. Offset 0 doit les voir sans nouveau POST utilisateur.
        empty = index_engine.IndexSearch([], 0, None, "nike trail")
        with patch.object(index_engine, "search", return_value=empty), \
             patch("app_web._progressive_source_order", return_value=[]):
            cold_response = _post(client, csrf)
            cold_token = _token(cold_response.get_data(as_text=True))
        assert app_web._search_cache[cold_token]["index_total"] == 0
        with patch.object(index_engine, "search", side_effect=search_in_test_db):
            cold_endpoint = client.get(
                f"/api/results/{cold_token}?offset=0&limit=50"
            ).get_json()
        assert len(cold_endpoint["results"]) > 0
        assert cold_endpoint["total"] >= 100

    print("OK - 10 requêtes universelles, Nike Trail >=100 et cold-start offset 0 non vides.")


if __name__ == "__main__":
    main()
