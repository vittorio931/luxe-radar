"""Surveillance Vinted persistante et conforme (alertes, jamais d'achat).

Le worker utilise exclusivement le connecteur public déjà présent dans Luxe
Radar. Il n'authentifie aucun compte Vinted et ne contourne aucune protection.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import time
from urllib.parse import urlparse
import urllib.request
from uuid import uuid4

import quick_buy


_LOCK = Lock()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def db_path() -> Path:
    override = str(os.environ.get("VINTED_WATCH_DB") or "").strip()
    return Path(override).expanduser().resolve() if override else Path(__file__).resolve().parent / "instance" / "vinted_watch.sqlite3"


def _connect(path=None):
    target = Path(path or db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=5, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS vinted_watches (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, criteria_json TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, interval_seconds INTEGER NOT NULL,
            next_run REAL NOT NULL, last_run TEXT, last_status TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(user_id)
        );
        CREATE TABLE IF NOT EXISTS vinted_seen (
            watch_id TEXT NOT NULL, offer_key TEXT NOT NULL, first_seen TEXT NOT NULL,
            PRIMARY KEY(watch_id, offer_key)
        );
        CREATE TABLE IF NOT EXISTS vinted_alerts (
            id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, user_id TEXT NOT NULL,
            offer_key TEXT NOT NULL, offer_json TEXT NOT NULL, created_at TEXT NOT NULL,
            read_at TEXT, UNIQUE(watch_id, offer_key)
        );
        CREATE INDEX IF NOT EXISTS idx_vinted_alert_user ON vinted_alerts(user_id, created_at DESC);
    """)
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def discord_enabled() -> bool:
    return bool(str(os.environ.get("LUXE_RADAR_DISCORD_WEBHOOK_URL") or "").strip())


def _discord_image_url(offer: dict) -> str:
    value = str(
        offer.get("image") or offer.get("image_url") or offer.get("photo")
        or offer.get("photo_url") or ""
    ).strip()
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and bool(parsed.netloc) else ""


def _discord_value(value, fallback="Non précisé") -> str:
    text = str(value).strip() if value is not None else ""
    return (text or fallback)[:1024]


def send_discord_offer(offer: dict, query: str) -> bool:
    """Envoie un embed d'alerte riche avec lien Vinted, sans achat automatisé."""
    target = str(os.environ.get("LUXE_RADAR_DISCORD_WEBHOOK_URL") or "").strip()
    url = quick_buy.safe_vinted_url(offer.get("lien") or offer.get("url"))
    if not target or not url:
        return False
    title = str(offer.get("titre") or offer.get("title") or "Annonce Vinted")[:256]
    price = offer.get("prix") if offer.get("prix") is not None else offer.get("price")
    currency = _discord_value(offer.get("devise") or offer.get("currency") or "EUR")
    price_label = f"{price} {currency}" if price is not None else "Non précisé"
    image_url = _discord_image_url(offer)
    fields = [
        {"name": "💶 Prix", "value": price_label[:1024], "inline": True},
        {"name": "🏷️ Marque", "value": _discord_value(offer.get("marque") or offer.get("brand")), "inline": True},
        {"name": "📏 Taille", "value": _discord_value(offer.get("taille") or offer.get("size")), "inline": True},
        {"name": "✨ État", "value": _discord_value(offer.get("etat") or offer.get("condition")), "inline": True},
        {"name": "🔎 Recherche", "value": _discord_value(query), "inline": True},
        {"name": "🎯 Score", "value": _discord_value(offer.get("score") or offer.get("match_score"), "À analyser"), "inline": True},
    ]
    embed = {
        "author": {"name": "LUXE RADAR · Nouvelle pépite détectée"},
        "title": title,
        "description": "Annonce correspondant à ton radar. Vérifie les détails avant de confirmer l’achat.",
        "url": url,
        "color": 0xD4AF37,
        "fields": fields,
        "footer": {"text": "LUXE RADAR · achat final sécurisé sur Vinted"},
        "timestamp": _now(),
    }
    if image_url:
        embed["thumbnail"] = {"url": image_url}
    payload = {
        "username": "LUXE RADAR · Bot Vinted",
        "content": "🚨 **NOUVELLE ANNONCE VINTED** · Clique vite pour vérifier l’article.",
        "embeds": [embed],
        "components": [{"type": 1, "components": [{
            "type": 2, "style": 5, "label": "⚡ Acheter sur Vinted", "url": url,
        }]}],
    }
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        endpoint = target + ("&" if "?" in target else "?") + "with_components=true"
        request = urllib.request.Request(endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "LuxeRadar/3.8",
        })
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def sanitise(payload) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    query = str(payload.get("query") or "").strip()[:120]
    def number(name, default):
        try:
            value = float(payload.get(name, default))
            return value if 0 <= value <= 1_000_000 else default
        except (TypeError, ValueError):
            return default
    # 30 s est le mode rapide : assez réactif pour les alertes, sans boucle
    # agressive à la seconde contre la source publique Vinted.
    interval = max(30, min(int(number("interval", 900)), 86_400))
    return {
        "query": query,
        "price_min": number("price_min", 0),
        "price_max": number("price_max", 0),
        "size": str(payload.get("size") or "").strip()[:30],
        "required": str(payload.get("required") or "").strip()[:120],
        "excluded": str(payload.get("excluded") or "").strip()[:120],
        "interval": interval,
    }


def save_watch(user_id: str, payload, path=None) -> dict:
    criteria = sanitise(payload)
    if len(criteria["query"]) < 2 or criteria["price_max"] <= 0 or criteria["price_min"] > criteria["price_max"]:
        raise ValueError("INVALID_CRITERIA")
    stamp, watch_id = _now(), uuid4().hex
    with _LOCK, _connect(path) as db:
        existing = db.execute("SELECT id, created_at FROM vinted_watches WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            watch_id, created = existing["id"], existing["created_at"]
        else:
            created = stamp
        db.execute("""
            INSERT INTO vinted_watches(id,user_id,criteria_json,active,interval_seconds,next_run,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET criteria_json=excluded.criteria_json,
              active=1, interval_seconds=excluded.interval_seconds, next_run=excluded.next_run,
              updated_at=excluded.updated_at
        """, (watch_id, user_id, json.dumps(criteria), 1, criteria["interval"], 0, created, stamp))
        db.commit()
    return {"id": watch_id, "active": True, **criteria}


def set_active(user_id: str, active: bool, path=None) -> bool:
    with _LOCK, _connect(path) as db:
        changed = db.execute(
            "UPDATE vinted_watches SET active=?, next_run=?, updated_at=? WHERE user_id=?",
            (int(bool(active)), 0 if active else time(), _now(), user_id),
        ).rowcount
        db.commit()
    return bool(changed)


def delete_watch(user_id: str, path=None) -> bool:
    """Supprime une surveillance et son historique associé."""
    with _LOCK, _connect(path) as db:
        rows = db.execute("SELECT id FROM vinted_watches WHERE user_id=?", (user_id,)).fetchall()
        watch_ids = [row["id"] for row in rows]
        for watch_id in watch_ids:
            db.execute("DELETE FROM vinted_alerts WHERE watch_id=?", (watch_id,))
            db.execute("DELETE FROM vinted_seen WHERE watch_id=?", (watch_id,))
        changed = db.execute("DELETE FROM vinted_watches WHERE user_id=?", (user_id,)).rowcount
        db.commit()
    return bool(changed)


def get_watch(user_id: str, path=None) -> dict | None:
    with _LOCK, _connect(path) as db:
        row = db.execute("SELECT * FROM vinted_watches WHERE user_id=?", (user_id,)).fetchone()
        unread = db.execute("SELECT COUNT(*) n FROM vinted_alerts WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()["n"]
    if not row:
        return None
    return {"id": row["id"], "active": bool(row["active"]), "last_run": row["last_run"],
            "last_status": row["last_status"], "unread": unread, **json.loads(row["criteria_json"])}


def alerts(user_id: str, mark_read=False, limit=100, path=None) -> list[dict]:
    with _LOCK, _connect(path) as db:
        rows = db.execute(
            "SELECT id,offer_json,created_at,read_at FROM vinted_alerts WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, max(1, min(int(limit), 200))),
        ).fetchall()
        if mark_read and rows:
            db.executemany("UPDATE vinted_alerts SET read_at=? WHERE id=?", [(_now(), row["id"]) for row in rows])
            db.commit()
    return [{"alert_id": row["id"], "created_at": row["created_at"], "unread": row["read_at"] is None,
             **json.loads(row["offer_json"])} for row in rows]


class WatchWorker:
    def __init__(self, scanner, *, path=None, notifier=None):
        self.scanner, self.path, self.notifier = scanner, path, notifier
        self.stop_event, self.thread = Event(), None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._loop, name="vinted-watch", daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.wait(5):
            try:
                self.run_due()
            except Exception:
                continue

    def run_due(self):
        with _LOCK, _connect(self.path) as db:
            rows = db.execute("SELECT * FROM vinted_watches WHERE active=1 AND next_run<=? ORDER BY next_run LIMIT 3", (time(),)).fetchall()
        for row in rows:
            criteria = json.loads(row["criteria_json"])
            status = "OK"
            try:
                offers = self.scanner(criteria) or []
                with _LOCK, _connect(self.path) as db:
                    for offer in offers:
                        key = quick_buy.offer_key(offer)
                        inserted = db.execute("INSERT OR IGNORE INTO vinted_seen(watch_id,offer_key,first_seen) VALUES(?,?,?)", (row["id"], key, _now())).rowcount
                        if inserted:
                            db.execute("INSERT OR IGNORE INTO vinted_alerts(id,watch_id,user_id,offer_key,offer_json,created_at) VALUES(?,?,?,?,?,?)",
                                       (uuid4().hex, row["id"], row["user_id"], key, json.dumps(offer, ensure_ascii=False), _now()))
                            Thread(
                                target=self.notifier or send_discord_offer,
                                args=((row["user_id"], dict(offer), str(criteria.get("query") or "Recherche Vinted"))
                                      if self.notifier else (dict(offer), str(criteria.get("query") or "Recherche Vinted"))),
                                daemon=True,
                            ).start()
                    db.execute("UPDATE vinted_watches SET last_run=?,last_status=?,next_run=? WHERE id=?",
                               (_now(), status, time() + row["interval_seconds"], row["id"]))
                    db.commit()
            except Exception as exc:
                status = f"ERROR:{type(exc).__name__}"[:80]
                with _LOCK, _connect(self.path) as db:
                    db.execute("UPDATE vinted_watches SET last_run=?,last_status=?,next_run=? WHERE id=?",
                               (_now(), status, time() + max(60, row["interval_seconds"]), row["id"]))
                    db.commit()
