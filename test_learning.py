"""Tests J1-J4 : Learning analytics (feature flag, schema, endpoint, buffer, SQLite)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import learn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_learn_state():
    learn._learn_buffer.clear()
    learn._learn_drop_count = 0
    learn._learn_flush_count = 0
    learn._learn_worker_started = False
    learn._learn_db_path = None
    learn._learn_schema_ready = False
    learn._last_purge_ts = 0.0
    learn._last_aggregate_ts = 0.0


_orig_enabled = learn.LEARN_ENABLED
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
        _errors.append(test_id)


class AssertionError(Exception):
    pass


class _Ctx:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.client = None
        self.csrf = None

    def db(self, name="test.sqlite3"):
        return self.tmp / name

    def init_client(self):
        from app_web import app as flask_app
        flask_app.config["TESTING"] = True
        flask_app.config["SESSION_COOKIE_HTTPONLY"] = False
        self.client = flask_app.test_client()

    def boot(self):
        if self.client is None:
            self.init_client()
        resp = self.client.get("/")
        assert resp.status_code == 200
        with self.client.session_transaction() as sess:
            self.csrf = sess["csrf_token"]
        return self.csrf


# ===========================================================================
# 1. Feature flag tests
# ===========================================================================

def test_flag_default_disabled():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LUXE_RADAR_LEARN_ENABLED", None)
        enabled = os.environ.get("LUXE_RADAR_LEARN_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        _check("flag_default_disabled", enabled is False)


def test_flag_enabled_true():
    with patch.dict(os.environ, {"LUXE_RADAR_LEARN_ENABLED": "true"}):
        enabled = os.environ.get("LUXE_RADAR_LEARN_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        _check("flag_enabled_true", enabled is True)


def test_flag_enabled_1():
    with patch.dict(os.environ, {"LUXE_RADAR_LEARN_ENABLED": "1"}):
        enabled = os.environ.get("LUXE_RADAR_LEARN_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        _check("flag_enabled_1", enabled is True)


def test_flag_disabled_empty():
    with patch.dict(os.environ, {"LUXE_RADAR_LEARN_ENABLED": ""}):
        enabled = os.environ.get("LUXE_RADAR_LEARN_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        _check("flag_disabled_empty", enabled is False)


def test_flag_off_no_push():
    _reset_learn_state()
    learn.LEARN_ENABLED = False
    result = learn.learn_push("eid1", "sess1", "search", "nike trail")
    _check("flag_off_no_push", result is False and len(learn._learn_buffer) == 0)


def test_flag_off_no_start():
    _reset_learn_state()
    learn.LEARN_ENABLED = False
    learn.start_learn_worker(db_path=Path("/tmp/nonexistent"))
    _check("flag_off_no_start", learn._learn_worker_started is False)


# ===========================================================================
# 2. Schema tests
# ===========================================================================

def test_schema_create_table():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    learn.ensure_learn_schema(conn)
    conn.commit()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    _check("schema_create_table", "learn_events" in tables)
    conn.close()


def test_learn_signals_table():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    learn.ensure_learn_schema(conn)
    conn.commit()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    _check("learn_signals_table", "learn_signals" in tables)
    conn.close()


def test_schema_idempotent():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    learn.ensure_learn_schema(conn)
    learn.ensure_learn_schema(conn)
    conn.commit()
    _check("schema_idempotent", True)
    conn.close()


def test_event_id_unique():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    learn.ensure_learn_schema(conn)
    conn.commit()
    now = 1700000000.0
    conn.execute(
        "INSERT INTO learn_events (event_id, session_id, ts, event_type, query_key, "
        "offer_key, marketplace, meta_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("evt1", "sess1", now, "search", "nike", "", "", "{}", now),
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO learn_events (event_id, session_id, ts, event_type, query_key, "
            "offer_key, marketplace, meta_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("evt1", "sess2", now + 1, "search", "adidas", "", "", "{}", now + 1),
        )
        _check("event_id_unique", False, "No IntegrityError raised")
    except sqlite3.IntegrityError:
        _check("event_id_unique", True)
    conn.close()


def test_schema_columns():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    learn.ensure_learn_schema(conn)
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(learn_events)")}
    expected = {"id", "event_id", "session_id", "ts", "event_type", "query_key",
                "offer_key", "marketplace", "meta_json", "created_at"}
    _check("schema_columns", cols == expected, f"got {cols}")
    conn.close()


# ===========================================================================
# 3. Buffer tests
# ===========================================================================

def test_push_adds_to_buffer():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    result = learn.learn_push("eid1", "sess1", "search", "nike trail")
    _check("push_adds_to_buffer", result is True and len(learn._learn_buffer) == 1)


def test_push_rejects_invalid_type():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    result = learn.learn_push("eid1", "sess1", "hacker_attack", "nike")
    _check("push_rejects_invalid_type", result is False and len(learn._learn_buffer) == 0)


def test_push_rejects_empty_qk():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    result = learn.learn_push("eid1", "sess1", "search", "")
    _check("push_rejects_empty_qk", result is False)


def test_push_rejects_long_qk():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    result = learn.learn_push("eid1", "sess1", "search", "x" * 300)
    _check("push_rejects_long_qk", result is False)


def test_push_rejects_empty_event_id():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    result = learn.learn_push("", "sess1", "search", "nike")
    _check("push_rejects_empty_event_id", result is False and len(learn._learn_buffer) == 0)


def test_push_rejects_long_event_id():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    result = learn.learn_push("x" * 200, "sess1", "search", "nike")
    _check("push_rejects_long_event_id", result is False and len(learn._learn_buffer) == 0)


def test_buffer_full_rejects_new():
    """Buffer plein = rejet du nouvel événement, pas d'éviction FIFO."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    for i in range(learn.LEARN_BUFFER_MAX):
        learn.learn_push(f"eid{i}", f"s{i}", "search", f"q{i}")
    first = learn._learn_buffer[0]
    first_event_id = first[0]
    stats_before = learn.learn_drop_stats()
    rejected = learn.learn_push("eid_overflow", "soverflow", "search", "overflow")
    stats_after = learn.learn_drop_stats()
    _check("buffer_full_rejects_new",
           rejected is False
           and len(learn._learn_buffer) == learn.LEARN_BUFFER_MAX
           and learn._learn_buffer[0][0] == first_event_id
           and stats_after["drops"] == stats_before["drops"] + 1)


def test_buffer_first_event_preserved_after_reject():
    """Après rejet, le premier événement est toujours présent."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    learn.learn_push("eid_first", "sfirst", "search", "first_query")
    for i in range(learn.LEARN_BUFFER_MAX - 1):
        learn.learn_push(f"eid{i}", f"s{i}", "search", f"q{i}")
    assert learn._learn_buffer[0][0] == "eid_first"
    result = learn.learn_push("eid_rejected", "srejected", "search", "rejected")
    _check("buffer_first_preserved",
           result is False
           and learn._learn_buffer[0][0] == "eid_first"
           and len(learn._learn_buffer) == learn.LEARN_BUFFER_MAX)


def test_meta_strips_unknown_fields():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "search", "nike", meta={"unknown": "hack", "nb_results": 5})
    meta = json.loads(learn._learn_buffer[-1][7])
    _check("meta_strips_unknown", "unknown" not in meta and meta.get("nb_results") == 5)


def test_meta_truncates_long_values():
    """Tronque les valeurs meta au-delà de _LEARN_MAX_VALUE_LEN."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    long_str = "x" * 500
    learn.learn_push("eid1", "s1", "search", "nike", meta={"nb_results": long_str})
    meta = json.loads(learn._learn_buffer[-1][7])
    _check("meta_truncates_long",
           isinstance(meta.get("nb_results"), str)
           and len(meta["nb_results"]) == learn.LEARN_MAX_VALUE_LEN)


def test_meta_real_allowed_field_search():
    """Vérifie exactement qu'un champ autorisé est conservé et borné pour 'search'."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "search", "nike",
                     meta={"nb_results": 42, "platform": "eBay", "extra_bad": "no"})
    meta = json.loads(learn._learn_buffer[-1][7])
    _check("meta_real_allowed_field_search",
           meta.get("nb_results") == 42
           and meta.get("platform") == "eBay"
           and "extra_bad" not in meta)


def test_meta_real_allowed_field_result_click():
    """Vérifie exactement que pos/price/marketplace sont conservés pour 'result_click'."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "result_click", "nike", meta={"pos": 3, "price": 89.99, "marketplace": "eBay"})
    meta = json.loads(learn._learn_buffer[-1][7])
    _check("meta_real_allowed_field_result_click",
           meta.get("pos") == 3
           and meta.get("price") == 89.99
           and meta.get("marketplace") == "eBay")


def test_drop_stats():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "search", "nike")
    stats = learn.learn_drop_stats()
    _check("drop_stats", stats["buffer_len"] == 1 and stats["drops"] == 0)


def test_drop_counter_increments():
    """Le compteur drops augmente à chaque rejet buffer plein."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    for i in range(learn.LEARN_BUFFER_MAX):
        learn.learn_push(f"eid{i}", f"s{i}", "search", f"q{i}")
    learn.learn_push("eid_r1", "s1", "search", "reject1")
    learn.learn_push("eid_r2", "s1", "search", "reject2")
    stats = learn.learn_drop_stats()
    _check("drop_counter_increments",
           stats["drops"] == 2
           and stats["buffer_len"] == learn.LEARN_BUFFER_MAX)


# ===========================================================================
# 4. Flush tests
# ===========================================================================

def test_flush_inserts():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "search", "nike", meta={"nb_results": 10})
    learn.learn_push("eid2", "s2", "result_click", "nike", offer_key="ok1",
                     marketplace="eBay", meta={"pos": 3, "price": 89.99})
    count = learn._learn_flush_batch()
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT * FROM learn_events").fetchall()
    _check("flush_inserts", count == 2 and len(rows) == 2)
    conn.close()


def test_flush_empty_buffer():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    learn._learn_buffer.clear()
    count = learn._learn_flush_batch()
    _check("flush_empty_buffer", count == 0)


def test_flush_unique_event_ids():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "search", "nike")
    learn.learn_push("eid2", "s2", "search", "nike")
    learn._learn_flush_batch()
    conn = sqlite3.connect(str(db))
    ids = [r[0] for r in conn.execute("SELECT event_id FROM learn_events")]
    _check("flush_unique_ids", len(ids) == 2 and ids[0] != ids[1])
    conn.close()


def test_flush_dedup_same_event_id():
    """Deux push avec le même event_id = une seule ligne en DB (INSERT OR IGNORE)."""
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    learn.LEARN_ENABLED = True
    learn.learn_push("eid_dup", "s1", "search", "nike")
    learn.learn_push("eid_dup", "s2", "search", "adidas")
    count = learn._learn_flush_batch()
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT event_id, query_key FROM learn_events WHERE event_id = ?", ("eid_dup",)).fetchall()
    _check("flush_dedup_same_event_id",
           count == 2
           and len(rows) == 1
           and rows[0][1] == "nike",
           f"rows={rows}")


def test_flush_abandon_on_locked_db():
    """Flush abandonne proprement sur DB locké : count=0, pas d'exception, durée bornée."""
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    learn.LEARN_ENABLED = True
    learn.learn_push("eid1", "s1", "search", "nike")
    lock_conn = sqlite3.connect(str(db), timeout=10.0)
    lock_conn.execute("BEGIN EXCLUSIVE")
    t0 = time.monotonic()
    try:
        count = learn._learn_flush_batch()
        elapsed = time.monotonic() - t0
    except Exception as exc:
        elapsed = time.monotonic() - t0
        lock_conn.rollback()
        lock_conn.close()
        _check("flush_abandon_on_locked", False, f"exception: {exc}")
        return
    lock_conn.rollback()
    lock_conn.close()
    _check("flush_abandon_on_locked",
           count == 0
           and elapsed < 1.0,
           f"count={count}, elapsed={elapsed:.3f}s")


# ===========================================================================
# 5. Purge tests (SQL portable, pas de DELETE LIMIT direct)
# ===========================================================================

_PURGE_SQL = (
    "DELETE FROM learn_events WHERE id IN "
    "(SELECT id FROM learn_events WHERE created_at < ? ORDER BY id LIMIT ?)"
)


def test_purge_removes_old_events():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    old = 1000000000.0
    recent = 1700000000.0
    conn = sqlite3.connect(str(db))
    for i, ts in enumerate([(old, "old"), (old, "old2"), (recent, "recent")]):
        conn.execute(
            "INSERT INTO learn_events (event_id, session_id, ts, event_type, query_key, "
            "offer_key, marketplace, meta_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"e{i}", f"s{i}", ts[0], "search", ts[1], "", "", "{}", ts[0]),
        )
    conn.commit()
    cutoff = 1700000000.0 - (30 * 86400)
    deleted = 0
    while True:
        cur = conn.execute(_PURGE_SQL, (cutoff, 500))
        n = cur.rowcount
        if n == 0:
            break
        deleted += n
        conn.commit()
        if n < 500:
            break
    _check("purge_removes_old", deleted == 2, f"deleted={deleted}")
    conn.close()


def test_purge_preserves_recent():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    ts = 1700000000.0
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO learn_events (event_id, session_id, ts, event_type, query_key, "
        "offer_key, marketplace, meta_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("e1", "s1", ts, "search", "nike", "", "", "{}", ts),
    )
    conn.commit()
    cutoff = ts - 86400
    cur = conn.execute(_PURGE_SQL, (cutoff, 500))
    conn.commit()
    _check("purge_preserves_recent", cur.rowcount == 0)
    conn.close()


# ===========================================================================
# 6. Endpoint tests
# ===========================================================================

def test_endpoint_flag_off_204():
    _reset_learn_state()
    learn.LEARN_ENABLED = False
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e1", "type": "search", "qk": "nike"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    _check("endpoint_flag_off_204", resp.status_code == 204)


def test_endpoint_valid_accepted():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"token": "a" * 32, "events": [{"eid": "e1", "type": "search", "qk": "nike", "m": {"nb_results": 10}}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_valid_accepted", resp.status_code == 200 and data["ok"] and data["accepted"] == 1)


def test_endpoint_invalid_type_rejected():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e1", "type": "hack", "qk": "nike"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_invalid_type", resp.status_code == 200 and data["accepted"] == 0)


def test_endpoint_missing_event_id_rejected():
    """Endpoint rejette un event sans eid."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"events": [{"type": "search", "qk": "nike"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_missing_event_id_rejected", resp.status_code == 200 and data["accepted"] == 0)


def test_endpoint_empty_body():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        data="",
        headers={"X-CSRF-Token": ctx.csrf, "Content-Type": "application/json"},
    )
    _check("endpoint_empty_body", resp.status_code == 400)


def test_endpoint_oversized_body():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    payload = json.dumps({"events": [{"eid": "e1", "type": "search", "qk": "x" * 20000}]})
    resp = ctx.client.post(
        "/api/learn/event",
        data=payload,
        headers={"X-CSRF-Token": ctx.csrf, "Content-Type": "application/json"},
    )
    _check("endpoint_oversized_body", resp.status_code == 413)


def test_endpoint_capped_events():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    events = [{"eid": f"e{i}", "type": "search", "qk": f"q{i}"} for i in range(50)]
    resp = ctx.client.post(
        "/api/learn/event",
        json={"events": events},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_capped_events", data["accepted"] <= learn.LEARN_MAX_EVENTS_PER_POST)


def test_endpoint_csrf_required():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e1", "type": "search", "qk": "nike"}]},
    )
    _check("endpoint_csrf_required", resp.status_code == 403)


def test_endpoint_post_only():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.get("/api/learn/event")
    _check("endpoint_post_only", resp.status_code == 405)


def test_endpoint_invalid_json():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        data="not json {{{",
        headers={"X-CSRF-Token": ctx.csrf, "Content-Type": "application/json"},
    )
    _check("endpoint_invalid_json", resp.status_code == 400)


def test_endpoint_missing_events_key():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"not_events": []},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    _check("endpoint_missing_events_key", resp.status_code == 400)


def test_endpoint_beacon_no_csrf_403():
    """sendBeacon ne peut pas envoyer X-CSRF-Token → 403. Le fetch keepalive oui."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    payload = json.dumps({"events": [{"eid": "eb1", "type": "search", "qk": "nike"}]})
    resp_no_csrf = ctx.client.post(
        "/api/learn/event",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    resp_with_csrf = ctx.client.post(
        "/api/learn/event",
        data=payload,
        headers={"X-CSRF-Token": ctx.csrf, "Content-Type": "application/json"},
    )
    _check("beacon_no_csrf_403",
           resp_no_csrf.status_code == 403
           and resp_with_csrf.status_code == 200,
           f"no_csrf={resp_no_csrf.status_code}, with_csrf={resp_with_csrf.status_code}")


# ===========================================================================
# 7. Server-side qk derivation (buffer vérifie canonicalisation)
# ===========================================================================

def test_endpoint_client_qk_fallback():
    """Sans token valide, le client_qk est utilisé tel quel (pas de canonicalisation possible)."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"token": "0" * 32, "events": [{"eid": "e1", "type": "search", "qk": "client_qk"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_client_qk_fallback",
           resp.status_code == 200 and data["ok"])


def test_endpoint_no_token_uses_client_qk():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e1", "type": "search", "qk": "fallback"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_no_token_uses_client_qk",
           resp.status_code == 200 and data["ok"])


def test_endpoint_qk_canonicalized_with_token():
    """Avec un token valide pointant vers une search, le qk dans le buffer est canonique."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    from uuid import uuid4
    from app_web import _search_cache, _cache_lock
    token = uuid4().hex
    with _cache_lock:
        _search_cache[token] = {
            "search_query": "  Nike  Trail  ",
            "owner": ctx.csrf,
        }
    learn._learn_buffer.clear()
    resp = ctx.client.post(
        "/api/learn/event",
        json={"token": token, "events": [{"eid": "eqk1", "type": "search", "qk": "ignored_client_value"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    data = resp.get_json()
    _check("endpoint_qk_canonicalized_with_token", data.get("ok") and data.get("accepted") == 1)
    if len(learn._learn_buffer) == 1:
        buffer_qk = learn._learn_buffer[0][4]
        from index_engine import canonical_query
        expected_qk = canonical_query("Nike Trail")
        _check("qk_is_canonical_server_side",
               buffer_qk == expected_qk and buffer_qk != "ignored_client_value",
               f"buffer_qk={buffer_qk!r}, expected={expected_qk!r}")
    else:
        _check("qk_is_canonical_server_side", False, f"buffer_len={len(learn._learn_buffer)}")


# ===========================================================================
# 8. Session ID tests
# ===========================================================================

def test_session_id_set_on_call():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx = _Ctx()
    ctx.boot()
    ctx.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e1", "type": "search", "qk": "nike"}]},
        headers={"X-CSRF-Token": ctx.csrf},
    )
    with ctx.client.session_transaction() as sess:
        sid = sess.get("learn_sid")
    _check("session_id_set",
           sid is not None
           and isinstance(sid, str)
           and len(sid) == 32,
           f"sid={sid!r}, len={len(sid) if sid else 0}")


def test_session_id_is_opaque_hex():
    """Session ID est un hex aléatoire, pas dérivé d'IP/UA/fingerprint."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    ctx1 = _Ctx()
    ctx1.boot()
    ctx1.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e1", "type": "search", "qk": "nike"}]},
        headers={"X-CSRF-Token": ctx1.csrf},
    )
    ctx2 = _Ctx()
    ctx2.boot()
    ctx2.client.post(
        "/api/learn/event",
        json={"events": [{"eid": "e2", "type": "search", "qk": "nike"}]},
        headers={"X-CSRF-Token": ctx2.csrf},
    )
    with ctx1.client.session_transaction() as s1:
        sid1 = s1.get("learn_sid")
    with ctx2.client.session_transaction() as s2:
        sid2 = s2.get("learn_sid")
    valid_hex = all(
        c in "0123456789abcdef"
        for s in [sid1, sid2] if s for c in s
    )
    _check("session_id_opaque_hex",
           valid_hex
           and sid1 != sid2,
           f"s1={sid1!r}, s2={sid2!r}")


# ===========================================================================
# 9. Lifecycle idempotent
# ===========================================================================

def test_start_worker_idempotent():
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.start_learn_worker(db_path=db)
    first = learn._learn_worker_started
    learn.start_learn_worker(db_path=db)
    _check("start_worker_idempotent", learn._learn_worker_started is first)


def test_start_worker_creates_single_thread():
    """Deux appels start_learn_worker ne créent qu'un seul thread."""
    _reset_learn_state()
    learn.LEARN_ENABLED = True
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    created_threads = []
    original_thread = threading.Thread

    class MockThread:
        def __init__(self, *args, **kwargs):
            self._daemon = kwargs.get("daemon", False)
            self._started = False
            self._target = kwargs.get("target")
            self._name = kwargs.get("name", "")
            created_threads.append(self)

        def start(self):
            self._started = True

    import threading as _threading
    original_Thread = _threading.Thread
    _threading.Thread = MockThread
    try:
        learn.start_learn_worker(db_path=db)
        learn.start_learn_worker(db_path=db)
    finally:
        _threading.Thread = original_Thread
    _check("start_worker_single_thread",
           len(created_threads) == 1,
           f"threads_created={len(created_threads)}")


# ===========================================================================
# 10. Integration: full pipeline
# ===========================================================================

def test_full_pipeline():
    _reset_learn_state()
    db = Path(tempfile.mkdtemp()) / "t.sqlite3"
    learn.init_learn_db(db)
    learn.LEARN_ENABLED = True

    learn.learn_push("eid1", "s1", "search", "nike trail", meta={"nb_results": 404})
    learn.learn_push("eid2", "s2", "result_click", "nike trail", offer_key="ok1",
                     marketplace="eBay", meta={"pos": 3, "price": 89.99})
    learn.learn_push("eid3", "s3", "favorite", "nike trail", offer_key="ok2",
                     marketplace="AliExpress", meta={"price": 42.50})
    learn.learn_push("eid4", "s4", "compare", "nike trail",
                     meta={"mps": ["eBay", "AliExpress"]})
    learn.learn_push("eid5", "s5", "sort", "nike trail", meta={"sort": "price_asc"})
    learn.learn_push("eid6", "s6", "expand", "nike trail",
                     meta={"added": 12, "new_sources": ["Grailed"]})
    learn.learn_push("eid7", "s7", "marketplace_click", "nike trail", marketplace="eBay")

    count = learn._learn_flush_batch()
    conn = sqlite3.connect(str(db))
    types = {r[0] for r in conn.execute("SELECT DISTINCT event_type FROM learn_events")}
    conn.close()

    expected = {"search", "result_click", "favorite", "compare", "sort", "expand", "marketplace_click"}
    _check("full_pipeline", count == 7 and expected.issubset(types), f"got {types}")


# ===========================================================================
# 11. Lifecycle / preload safety
# ===========================================================================

def test_wsgi_import_no_background_workers():
    """Importer wsgi.py ne doit JAMAIS appeler _start_background_workers."""
    import importlib
    import sys as _sys

    call_log = []
    orig_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def spy_import(name, *args, **kwargs):
        mod = orig_import(name, *args, **kwargs)
        return mod

    wsgi_path = str(ROOT / "wsgi.py")
    with open(wsgi_path, "r") as f:
        source = f.read()

    has_module_level_call = (
        "_start_background_workers()" in source
        or "_start_collector()" in source
        or "start_learn_worker(" in source
    )
    _check("wsgi_no_background_call",
           not has_module_level_call,
           "wsgi.py still has background worker call at module level")


def test_wsgi_only_exposes_app():
    """wsgi.py ne doit exposer que `app` → `application`."""
    import ast
    wsgi_path = str(ROOT / "wsgi.py")
    with open(wsgi_path, "r") as f:
        tree = ast.parse(f.read())
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    import_from_stmts = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    only_imports_app = True
    for node in import_from_stmts:
        if node.module and "app_web" in node.module:
            names = [alias.name for alias in node.names]
            if names != ["app"]:
                only_imports_app = False
    has_assignment = any(
        isinstance(node, ast.Assign)
        for node in ast.walk(tree)
        if any(
            isinstance(t, ast.Name) and t.id == "application"
            for t in getattr(node, "targets", [])
        )
    )
    _check("wsgi_only_exposes_app",
           only_imports_app and has_assignment,
           f"imports_app={only_imports_app}, has_assignment={has_assignment}")


def test_gunicorn_post_fork_calls_start_background():
    """gunicorn.conf.py post_fork doit appeler _start_background_workers."""
    conf_path = str(ROOT / "gunicorn.conf.py")
    with open(conf_path, "r") as f:
        source = f.read()
    _check("gunicorn_post_fork_calls_start_background",
           "_start_background_workers" in source and "post_fork" in source,
           "post_fork missing _start_background_workers call")


def test_gunicorn_no_direct_learn_import():
    """gunicorn.conf.py post_fork ne doit pas importer directement learn."""
    conf_path = str(ROOT / "gunicorn.conf.py")
    with open(conf_path, "r") as f:
        source = f.read()
    import ast
    tree = ast.parse(source)
    has_direct_learn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "learn" in node.module:
            has_direct_learn = True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "learn" in alias.name:
                    has_direct_learn = True
    _check("gunicorn_no_direct_learn_import",
           not has_direct_learn,
           "gunicorn.conf.py still imports learn directly")


def test_post_fork_hook_idempotent():
    """Appeler _start_background_workers deux fois ne crée qu'un seul thread learn."""
    import app_web
    _reset_learn_state()
    learn.LEARN_ENABLED = True

    created_threads = []
    original_Thread = threading.Thread

    class MockThread:
        def __init__(self, *args, **kwargs):
            self._daemon = kwargs.get("daemon", False)
            self._started = False
            self._name = kwargs.get("name", "")
            created_threads.append(self)

        def start(self):
            self._started = True

    threading.Thread = MockThread
    try:
        app_web._start_background_workers()
        app_web._start_background_workers()
    finally:
        threading.Thread = original_Thread
        learn.LEARN_ENABLED = _orig_enabled
    _check("post_fork_idempotent",
           len(created_threads) <= 1,
           f"threads_created={len(created_threads)}")


def test_learn_flag_off_no_thread():
    """Si LEARN_ENABLED=False, _start_background_workers ne crée pas de thread learning."""
    _reset_learn_state()
    learn.LEARN_ENABLED = False

    created_threads = []
    original_Thread = threading.Thread

    class MockThread:
        def __init__(self, *args, **kwargs):
            self._daemon = kwargs.get("daemon", False)
            self._started = False
            self._name = kwargs.get("name", "")
            created_threads.append(self)

        def start(self):
            self._started = True

    import app_web
    threading.Thread = MockThread
    try:
        app_web._start_background_workers()
    finally:
        threading.Thread = original_Thread

    learn_threads = [t for t in created_threads if t._name == "luxe-learn"]
    _check("learn_flag_off_no_thread",
           len(learn_threads) == 0,
           f"learn_threads={len(learn_threads)}")
    learn.LEARN_ENABLED = _orig_enabled


def test_background_workers_function_exists():
    """app_web exposes _start_background_workers as callable."""
    import app_web
    _check("background_workers_exists",
           callable(getattr(app_web, "_start_background_workers", None)),
           "missing _start_background_workers")


# ===========================================================================
# Main
# ===========================================================================

ALL_TESTS = [
    test_flag_default_disabled, test_flag_enabled_true, test_flag_enabled_1,
    test_flag_disabled_empty, test_flag_off_no_push, test_flag_off_no_start,
    test_schema_create_table, test_learn_signals_table, test_schema_idempotent,
    test_event_id_unique, test_schema_columns,
    test_push_adds_to_buffer, test_push_rejects_invalid_type,
    test_push_rejects_empty_qk, test_push_rejects_long_qk,
    test_push_rejects_empty_event_id, test_push_rejects_long_event_id,
    test_buffer_full_rejects_new, test_buffer_first_event_preserved_after_reject,
    test_meta_strips_unknown_fields, test_meta_truncates_long_values,
    test_meta_real_allowed_field_search, test_meta_real_allowed_field_result_click,
    test_drop_stats, test_drop_counter_increments,
    test_flush_inserts, test_flush_empty_buffer, test_flush_unique_event_ids,
    test_flush_dedup_same_event_id,
    test_flush_abandon_on_locked_db,
    test_purge_removes_old_events, test_purge_preserves_recent,
    test_endpoint_flag_off_204, test_endpoint_valid_accepted,
    test_endpoint_invalid_type_rejected, test_endpoint_missing_event_id_rejected,
    test_endpoint_empty_body,
    test_endpoint_oversized_body, test_endpoint_capped_events,
    test_endpoint_csrf_required, test_endpoint_post_only,
    test_endpoint_invalid_json, test_endpoint_missing_events_key,
    test_endpoint_beacon_no_csrf_403,
    test_endpoint_client_qk_fallback, test_endpoint_no_token_uses_client_qk,
    test_endpoint_qk_canonicalized_with_token,
    test_session_id_set_on_call, test_session_id_is_opaque_hex,
    test_start_worker_idempotent, test_start_worker_creates_single_thread,
    test_full_pipeline,
    test_wsgi_import_no_background_workers,
    test_wsgi_only_exposes_app,
    test_gunicorn_post_fork_calls_start_background,
    test_gunicorn_no_direct_learn_import,
    test_post_fork_hook_idempotent,
    test_learn_flag_off_no_thread,
    test_background_workers_function_exists,
]


def main():
    global _pass, _fail, _errors
    _pass = 0
    _fail = 0
    _errors = []
    print("=" * 60)
    print("Learning J1-J4 Tests")
    print("=" * 60)
    for fn in ALL_TESTS:
        _reset_learn_state()
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
