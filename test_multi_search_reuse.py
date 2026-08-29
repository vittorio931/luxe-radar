"""Régression : A → B → A réutilise le token persistant de A."""

import os
import tempfile
from unittest.mock import patch

os.environ["LUXE_RADAR_SESSIONS_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="lr_multi_reuse_"), "sessions.sqlite3"
)

import app_web  # noqa: E402
import index_engine  # noqa: E402
import search_sessions  # noqa: E402


def offer(query):
    return {
        "titre": f"{query} produit",
        "marketplace": "eBay",
        "prix": 99.0,
        "niveau_identite": "fort",
        "score_identite": 90,
        "score": 90,
        "score_confiance": 90,
        "lien": "https://example.test/" + query.casefold().replace(" ", "-"),
    }


def main():
    assert app_web._search_signature("Louiss Vuitton", "", "Toutes", "", False) == \
        app_web._search_signature("Louis Vuitton", "", "Toutes", "", False)

    client = app_web.app.test_client()
    client.get("/")
    with client.session_transaction() as flask_session:
        csrf = flask_session["csrf_token"]

    calls = []

    def fake_index(query, **_kwargs):
        calls.append(query)
        return index_engine.IndexSearch([offer(query)], 1, 0.0, query.casefold())

    def submit(query):
        response = client.post("/", data={
            "csrf_token": csrf,
            "marque": query,
            "prix": "",
            "plateforme": "Toutes",
        })
        assert response.status_code == 200
        with client.session_transaction() as flask_session:
            return flask_session["lr_search_token"]

    with patch.object(index_engine, "search", side_effect=fake_index), patch.object(
        app_web, "_progressive_source_order", return_value=[]
    ), patch.object(app_web, "_index_spillover", return_value=None):
        nike_token = submit("Nike Trail")
        stone_token = submit("Stone Island")
        reused_token = submit("Nike Trail")

        assert nike_token != stone_token
        assert reused_token == nike_token
        assert calls == ["Nike Trail", "Stone Island"], calls

        with app_web._cache_lock:
            app_web._search_cache.clear()
        restored_token = submit("Nike Trail")
        assert restored_token == nike_token
        assert calls == ["Nike Trail", "Stone Island"], calls

        # Une ancienne session terminée à zéro (par exemple juste avant un OOM
        # Render) doit être supprimée et la recherche réellement relancée.
        empty_query = "Balenciaga"
        empty_signature = app_web._search_signature(empty_query, "", "Toutes", "", False)
        empty_token = "0" * 32
        search_sessions.save_search_session(
            empty_token,
            owner=csrf,
            search_request=empty_query,
            selected_platform="Toutes",
            request_signature=empty_signature,
            state={"results": [], "pending_sources": [], "completed_sources": ["eBay"]},
        )
        with client.session_transaction() as flask_session:
            flask_session["lr_search_token"] = empty_token
            flask_session["lr_search_signature"] = empty_signature
        refreshed_token = submit(empty_query)
        assert refreshed_token != empty_token
        assert calls[-1] == empty_query
        assert search_sessions.load_search_session(empty_token) is None

    record = search_sessions.load_search_session(nike_token)
    assert record and len(record["state"]["results"]) == 1
    print("OK - A -> B -> A et restart réutilisent le token SQLite sans nouveau scan.")


if __name__ == "__main__":
    main()
