"""Persistance SQLite des sessions de recherche (hotfix V4.1).

Une recherche est un état : token + owner + paramètres + curseurs d'infinite
scroll + résultats bornés (SEARCH_RESULT_LIMIT). Après un redémarrage du
worker, le serveur restaure le token depuis cette base (aucun 404 sur
/expand /more_results), réapplique les curseurs et relance uniquement les
sources encore en attente. La base vit dans instance/ qui survit aux restarts
Gunicorn (même si elle reste éphémère sur un plan Render sans disque persistant).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_sessions (
    token TEXT PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT '',
    search_request TEXT NOT NULL,
    search_price_raw TEXT NOT NULL DEFAULT '',
    selected_platform TEXT NOT NULL DEFAULT 'Toutes',
    reference TEXT NOT NULL DEFAULT '',
    reference_stricte INTEGER NOT NULL DEFAULT 0,
    universe TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_sessions_owner
    ON search_sessions (owner);
"""

_LOCK = threading.Lock()
_DB_PATH = None
_BUSY_TIMEOUT_MS = 4000


def default_db_path() -> Path:
    configured = str(os.environ.get("LUXE_RADAR_SESSIONS_DB") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(__file__).resolve().parent
    return (root / "instance" / "luxe_radar_sessions.sqlite3").resolve()


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = default_db_path()
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=%d" % _BUSY_TIMEOUT_MS)
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.row_factory = sqlite3.Row
    return connection


def _initialize():
    connection = _connect()
    try:
        connection.executescript(_SCHEMA)
    finally:
        connection.close()


def save_search_session(
    token,
    *,
    owner,
    search_request,
    search_price_raw="",
    selected_platform="Toutes",
    reference="",
    reference_stricte=False,
    universe="",
    state=None,
):
    """Upsert l'état persistant d'une recherche (appelé hors du chemin critique)."""
    token = str(token or "").strip()
    if not token:
        return
    with _LOCK:
        _initialize()
        now = time.time()
        connection = _connect()
        try:
            connection.execute(
                """
                INSERT INTO search_sessions (
                    token, owner, search_request, search_price_raw,
                    selected_platform, reference, reference_stricte, universe,
                    state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    owner = excluded.owner,
                    search_request = excluded.search_request,
                    search_price_raw = excluded.search_price_raw,
                    selected_platform = excluded.selected_platform,
                    reference = excluded.reference,
                    reference_stricte = excluded.reference_stricte,
                    universe = excluded.universe,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    token,
                    str(owner or ""),
                    str(search_request or ""),
                    str(search_price_raw or ""),
                    str(selected_platform or "Toutes"),
                    str(reference or ""),
                    1 if reference_stricte else 0,
                    str(universe or ""),
                    json.dumps(state or {}),
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()


def load_search_session(token):
    """Retourne un dict avec les champs persistés, ou None si absent."""
    token = str(token or "").strip()
    if not token:
        return None
    with _LOCK:
        _initialize()
        connection = _connect()
        try:
            row = connection.execute(
                "SELECT * FROM search_sessions WHERE token = ?", (token,)
            ).fetchone()
        finally:
            connection.close()
    if row is None:
        return None
    try:
        state = json.loads(str(row["state_json"] or "{}"))
    except (TypeError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    return {
        "token": str(row["token"]),
        "owner": str(row["owner"] or ""),
        "search_request": str(row["search_request"] or ""),
        "search_price_raw": str(row["search_price_raw"] or ""),
        "selected_platform": str(row["selected_platform"] or "Toutes"),
        "reference": str(row["reference"] or ""),
        "reference_stricte": bool(row["reference_stricte"]),
        "universe": str(row["universe"] or ""),
        "state": state,
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }


def delete_search_session(token):
    token = str(token or "").strip()
    if not token:
        return
    with _LOCK:
        _initialize()
        connection = _connect()
        try:
            connection.execute("DELETE FROM search_sessions WHERE token = ?", (token,))
            connection.commit()
        finally:
            connection.close()


def delete_expired(ttl_seconds):
    """Supprime les sessions non rafraîchies depuis ttl_seconds."""
    try:
        ttl = max(300, min(float(ttl_seconds), 24 * 60 * 60))
    except (TypeError, ValueError):
        ttl = 45 * 60
    with _LOCK:
        _initialize()
        cutoff = time.time() - ttl
        connection = _connect()
        try:
            connection.execute("DELETE FROM search_sessions WHERE updated_at < ?", (cutoff,))
            connection.commit()
        finally:
            connection.close()


def count_sessions():
    with _LOCK:
        _initialize()
        connection = _connect()
        try:
            row = connection.execute("SELECT COUNT(*) AS count FROM search_sessions").fetchone()
        finally:
            connection.close()
    return int(row["count"] if row is not None else 0)


def drop_sessions():
    """Vidange complète (tests uniquement)."""
    with _LOCK:
        _initialize()
        connection = _connect()
        try:
            connection.execute("DELETE FROM search_sessions")
            connection.commit()
        finally:
            connection.close()
