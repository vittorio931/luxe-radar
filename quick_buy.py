"""Quick Buy sûr : validation locale puis redirection officielle uniquement.

Aucune connexion, aucun paiement et aucun appel à une API privée Vinted ne sont
effectués. L'offre passée à ``check_offer`` doit provenir du cache serveur.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock
from urllib.parse import urlparse
from uuid import uuid4


LINK_ONLY = "LINK_ONLY"
ASSISTED_CHECKOUT = "ASSISTED_CHECKOUT"
OFFICIAL_ONE_CLICK = "OFFICIAL_ONE_CLICK"
ALLOWED_VINTED_HOSTS = frozenset({
    "vinted.fr", "www.vinted.fr", "vinted.com", "www.vinted.com",
    "vinted.be", "www.vinted.be", "vinted.de", "www.vinted.de",
    "vinted.es", "www.vinted.es", "vinted.it", "www.vinted.it",
    "vinted.nl", "www.vinted.nl", "vinted.pt", "www.vinted.pt",
    "vinted.co.uk", "www.vinted.co.uk", "vinted.pl", "www.vinted.pl",
})
_DB_LOCK = Lock()


class _ClosingConnection(sqlite3.Connection):
    """Connexion contextuelle réellement fermée, important sous Windows."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _bool_env(name: str, default: bool) -> bool:
    value = str(os.environ.get(name, "")).strip().casefold()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _bool_env("QUICK_BUY_ENABLED", True)


def launch_free() -> bool:
    return _bool_env("QUICK_BUY_LAUNCH_FREE", True)


def free_monthly_limit() -> int:
    try:
        return max(0, min(int(os.environ.get("QUICK_BUY_FREE_MONTHLY_LIMIT", "3")), 1000))
    except (TypeError, ValueError):
        return 3


def db_path() -> Path:
    override = str(os.environ.get("QUICK_BUY_DB") or "").strip()
    return Path(override).expanduser().resolve() if override else (
        Path(__file__).resolve().parent / "instance" / "quick_buy.sqlite3"
    )


def _connect(path=None):
    target = Path(path or db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=5, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS quick_buy_preferences (
            user_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quick_buy_events (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            offer_key TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            checked_price REAL,
            status TEXT NOT NULL,
            final_destination TEXT,
            failure_reason TEXT,
            purchase_capability TEXT NOT NULL,
            response_json TEXT NOT NULL,
            UNIQUE(user_id, request_id)
        );
        CREATE INDEX IF NOT EXISTS idx_quick_buy_user_month
        ON quick_buy_events(user_id, requested_at);
    """)
    return connection


def user_id(session_token: str) -> str:
    return hashlib.sha256(str(session_token or "").encode()).hexdigest()


def offer_key(offer: dict) -> str:
    source = str(offer.get("marketplace") or offer.get("plateforme") or "")
    url = str(offer.get("lien") or offer.get("url") or "")
    title = str(offer.get("titre") or offer.get("title") or "")
    return hashlib.sha256(f"{source}|{url}|{title}".encode()).hexdigest()[:32]


def safe_vinted_url(value: str) -> str | None:
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or host not in ALLOWED_VINTED_HOSTS:
        return None
    if not parsed.path.startswith("/items/"):
        return None
    return value


DEFAULT_PREFERENCES = {
    "active": True, "budget_max": None, "item_price_max": None,
    "fees_max": None, "brands": [], "sizes": [], "categories": [],
    "required_keywords": [], "excluded_keywords": [], "conditions": [],
    "seller_type": "all",
}


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 1_000_000 else None


def _words(value, limit=30):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip()[:80] for item in value if str(item).strip()))[:limit]


def sanitise_preferences(payload) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    return {
        "active": bool(payload.get("active", True)),
        "budget_max": _number(payload.get("budget_max")),
        "item_price_max": _number(payload.get("item_price_max")),
        "fees_max": _number(payload.get("fees_max")),
        "brands": _words(payload.get("brands")),
        "sizes": _words(payload.get("sizes")),
        "categories": _words(payload.get("categories")),
        "required_keywords": _words(payload.get("required_keywords")),
        "excluded_keywords": _words(payload.get("excluded_keywords")),
        "conditions": _words(payload.get("conditions")),
        "seller_type": payload.get("seller_type") if payload.get("seller_type") in {"all", "professional", "individual"} else "all",
    }


def get_preferences(uid: str, path=None) -> dict:
    with _DB_LOCK, _connect(path) as connection:
        row = connection.execute(
            "SELECT payload FROM quick_buy_preferences WHERE user_id=?", (uid,)
        ).fetchone()
    return sanitise_preferences(json.loads(row["payload"])) if row else dict(DEFAULT_PREFERENCES)


def save_preferences(uid: str, payload, path=None) -> dict:
    clean = sanitise_preferences(payload)
    now = datetime.now(timezone.utc).isoformat()
    with _DB_LOCK, _connect(path) as connection:
        connection.execute(
            "INSERT INTO quick_buy_preferences(user_id,payload,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (uid, json.dumps(clean, ensure_ascii=False), now),
        )
    return clean


def monthly_usage(uid: str, path=None) -> int:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with _DB_LOCK, _connect(path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM quick_buy_events "
            "WHERE user_id=? AND substr(requested_at,1,7)=? AND status IN ('READY','OPENED')",
            (uid, month),
        ).fetchone()
    return int(row["total"] or 0)


def quota(uid: str, plan="free", path=None) -> dict:
    used = monthly_usage(uid, path)
    unlimited = launch_free() or plan in {"pro", "premium", "reseller"}
    limit = None if unlimited else free_monthly_limit()
    return {"allowed": unlimited or used < limit, "used": used, "limit": limit,
            "remaining": None if limit is None else max(0, limit - used),
            "launch_free": launch_free(), "unlimited": unlimited}


class PurchaseProvider:
    capability = LINK_ONLY

    def can_purchase(self, offer):
        raise NotImplementedError

    def check_offer(self, offer, preferences):
        raise NotImplementedError

    def get_purchase_capability(self):
        return self.capability

    def get_checkout_destination(self, offer):
        raise NotImplementedError


class VintedPurchaseProvider(PurchaseProvider):
    capability = LINK_ONLY

    def can_purchase(self, offer):
        return str(offer.get("marketplace") or "").casefold() == "vinted" and bool(
            self.get_checkout_destination(offer)
        )

    def get_checkout_destination(self, offer):
        return safe_vinted_url(offer.get("lien") or offer.get("url"))

    def check_offer(self, offer, preferences):
        if not self.can_purchase(offer):
            return False, "UNAVAILABLE", "Offre ou URL Vinted invalide."
        if offer.get("disponible") is False or offer.get("sold") is True:
            return False, "ITEM_SOLD", "Cette annonce est indiquée comme vendue."
        price = _number(offer.get("prix"))
        if price is None:
            return False, "UNKNOWN_STATE", "Prix actuel non vérifiable."
        if not preferences.get("active", True):
            return False, "CRITERIA_MISMATCH", "Quick Buy est désactivé dans tes préférences."
        limits = [value for value in (preferences.get("budget_max"), preferences.get("item_price_max")) if value is not None]
        if limits and price > min(limits):
            return False, "PRICE_CHANGED", "Le prix dépasse ta limite."
        fees = _number(offer.get("frais") or offer.get("frais_port"))
        if preferences.get("fees_max") is not None and fees is not None and fees > preferences["fees_max"]:
            return False, "FEES_CHANGED", "Les frais connus dépassent ta limite."
        text = " ".join(str(offer.get(key) or "") for key in ("titre", "title", "marque", "categorie", "taille", "etat", "condition")).casefold()
        for key in ("brands", "sizes", "categories", "conditions"):
            wanted = preferences.get(key) or []
            if wanted and not any(value.casefold() in text for value in wanted):
                return False, "CRITERIA_MISMATCH", f"Critère {key} non respecté."
        if any(word.casefold() not in text for word in preferences.get("required_keywords") or []):
            return False, "CRITERIA_MISMATCH", "Mot-clé requis absent."
        if any(word.casefold() in text for word in preferences.get("excluded_keywords") or []):
            return False, "CRITERIA_MISMATCH", "Mot-clé exclu détecté."
        seller_type = str(offer.get("seller_type") or offer.get("type_vendeur") or "").casefold()
        wanted_seller = preferences.get("seller_type", "all")
        if wanted_seller != "all" and seller_type and seller_type != wanted_seller:
            return False, "CRITERIA_MISMATCH", "Type de vendeur non conforme."
        # Sans API acheteur officielle, disponibilité et frais finaux restent
        # à confirmer sur Vinted : aucune fausse validation n'est renvoyée.
        return True, "READY", None


PROVIDERS = {"vinted": VintedPurchaseProvider()}


def provider_for(offer):
    return PROVIDERS.get(str(offer.get("marketplace") or "").casefold())


def previous_response(uid: str, request_id: str, path=None):
    with _DB_LOCK, _connect(path) as connection:
        row = connection.execute(
            "SELECT response_json FROM quick_buy_events WHERE user_id=? AND request_id=?",
            (uid, request_id),
        ).fetchone()
    return json.loads(row["response_json"]) if row else None


def record(uid, request_id, key, offer, response, path=None):
    now = datetime.now(timezone.utc).isoformat()
    with _DB_LOCK, _connect(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO quick_buy_events "
            "(id,request_id,user_id,offer_key,marketplace,requested_at,checked_price,status,final_destination,failure_reason,purchase_capability,response_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid4().hex, request_id, uid, key, str(offer.get("marketplace") or ""), now,
             _number(offer.get("prix")), response.get("status", "FAILED"),
             response.get("destination_url"), response.get("failure_reason"),
             response.get("purchase_capability", LINK_ONLY), json.dumps(response, ensure_ascii=False)),
        )
