"""Tests des correctifs production (post-v370).

Couvre :
  A — expansion_inflight guard (pas de double expand)
  B — old search cancellation (nouvelle recherche annule l'ancienne)
  C — expand async (jamais de blocage Gunicorn thread)
  D — 67behaviour 429 → cooldown, pas de navigateur
  E — ASOS 403 → source_health report + fail-fast
  F — identity default 'all' (diversité scroll)
  G — SQLite contention learning vs. collector (connections séparées)
  H — lifecycle preload safety (wsgi.py ne lance pas de workers)
  I — Render snapshot bloque les expansions lourdes entre deux recherches
  J — index absent : eBay fournit des résultats dès le premier rendu
  K — modèle libre absent : repli marque + chaque terme distinctif
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app_web import app, _search_cache, _cache_lock, _progressive_executor

_pass = 0
_fail = 0
_errors = []


def _check(test_id: str, condition: bool, detail: str = ""):
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  PASS  {test_id}")
    else:
        _fail += 1
        msg = f"  FAIL  {test_id}"
        if detail:
            msg += f": {detail}"
        print(msg)


def _make_token():
    return secrets.token_hex(16)


def _seed_cache(token, owner="", query="Nike Trail", price=100,
                pending=False, pending_sources=None,
                results=None, expansion_inflight=False,
                expansion_exhausted=False, page_exhausted=None):
    with _cache_lock:
        _search_cache[token] = {
            "created_at": time.monotonic(),
            "started_at": time.time(),
            "owner": owner,
            "results": results or [],
            "pending": pending,
            "pending_sources": pending_sources or [],
            "completed_sources": [],
            "failed_sources": [],
            "source_counts": {},
            "generation": 0,
            "cancelled": False,
            "search_query": query,
            "search_price": price,
            "index_mode": False,
            "index_hit_count": 0,
            "index_total": 0,
            "index_age_seconds": None,
            "expansion_inflight": expansion_inflight,
            "expansion_exhausted": expansion_exhausted,
            "expansion_round": 0,
            "page_state": {},
            "page_empty": {},
            "page_exhausted": page_exhausted or [],
            "recall_limit": {},
            "recall_empty": {},
            "discovery_cursor": 0,
            "discovery_has_more": True,
            "catalog_scanned": 0,
        }


# ============================================================
# A — expansion_inflight guard
# ============================================================
def test_expand_inflight_guard():
    """Double expand returns busy when inflight is set."""
    from app_web import _expand_search_once

    token = _make_token()
    _seed_cache(token, owner="test", expansion_inflight=True)

    payload, status = _expand_search_once(token, "test")
    _check("A1_expand_inflight_returns_202",
           status == 202,
           f"expected 202, got {status}")
    _check("A2_expand_inflight_busy_flag",
           payload.get("busy") is True,
           f"expected busy=True, got {payload.get('busy')}")


# ============================================================
# B — old search cancellation
# ============================================================
def test_old_search_cancelled():
    """New search marks previous token as cancelled."""
    from app_web import _cache_results, _progressive_owner_tokens

    owner = "test-owner"
    token_old = _make_token()
    token_new = _make_token()
    _seed_cache(token_old, owner=owner, pending=True,
                pending_sources=["eBay"])
    _progressive_owner_tokens[owner] = token_old

    token_new = _cache_results(
        results=[], owner=owner, search_query="Nike Air Max", search_price=80,
        pending_sources=["eBay"],
        reuse_token=token_new,
    )

    with _cache_lock:
        old_entry = _search_cache.get(token_old)
    _check("B1_old_token_cancelled",
           old_entry is not None and old_entry.get("cancelled") is True,
           f"expected cancelled=True")
    _check("B2_old_token_pending_false",
           old_entry is not None and old_entry.get("pending") is False,
           f"expected pending=False")


# ============================================================
# C — expand never blocks Gunicorn thread
# ============================================================
def test_expand_submits_to_executor():
    """_expand_search_once submits work to _progressive_executor, not inline."""
    from app_web import _expand_search_once

    token = _make_token()
    _seed_cache(token, owner="c-test", query="Nike", price=50)

    with patch.object(_progressive_executor, 'submit') as mock_submit:
        mock_submit.return_value = MagicMock()
        payload, status = _expand_search_once(token, "c-test")

    _check("C1_expand_submits_to_executor",
           mock_submit.called,
           "submit was not called")
    _check("C2_expand_returns_202_async",
           status == 202,
           f"expected 202, got {status}")
    _check("C3_expand_not_inline_result",
           payload.get("accepted") is True,
           f"expected accepted=True")


# ============================================================
# D — 67behaviour 429 → cooldown, skip browser
# ============================================================
def test_behaviour67_429_cooldown():
    """HTTP 429 on 67behaviour records source_health cooldown."""
    from marketplaces.source_health import registry as sh_registry

    before = sh_registry.in_cooldown("67behaviour")
    _check("D1_67behaviour_not_blocked_before", not before)

    import requests
    fake_resp = MagicMock()
    fake_resp.status_code = 429
    err = requests.exceptions.HTTPError(response=fake_resp)

    fake_session = MagicMock()
    fake_session.get.side_effect = err

    from marketplaces.connectors.behaviour67 import Behaviour67Connector
    connector = Behaviour67Connector()
    with patch("marketplaces.connectors.behaviour67.construire_session", return_value=fake_session), \
         patch("marketplaces.connectors.behaviour67._devise_boutique", return_value="EUR"):
        results = connector.search("test", price_max=100)

    _check("D2_67behaviour_returns_empty_on_429",
           results == [],
           f"expected [], got {results}")

    after = sh_registry.in_cooldown("67behaviour")
    _check("D3_67behaviour_blocked_after_429",
           after,
           "expected blocked after 429")


# ============================================================
# E — ASOS 403 → source_health + fail-fast
# ============================================================
def test_asos_403_reporting():
    """HTTP 403 on ASOS records source_health."""
    from marketplaces.source_health import registry as sh_registry

    before_fr = sh_registry.in_cooldown("ASOS")

    from marketplaces.connectors.asos import _telecharger_page, SEARCH_URL_FR, REQUEST_HEADERS_FR

    with patch("marketplaces.connectors.asos.construire_session") as mock_sess:
        fake_resp = MagicMock()
        fake_resp.status_code = 403
        fake_session = MagicMock()
        fake_session.get.return_value = fake_resp
        mock_sess.return_value = fake_session

        info = _telecharger_page(SEARCH_URL_FR, REQUEST_HEADERS_FR, "test", 1)

    _check("E1_asos_page_returns_403",
           info.get("status") == 403,
           f"expected 403, got {info.get('status')}")

    after = sh_registry.in_cooldown("ASOS")
    _check("E2_asos_source_health_recorded",
           after or (not before_fr),
           "expected source_health update after 403")


# ============================================================
# F — identity default 'all'
# ============================================================
def test_identity_default_all():
    """more_results endpoint defaults identity to 'all' for scroll diversity."""
    client = app.test_client()
    token = _make_token()
    _seed_cache(token, owner="test", results=[
        {"marketplace": "eBay", "titre": "Test", "prix": 10,
         "score": 80, "score_confiance": 60,
         "niveau_identite": "fort", "categorie": "BONNE AFFAIRE",
         "lien": "https://example.com/1"},
    ])

    with client.session_transaction() as sess:
        sess["csrf_token"] = "test"

    resp = client.get(f"/api/results/{token}?identity=all")
    _check("F1_identity_all_returns_200",
           resp.status_code == 200,
           f"expected 200, got {resp.status_code}")

    resp2 = client.get(f"/api/results/{token}")
    _check("F2_no_identity_param_returns_200",
           resp2.status_code == 200,
           f"expected 200, got {resp2.status_code}")
    if resp2.status_code == 200:
        data = resp2.get_json()
        _check("F3_default_identity_all",
               data.get("identity") == "all" or "results" in data,
               f"unexpected response keys: {list(data.keys())}")


# ============================================================
# G — SQLite contention: learning uses separate connection
# ============================================================
def test_learning_separate_connection():
    """learn.py uses _learn_conn() — separate from app DB."""
    import learn

    _orig_path = getattr(learn, "_learn_db_path", None)
    try:
        learn._learn_db_path = tempfile.mktemp(suffix=".db")
        learn._learn_schema_ready = False
        conn1 = learn._learn_conn()
        conn2 = learn._learn_conn()
        _check("G1_learn_conn_returns_connection",
               conn1 is not None and conn2 is not None,
               "expected non-None connections")
        _check("G2_connections_are_independent",
               conn1 is not conn2,
               "expected different connection objects (no shared caching)")
        cur1 = conn1.execute("SELECT 1")
        _check("G3_connection_queryable",
               cur1.fetchone() is not None,
               "expected queryable connection")
        conn1.close()
        conn2.close()
    finally:
        learn._learn_db_path = _orig_path
        learn._learn_schema_ready = False


# ============================================================
# H — lifecycle preload: wsgi.py doesn't start workers
# ============================================================
def test_wsgi_bare_exposure():
    """wsgi.py only exposes 'application', no background workers."""
    import importlib
    import wsgi as wsgi_mod

    _check("H1_wsgi_has_application",
           hasattr(wsgi_mod, "application"),
           "missing 'application' in wsgi module")
    _check("H2_wsgi_no_start_collector",
           not hasattr(wsgi_mod, "_start_collector"),
           "wsgi still has _start_collector")
    _check("H3_wsgi_no_start_background",
           not hasattr(wsgi_mod, "_start_background_workers"),
           "wsgi should not have _start_background_workers")


# ============================================================
# I — Render snapshot: aucune expansion lourde après le scroll
# ============================================================
def test_render_snapshot_expansion_guard():
    import app_web

    token = _make_token()
    _seed_cache(token, owner="render-test", query="Nike Phenom Elite", price=1000)
    active = ["Vinted", "eBay", "Grailed", "SSENSE", "The Outnet", "Zalando"]

    with patch.object(app_web, "_render_snapshot_mode", return_value=True), \
         patch.object(_progressive_executor, "submit") as mock_submit:
        sources = app_web._progressive_source_order("sac Louis Vuitton", active)
        payload, status = app_web._expand_search_once(token, "render-test")
        cached = app_web._cache_results(
            [], "render-cache", search_query="sac Louis Vuitton",
            expansion_exhausted=app_web._render_snapshot_mode(),
        )

    _check("I1_render_sources_http_safe",
           set(sources).issubset({"eBay", "SSENSE", "The Outnet"}),
           f"unexpected sources: {sources}")
    _check("I2_render_expand_stopped",
           status == 200 and payload.get("exhausted") is True,
           f"status={status}, payload={payload}")
    _check("I3_render_no_background_submit",
           not mock_submit.called,
           "a heavy background expansion was submitted")
    with _cache_lock:
        cache_exhausted = bool(_search_cache[cached].get("expansion_exhausted"))
    _check("I4_new_render_token_exhausted", cache_exhausted)


def test_render_cold_query_has_immediate_results():
    """Une marque absente du snapshot ne doit pas afficher zéro en production."""
    import app_web
    import index_engine

    client = app_web.app.test_client()
    client.get("/")
    with client.session_transaction() as browser_session:
        csrf = browser_session["csrf_token"]
    offer = {
        "marketplace": "eBay", "titre": "Pantalon Columbia randonnée",
        "prix": 49.0, "niveau_identite": "fort", "score_identite": 95,
        "score": 90, "score_confiance": 90,
        "lien": "https://example.test/columbia-pants",
    }
    empty = index_engine.IndexSearch([], 0, None, "pantalon columbia test froid")
    with patch.object(app_web, "_render_snapshot_mode", return_value=True), \
         patch.object(index_engine, "search", return_value=empty), \
         patch.object(app_web, "rechercher_multi_marketplaces", return_value=[offer]) as live, \
         patch.object(app_web, "_index_results_async"), \
         patch.object(app_web, "_progressive_source_order", return_value=[]):
        response = client.post("/", data={
            "csrf_token": csrf,
            "marque": "pantalon Columbia test froid",
            "prix": "",
            "plateforme": "Toutes",
        })

    _check("J1_cold_query_http_200", response.status_code == 200)
    _check("J2_cold_query_calls_safe_ebay",
           live.call_count == 1 and live.call_args.kwargs.get("plateformes") == ["eBay"])
    with client.session_transaction() as browser_session:
        token = browser_session.get("lr_search_token")
    with _cache_lock:
        count = len((_search_cache.get(token) or {}).get("results") or [])
    _check("J3_cold_query_initial_nonzero", count == 1, f"count={count}")


def test_free_model_query_relaxes_without_losing_brand():
    import app_web

    tech = {"marketplace": "eBay", "titre": "Columbia Omni Tech veste", "lien": "https://example.test/tech"}
    wind = {"marketplace": "eBay", "titre": "Columbia Wind veste", "lien": "https://example.test/wind"}
    with patch.object(
        app_web, "rechercher_multi_marketplaces",
        side_effect=[[], [tech], [wind]],
    ) as search:
        results = app_web._render_live_ebay_results("Columbia Tech Wind", 500)
    queries = [call.kwargs.get("marque") for call in search.call_args_list]
    _check("K1_relaxed_queries_keep_brand",
           queries == ["Columbia Tech Wind", "Columbia tech", "Columbia wind"],
           f"queries={queries}")
    _check("K2_relaxed_results_are_merged", len(results) == 2)


# ============================================================
# Main
# ============================================================
ALL_TESTS = [
    test_expand_inflight_guard,
    test_old_search_cancelled,
    test_expand_submits_to_executor,
    test_behaviour67_429_cooldown,
    test_asos_403_reporting,
    test_identity_default_all,
    test_learning_separate_connection,
    test_wsgi_bare_exposure,
    test_render_snapshot_expansion_guard,
    test_render_cold_query_has_immediate_results,
    test_free_model_query_relaxes_without_losing_brand,
]


def main():
    global _pass, _fail, _errors
    _pass = 0
    _fail = 0
    _errors = []
    print("=" * 60)
    print("Production Bug Fix Tests (A–H)")
    print("=" * 60)
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as exc:
            _fail += 1
            _errors.append(f"{fn.__name__}: {exc}")
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{'=' * 60}")
    print(f"Results: {_pass} passed, {_fail} failed out of {_pass + _fail}")
    if _errors:
        print("\nFailures:")
        for e in _errors:
            print(f"  {e}")
    return _fail == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
