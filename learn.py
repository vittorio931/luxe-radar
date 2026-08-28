"""LUXE RADAR — anonymous analytics and bounded ranking signals.

Collecte anonyme d'événements de recherche/interaction pour alimenter
ultérieurement un ranking adaptatif (J5-J6, pas encore codé).

Feature flag : LUXE_RADAR_LEARN_ENABLED=false par défaut.
Si désactivé : aucun thread, aucune écriture, aucun impact.

Sécurité :
- session_id = identifiant aléatoire serveur (jamais dérivé d'IP/UA)
- query_key = canonical_query côté serveur (jamais raw_query côté client)
- timestamp = côté serveur
- event_id = fourni par le frontend, stocké tel quel, UNIQUE(event_id)
- aucune IP, UA, fingerprint, email, nom ou adresse stocké
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from time import monotonic

from index_engine import canonical_query

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

LEARN_ENABLED = os.environ.get(
    "LUXE_RADAR_LEARN_ENABLED", ""
).strip().lower() in {"1", "true", "yes"}


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name) or "")
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LEARN_BUSY_TIMEOUT_MS = _env_int("LUXE_RADAR_LEARN_BUSY_MS", 200, 50, 500)
LEARN_BATCH_SIZE = 200
LEARN_BUFFER_MAX = 4096
LEARN_FLUSH_INTERVAL = 12.0
LEARN_PURGE_INTERVAL = 3600.0
LEARN_AGGREGATE_INTERVAL = 60.0
LEARN_PURGE_BATCH = 500
LEARN_RETENTION_DAYS = 30
LEARN_MAX_EVENTS_PER_POST = 15
LEARN_MAX_BODY_BYTES = 16384

# Allowlist stricte des types d'événements
_LEARN_EVENT_TYPES = frozenset({
    "search", "result_click", "favorite", "compare",
    "sort", "expand", "marketplace_click",
})

# Champs autorisés dans meta_json par event_type
_LEARN_META_FIELDS = {
    "search": frozenset({"nb_results", "platform", "nb_sources"}),
    "result_click": frozenset({"pos", "price", "marketplace"}),
    "favorite": frozenset({"price", "marketplace"}),
    "compare": frozenset({"mps"}),
    "sort": frozenset({"sort"}),
    "expand": frozenset({"added", "new_sources"}),
    "marketplace_click": frozenset({"marketplace"}),
}

LEARN_MAX_VALUE_LEN = 100
LEARN_MAX_META_KEYS = 10
LEARN_MAX_QK_LEN = 200
LEARN_MAX_MP_LEN = 60
LEARN_MAX_OK_LEN = 200
LEARN_MAX_EVENT_ID_LEN = 128

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS learn_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    query_key TEXT NOT NULL,
    offer_key TEXT NOT NULL DEFAULT '',
    marketplace TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learn_ts ON learn_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_learn_query ON learn_events(query_key, ts DESC);
CREATE TABLE IF NOT EXISTS learn_signals (
    query_key TEXT NOT NULL,
    marketplace TEXT NOT NULL DEFAULT '',
    interactions INTEGER NOT NULL DEFAULT 0,
    sessions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    favorites INTEGER NOT NULL DEFAULT 0,
    compares INTEGER NOT NULL DEFAULT 0,
    bonus REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(query_key, marketplace)
);
CREATE TABLE IF NOT EXISTS learn_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def ensure_learn_schema(conn: sqlite3.Connection) -> None:
    """Crée la table learn_events si absante. Idempotent."""
    conn.executescript(_SCHEMA_DDL)


# ---------------------------------------------------------------------------
# Connexion SQLite dédiée (timeout très court)
# ---------------------------------------------------------------------------

_learn_db_path: Path | None = None
_learn_schema_ready = False
_learn_schema_lock = threading.Lock()


def init_learn_db(path: Path) -> None:
    """Initialise le chemin DB et le schéma une seule fois."""
    global _learn_db_path, _learn_schema_ready
    if _learn_schema_ready and str(path.resolve()) == str(
        _learn_db_path.resolve() if _learn_db_path else ""
    ):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _learn_schema_lock:
        if _learn_schema_ready:
            return
        conn = sqlite3.connect(str(path), timeout=1.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={LEARN_BUSY_TIMEOUT_MS}")
            ensure_learn_schema(conn)
            conn.commit()
        finally:
            conn.close()
        _learn_db_path = path.resolve()
        _learn_schema_ready = True


def _learn_conn() -> sqlite3.Connection:
    """Connexion learning avec timeout très court (< 300ms)."""
    if _learn_db_path is None:
        raise sqlite3.OperationalError("learn DB not initialized")
    conn = sqlite3.connect(str(_learn_db_path), timeout=LEARN_BUSY_TIMEOUT_MS / 1000.0)
    conn.execute(f"PRAGMA busy_timeout={LEARN_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Buffer RAM borné (rejet si plein, jamais d'éviction)
# ---------------------------------------------------------------------------

_learn_buffer: deque = deque()
_learn_drop_count = 0
_learn_drop_lock = threading.Lock()
_learn_flush_count = 0


def learn_push(
    event_id: str,
    session_id: str,
    event_type: str,
    query_key: str,
    offer_key: str = "",
    marketplace: str = "",
    meta: dict | None = None,
) -> bool:
    """Pousse un event validé dans le buffer. Retourne False si rejeté.

    Si le buffer est plein (>= LEARN_BUFFER_MAX), l'événement est rejeté
    et le compteur drops est incrémenté. Aucune éviction FIFO.
    """
    if not LEARN_ENABLED:
        return False
    if event_type not in _LEARN_EVENT_TYPES:
        return False
    if not query_key or len(query_key) > LEARN_MAX_QK_LEN:
        return False
    if not event_id or len(event_id) > LEARN_MAX_EVENT_ID_LEN:
        return False

    if len(_learn_buffer) >= LEARN_BUFFER_MAX:
        with _learn_drop_lock:
            global _learn_drop_count
            _learn_drop_count += 1
        return False

    now = time.time()
    meta_json = _sanitize_meta(event_type, meta or {})

    try:
        _learn_buffer.append((event_id, session_id, now, event_type, query_key, offer_key, marketplace, meta_json, now))
        return True
    except Exception:
        return False


def _sanitize_meta(event_type: str, meta: dict) -> str:
    """Valide et tronque meta_json selon l'allowlist de event_type."""
    allowed = _LEARN_META_FIELDS.get(event_type, frozenset())
    if not allowed or not isinstance(meta, dict):
        return "{}"
    clean = {}
    for key in list(meta.keys())[:LEARN_MAX_META_KEYS]:
        if key not in allowed:
            continue
        value = meta[key]
        if isinstance(value, str):
            value = value[:LEARN_MAX_VALUE_LEN]
        elif isinstance(value, (int, float)):
            pass
        elif isinstance(value, list):
            value = [str(v)[:LEARN_MAX_VALUE_LEN] for v in value[:10]]
        else:
            continue
        clean[key] = value
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))


def learn_drop_stats() -> dict:
    """Retourne les compteurs de drops pour monitoring."""
    with _learn_drop_lock:
        return {
            "buffer_len": len(_learn_buffer),
            "buffer_max": LEARN_BUFFER_MAX,
            "drops": _learn_drop_count,
            "flushes": _learn_flush_count,
        }


# ---------------------------------------------------------------------------
# Flush batch → SQLite
# ---------------------------------------------------------------------------

def _learn_flush_batch() -> int:
    """Draine jusqu'à LEARN_BATCH_SIZE events du buffer et les INSERT.

    Utilise INSERT OR IGNORE pour ignorer proprement les doublons event_id.
    """
    global _learn_drop_count, _learn_flush_count
    batch = []
    while _learn_buffer and len(batch) < LEARN_BATCH_SIZE:
        try:
            batch.append(_learn_buffer.popleft())
        except IndexError:
            break
    if not batch:
        return 0

    try:
        conn = _learn_conn()
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO learn_events "
                "(event_id, session_id, ts, event_type, query_key, offer_key, "
                "marketplace, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            _learn_flush_count += 1
            return len(batch)
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    except Exception:
        pass

    # Contention is temporary: preserve the batch for the next short cycle.
    for event in reversed(batch):
        if len(_learn_buffer) < LEARN_BUFFER_MAX:
            _learn_buffer.appendleft(event)
    return 0


_last_aggregate_ts = 0.0


def _learn_aggregate() -> int:
    """Recompute bounded signals from the retained window, outside HTTP work."""
    global _last_aggregate_ts
    cutoff = time.time() - LEARN_RETENTION_DAYS * 86400
    try:
        conn = _learn_conn()
        try:
            rows = conn.execute(
                "SELECT query_key,marketplace,COUNT(*) interactions,"
                "COUNT(DISTINCT session_id) sessions,"
                "SUM(event_type IN ('result_click','marketplace_click')) clicks,"
                "SUM(event_type='favorite') favorites,SUM(event_type='compare') compares "
                "FROM learn_events WHERE ts>=? GROUP BY query_key,marketplace",
                (cutoff,),
            ).fetchall()
            now = time.time()
            values = []
            for row in rows:
                interactions, sessions = int(row[2]), int(row[3])
                # No behavioral influence before enough independent evidence.
                bonus = 0.0
                if interactions >= 20 and sessions >= 5:
                    positive = int(row[4] or 0) + 2 * int(row[5] or 0) + 2 * int(row[6] or 0)
                    bonus = max(-2.0, min(2.0, 2.0 * positive / max(1, interactions)))
                values.append((*row[:7], bonus, now))
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM learn_signals")
            conn.executemany(
                "INSERT INTO learn_signals(query_key,marketplace,interactions,sessions,clicks,favorites,compares,bonus,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)", values,
            )
            conn.execute(
                "INSERT INTO learn_meta(key,value) VALUES('last_aggregation',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),),
            )
            conn.commit()
            _last_aggregate_ts = now
            return len(values)
        finally:
            conn.close()
    except Exception:
        return 0


def learning_status() -> dict:
    status = learn_drop_stats()
    status.update({"enabled": LEARN_ENABLED, "queries_learned": 0, "last_aggregation": None})
    try:
        conn = _learn_conn()
        try:
            status["queries_learned"] = int(conn.execute(
                "SELECT COUNT(DISTINCT query_key) FROM learn_signals WHERE bonus!=0"
            ).fetchone()[0])
            row = conn.execute("SELECT value FROM learn_meta WHERE key='last_aggregation'").fetchone()
            status["last_aggregation"] = float(row[0]) if row else None
        finally:
            conn.close()
    except Exception:
        pass
    return status


# ---------------------------------------------------------------------------
# Purge incrémentale (SQL portable, sans DELETE ... LIMIT direct)
# ---------------------------------------------------------------------------

_last_purge_ts = 0.0


def _learn_purge_old() -> int:
    """Supprime les events > retention en lots bornés. Hors hot path.

    Utilise DELETE ... WHERE id IN (SELECT id ... ORDER BY id LIMIT ?)
    pour être portable sans option de compilation SQLite spéciale.
    """
    global _last_purge_ts
    cutoff = time.time() - (LEARN_RETENTION_DAYS * 86400)
    deleted = 0
    try:
        conn = _learn_conn()
        try:
            while True:
                cursor = conn.execute(
                    "DELETE FROM learn_events WHERE id IN "
                    "(SELECT id FROM learn_events WHERE created_at < ? "
                    "ORDER BY id LIMIT ?)",
                    (cutoff, LEARN_PURGE_BATCH),
                )
                batch_deleted = cursor.rowcount
                if batch_deleted == 0:
                    break
                deleted += batch_deleted
                conn.commit()
                if batch_deleted < LEARN_PURGE_BATCH:
                    break
        finally:
            conn.close()
    except Exception:
        pass
    _last_purge_ts = time.time()
    return deleted


# ---------------------------------------------------------------------------
# Flush daemon loop
# ---------------------------------------------------------------------------

_learn_worker_started = False
_learn_worker_lock = threading.Lock()


def start_learn_worker(db_path: Path | None = None) -> None:
    """Démarre le flush daemon une seule fois (idempotent).

    Appelé depuis _start_background_workers() dans app_web.py
    (post_fork Gunicorn ou __main__ local). Deux appels dans le même
    PID ne créent qu'un seul thread.
    """
    global _learn_worker_started
    if not LEARN_ENABLED:
        return
    with _learn_worker_lock:
        if _learn_worker_started:
            return
        _learn_worker_started = True

    if db_path is not None:
        init_learn_db(db_path)

    t = threading.Thread(target=_learn_flush_loop, name="luxe-learn", daemon=True)
    t.start()


def _learn_flush_loop() -> None:
    """Loop daemon : flush périodique + purge incrémentale."""
    global _last_purge_ts
    while True:
        time.sleep(LEARN_FLUSH_INTERVAL)
        _learn_flush_batch()
        if time.time() - _last_aggregate_ts > LEARN_AGGREGATE_INTERVAL:
            _learn_aggregate()
        if time.time() - _last_purge_ts > LEARN_PURGE_INTERVAL:
            _learn_purge_old()
