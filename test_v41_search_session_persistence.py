"""Test V4.1 : persistance des sessions de recherche (hotfix restart Gunicorn).

Scénarios hors-ligne (recherche simulée, un seul worker eBay) :
- un token vit dans SQLite avec résultats + curseurs ;
- un "redémarrage" (cache RAM vidé) ne produit plus de 404 sur status,
  more_results ni expand : l'état est restauré ;
- les sources déjà terminées ne sont jamais re-frappées ;
- un autre propriétaire (autre session CSRF) reste exclu (404) ;
- une session expirée est purgée (404 + suppression SQLite) ;
- le frontend embarque la récupération auto et le throttling health.
"""
import os
import sqlite3
import tempfile
import threading
import time

from unittest.mock import patch

_DB = os.path.join(tempfile.mkdtemp(prefix="lr_sessions_"), "sessions.sqlite3")
os.environ["LUXE_RADAR_SESSIONS_DB"] = _DB

from pathlib import Path  # noqa: E402

import search_sessions  # noqa: E402

import app_web  # noqa: E402
from app_web import (  # noqa: E402
    CACHE_TTL_SECONDS,
    SEARCH_SESSION_TTL_SECONDS,
    _cache_results,
    _ensure_search_session,
    _progressive_owner_tokens,
    _search_cache,
    app,
)


def sample_results(count=170):
    marketplaces = ["eBay"] * 120 + ["Vinted"] * 20 + ["Grailed"] * 15 + ["67behaviour"] * 15
    return [
        {
            "marketplace": marketplaces[i], "titre": f"Annonce {i}",
            "prix": (i % 80) + 1, "score": 100 - (i / 2),
            "score_confiance": 50 + (i % 45), "categorie": "BONNE AFFAIRE",
            "niveau_identite": "fort",
            "lien": f"https://example.com/{i}",
        }
        for i in range(count)
    ]


def fake_search(**kwargs):
    """Page 1 = catalogue complet ; pages suivantes = nouvelles annonces."""
    page = int(kwargs.get("page", 1) or 1)
    if page <= 1:
        return sample_results()
    source = (kwargs.get("plateformes") or ["x"])[0]
    start = 50 * (page - 1)
    return [
        dict(item, titre=f"Vague {page} - Annonce {i}", score=max(1, 60 - i),
             marketplace=source,
             lien=f"https://example.com/{source}/wave{page}-{i}")
        for i, item in enumerate(sample_results()[start:start + 50])
    ]


def _boot(client):
    client.get("/")
    with client.session_transaction() as session:
        return session["csrf_token"]


def _post_search(client, csrf, marque="Nike Trail", prix="50", plateforme="Toutes"):
    resp = client.post("/", data={
        "csrf_token": csrf, "marque": marque, "prix": prix,
        "plateforme": plateforme,
    })
    assert resp.status_code == 200, resp.status_code
    with client.session_transaction() as session:
        return session["lr_search_token"]


def _wait_done(client, token, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/results/{token}/status").get_json()
        if status and not status["pending"]:
            return status
        time.sleep(0.15)
    raise AssertionError("recherche non stabilisée (pending)")


def _wait_expansion_idle(token, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with app_web._cache_lock:
            entry = _search_cache.get(token)
            if entry is not None and not entry.get("expansion_inflight"):
                return dict(entry)
        time.sleep(0.05)
    raise AssertionError("expansion asynchrone non stabilisée")


def _simulate_restart():
    with app_web._cache_lock:
        _search_cache.clear()
    _progressive_owner_tokens.clear()


def main():
    assert str(search_sessions.default_db_path()) == str(Path(_DB).resolve())
    search_sessions.drop_sessions()

    client = app.test_client()
    csrf = _boot(client)

    # Page initiale vide : le scroll profond doit attendre la fin de la page 1
    # au lieu de lancer eBay page 2 en parallèle du pipeline initial.
    guard_token = _cache_results(
        [], csrf, pending_sources=["eBay"], search_query="Nike P-6000",
        search_price=250, selected_platform="Toutes",
    )
    guard = client.post(
        f"/api/results/{guard_token}/expand?marketplace=Toutes",
        headers={"X-CSRF-Token": csrf},
    )
    assert guard.status_code == 202, guard.get_data(as_text=True)[:300]
    assert guard.get_json()["busy"] is True and guard.get_json()["added"] == 0
    search_sessions.delete_search_session(guard_token)
    with app_web._cache_lock:
        _search_cache.pop(guard_token, None)
    _progressive_owner_tokens.pop(csrf, None)

    with patch(
        "app_web.rechercher_multi_marketplaces", side_effect=fake_search,
    ) as mocked, patch(
        "app_web._progressive_source_order", return_value=["eBay"],
    ), patch("app_web._index_results_async"):
        # --- 1. round-trip SQLite -----------------------------------------
        token = _post_search(client, csrf)
        _wait_done(client, token)
        assert mocked.call_count == 1

        record = search_sessions.load_search_session(token)
        assert record is not None, "session non persistée en SQLite"
        assert record["search_request"] == "Nike Trail"
        assert record["owner"] == csrf
        assert record["selected_platform"] == "Toutes"
        assert len(record["state"]["results"]) >= 100

        # --- 2. restart : status + more_results ne renvoient plus 404 -------
        _simulate_restart()
        before_restore_calls = mocked.call_count
        st = client.get(f"/api/results/{token}/status")
        assert st.status_code == 200, st.get_data(as_text=True)[:300]
        assert "pending" in st.get_json()

        page = client.get(f"/api/results/{token}?offset=0&limit=50")
        assert page.status_code == 200, page.get_data(as_text=True)[:300]
        data = page.get_json()
        assert data["total"] >= 100 and len(data["results"]) >= 50
        assert mocked.call_count == before_restore_calls, \
            "le restore a re-frappé des sources déjà terminées"

        # --- 3. restart : expand continue la pagination ---------------------
        r1 = client.post(f"/api/results/{token}/expand", headers={"X-CSRF-Token": csrf})
        assert r1.status_code in {200, 202}, r1.get_data(as_text=True)[:300]
        after_r1 = _wait_expansion_idle(token)
        assert int(after_r1.get("page_state", {}).get("eBay", 0)) == 2
        assert len(after_r1.get("results") or []) >= 170

        _simulate_restart()
        r2 = client.post(f"/api/results/{token}/expand", headers={"X-CSRF-Token": csrf})
        assert r2.status_code in {200, 202}, "expand après restart : 404 inattendu"
        _wait_expansion_idle(token)

        with app_web._cache_lock:
            entry = _search_cache.get(token)
            assert entry is not None
            pages = entry.get("page_state", {})
            assert int(pages.get("eBay", 0)) == 2
            assert int(pages.get("Vinted", 0)) == 2

        # --- 3b. éviction RAM (entrée expirée) ne détruit PAS la ligne SQLite
        with app_web._cache_lock:
            _search_cache[token]["created_at"] -= CACHE_TTL_SECONDS + 60
        alive = client.get(f"/api/results/{token}/status")
        assert alive.status_code == 200, "session encore valide perdue lors de l'éviction RAM"
        assert search_sessions.load_search_session(token) is not None

        # --- 3c. deux requêtes concurrentes attendent le même restore -------
        _simulate_restart()
        real_load = search_sessions.load_search_session
        gate = threading.Barrier(2)
        restored = []

        def restore_worker():
            gate.wait(timeout=2)
            restored.append(_ensure_search_session(token, csrf, ["eBay"]))

        def slow_load(value):
            time.sleep(0.15)
            return real_load(value)

        with patch.object(search_sessions, "load_search_session", side_effect=slow_load):
            threads = [threading.Thread(target=restore_worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
            assert all(not thread.is_alive() for thread in threads), "restore concurrent bloqué"
        assert len(restored) == 2 and all(item is not None for item in restored), \
            "une requête concurrente a reçu un faux 404 pendant le restore"

        # --- 4. autre propriétaire -> 404 ----------------------------------
        other = app.test_client()
        other_csrf = _boot(other)
        assert other_csrf != csrf
        _simulate_restart()
        hidden = other.get(f"/api/results/{token}/status")
        assert hidden.status_code == 404
        assert search_sessions.load_search_session(token) is not None

        # --- 5. session expirée -> 404 + purge SQLite -----------------------
        conn = sqlite3.connect(_DB)
        try:
            conn.execute(
                "UPDATE search_sessions SET updated_at = ? WHERE token = ?",
                (time.time() - SEARCH_SESSION_TTL_SECONDS - 60, token),
            )
            conn.commit()
        finally:
            conn.close()
        _simulate_restart()
        expired = client.get(f"/api/results/{token}/status")
        assert expired.status_code == 404
        assert search_sessions.load_search_session(token) is None, "session expirée non purgée"

        search_sessions.delete_expired(SEARCH_SESSION_TTL_SECONDS)
        assert search_sessions.count_sessions() == 0

    # --- 6. frontend : recovery + throttling --------------------------------
    with open("static/app.js", encoding="utf-8") as handle:
        js = handle.read()
    assert "recoverSearchSession" in js
    assert "INVALID_TOKEN" in js
    assert "30000" in js, "garde anti-boucle 30 s manquante"
    assert "sourcesHealthFetchedAt" in js and "60000" in js, "throttle health 60 s manquant"
    assert js.count("recoverSearchSession()") >= 4, "recovery non branché sur tous les endpoints"

    print("OK - V4.1 persistance des sessions (restart sans 404, owner, TTL, frontend) validée.")


if __name__ == "__main__":
    main()
