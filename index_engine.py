from __future__ import annotations

"""Persistent global offer index for LUXE RADAR V3.7.

The index is intentionally dependency-free (sqlite3 from the stdlib) so the
existing Render build stays stable. It is designed as a hybrid cache/index:
real marketplace results are written after every live scan. V3.7 keeps the
legacy exact-query cache but also builds a reusable global catalogue, so a
refinement such as ``Stone Island veste`` can reuse offers collected for
``Stone Island`` without contacting a marketplace first.

Persistence notes:
- local dev: ``instance/luxe_radar_index.sqlite3`` by default;
- Render: point ``LUXE_RADAR_INDEX_DB`` at a persistent disk path if available;
- the module never fabricates listings: only connector results are indexed.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import urlparse

try:
    from search_understanding import (
        BRAND_ALIASES as SEARCH_BRAND_ALIASES,
        TYPE_ALIASES as SEARCH_TYPE_ALIASES,
        canonicalize_search_query,
        understand_query,
    )
except Exception:  # pragma: no cover - standalone maintenance safety
    SEARCH_BRAND_ALIASES = {}
    SEARCH_TYPE_ALIASES = {}
    canonicalize_search_query = None
    understand_query = None

# V3.7.x : parser d'intent structuré + quality gate central (stdlib uniquement).
try:
    from search_intent import parse_search_intent
    from relevance_gate import evaluate_offer as _gate_evaluate
except Exception:  # pragma: no cover - degradation if the modules are absent
    parse_search_intent = None
    _gate_evaluate = None

# Small deterministic cache: parse_search_intent runs fuzzy brand/model lookup,
# so we parse each canonical query once per process instead of once per offer.
_INTENT_CACHE: dict[str, object] = {}


def _intent_for(query: str):
    if parse_search_intent is None:
        return None
    # V3.7.x : clé = requête repliée (rapide). canonical_query coûte ~4 ms
    # (corrections floues) : le recalculer à chaque cache-hit ralentissait
    # l'index chaud bien au-delà de la cible < 100 ms.
    key = _fold(query)
    if key in _INTENT_CACHE:
        return _INTENT_CACHE[key]
    try:
        intent = parse_search_intent(query)
    except Exception:  # pragma: no cover - defensive
        intent = None
    if len(_INTENT_CACHE) > 256:
        _INTENT_CACHE.clear()
    _INTENT_CACHE[key] = intent
    return intent


def _gate_evaluate_item(item: dict, intent):
    """Gate central : calcule une fois le score, le garde sur l'item."""
    if _gate_evaluate is None:
        return None
    try:
        cached = item.get("_quality")
        if cached is None:
            cached = _gate_evaluate(intent, item)
            item["_quality"] = cached
        return cached
    except Exception:  # pragma: no cover - defensive: never reject on a gate bug
        return None


def _gate_accepts(item: dict, intent) -> bool:
    result = _gate_evaluate_item(item, intent)
    return result is None or bool(result.accepted)


SCHEMA_VERSION = 3
LEARN_RANKING_ENABLED = os.environ.get(
    "LUXE_RADAR_LEARN_RANKING_ENABLED", ""
).strip().lower() in {"1", "true", "yes"}
DEFAULT_MAX_AGE_SECONDS = 6 * 60 * 60
DEFAULT_QUERY_LIMIT = 5000

# Freshness is tracked per marketplace: fast-moving marketplaces (Vinted) get a
# short TTL so sold items disappear quickly, while slower ones (eBay/retail)
# keep their offers available for a few days. Override each entry with
# ``LUXE_RADAR_TTL_<SOURCE>_SECONDS`` or all others with
# ``LUXE_RADAR_TTL_DEFAULT_SECONDS``.
MARKETPLACE_TTL_SECONDS = {
    "Vinted": 6 * 60 * 60,
    "Grailed": 24 * 60 * 60,
    "Zalando": 24 * 60 * 60,
    "ASOS": 48 * 60 * 60,
    "SSENSE": 48 * 60 * 60,
    "eBay": 48 * 60 * 60,
    "67behaviour": 48 * 60 * 60,
}
DEFAULT_MARKETPLACE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class IndexSearch:
    results: list[dict]
    total: int
    age_seconds: float | None
    query_key: str


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _marketplace_ttl_seconds(marketplace) -> int:
    """Per-marketplace freshness TTL (env overridable, bounds 5 min..7 days)."""
    key = str(marketplace or "").strip()
    if not key:
        return DEFAULT_MARKETPLACE_TTL_SECONDS
    env_name = "LUXE_RADAR_TTL_" + re.sub(r"[^a-zA-Z0-9]+", "_", key).upper() + "_SECONDS"
    raw = str(os.environ.get(env_name, "") or "").strip()
    if raw:
        try:
            return max(300, min(int(float(raw)), MAX_TTL_SECONDS))
        except (TypeError, ValueError):
            pass
    return max(300, min(int(MARKETPLACE_TTL_SECONDS.get(key, DEFAULT_MARKETPLACE_TTL_SECONDS)), MAX_TTL_SECONDS))


def index_enabled() -> bool:
    return _env_bool("LUXE_RADAR_INDEX_ENABLED", True)


def default_db_path() -> Path:
    override = str(os.environ.get("LUXE_RADAR_INDEX_DB") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    root = Path(__file__).resolve().parent
    return (root / "instance" / "luxe_radar_index.sqlite3").resolve()


def max_age_seconds() -> int:
    minutes = _env_int("LUXE_RADAR_INDEX_MAX_AGE_MINUTES", 360, 5, 7 * 24 * 60)
    return minutes * 60


def query_limit() -> int:
    return _env_int("LUXE_RADAR_INDEX_QUERY_LIMIT", DEFAULT_QUERY_LIMIT, 100, 10000)


def min_instant_results() -> int:
    return _env_int("LUXE_RADAR_INDEX_MIN_INSTANT_RESULTS", 1, 1, 200)


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = []
    last_space = False
    for ch in text:
        if ch.isalnum():
            cleaned.append(ch)
            last_space = False
        elif not last_space:
            cleaned.append(" ")
            last_space = True
    return " ".join("".join(cleaned).split())[:180]


def canonical_query(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if canonicalize_search_query is not None:
        try:
            raw = canonicalize_search_query(raw)
        except Exception:
            pass
    return _fold(raw)


def _safe_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clean_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    # V3.7.x : rejeter les URL issues de gabarits de redirection non résolus
    # (ex. ``https://www.i-run.fr${searchAction}``) ou les schémas dangereux.
    # Un template n'est pas une URL navigable : l'indexer fabriquerait un lien
    # mort dans le catalogue.
    low = value.lower()
    if "$" in value or "{" in value or "}" in value:
        return ""
    if "%7b" in low or "%7d" in low or "%24" in low:
        return ""
    if low.startswith("javascript:") or low.startswith("data:"):
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    # Fragment does not identify a different listing and often changes per click.
    return parsed._replace(fragment="").geturl()[:2048]


def _offer_key(item: dict) -> str:
    marketplace = _fold(item.get("marketplace") or item.get("source") or "unknown")
    url = _clean_url(item.get("lien") or item.get("url") or "")
    title = _fold(item.get("titre") or item.get("title") or "")
    reference = _fold(item.get("reference") or "")
    # V3.7.x : déduplication robuste. Priorité (1) ID produit stable, (2) URL
    # canonique, (3) empreinte titre+marque+taille+prix pour les annonces qui
    # n'exposent ni ID ni URL fiable.
    product_id = _fold(
        item.get("id_produit")
        or item.get("product_id")
        or item.get("item_id")
        or item.get("annonce_id")
        or ""
    )
    price = round(_safe_float(item.get("prix_total", item.get("prix", 0))), 2)
    if product_id:
        stable = f"{marketplace}|pid:{product_id}"
    elif url:
        stable = f"{marketplace}|{url}"
    else:
        brand = _fold(item.get("marque") or item.get("brand") or "")
        size = _fold(item.get("taille") or item.get("size") or "")
        fingerprint = f"{title}|{brand}|{size}|{price:.2f}"
        stable = f"{marketplace}|{reference or fingerprint}"
    return hashlib.sha256(stable.encode("utf-8", "ignore")).hexdigest()


def _identity_level(item: dict) -> str:
    level = str(item.get("niveau_identite") or "possible").strip().casefold()
    return level if level in {"fort", "possible", "rejet"} else "possible"


def _risk_level(item: dict) -> str:
    value = str(item.get("risque_contrefacon") or "").strip().casefold()
    if value in {"faible", "low", "bas"}:
        return "low"
    if value in {"eleve", "élevé", "high", "fort"}:
        return "high"
    return "unknown"


def _publicish_payload(item: dict) -> dict:
    # Keep scalar values only: avoids serialising connector internals or large blobs.
    payload = {}
    for key, value in dict(item or {}).items():
        if value is None or isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, str):
            value = value[:2048] if key in {"lien", "url", "image"} else value[:600]
        payload[str(key)[:80]] = value
    payload.pop("_rank_index", None)
    return payload


def _contains_phrase(text: str, phrase: str) -> bool:
    hay = f" {_fold(text)} "
    target = _fold(phrase)
    return bool(target) and f" {target} " in hay


def _query_info(query: str):
    if understand_query is None:
        return None
    try:
        return understand_query(query)
    except Exception:
        return None


def _catalog_accept(item: dict, query: str) -> bool:
    """Conservative gate before an offer enters the reusable global catalogue.

    Exact-query rows remain available for diagnostics/explore mode, but the
    global catalogue must not learn obvious false positives. This is especially
    important for multi-word brands (Stone Island vs River Island) and explicit
    numbered models (Cloud 5 vs Cloud 6).
    """
    if _identity_level(item) == "rejet":
        return False
    title = str(item.get("titre") or item.get("title") or "").strip()
    if not title:
        return False
    # V3.7.x : une URL fournie mais non nettoyable (gabarit ``${...}``,
    # schéma invalide) rend l'offre inutilisable pour le catalogue.
    raw_url = str(item.get("lien") or item.get("url") or "").strip()
    if raw_url and not _clean_url(raw_url):
        return False
    info = _query_info(query)
    if info is None:
        q_tokens = canonical_query(query).split()
        return bool(q_tokens) and all(tok in _fold(title).split() for tok in q_tokens[:4])

    brand = str(getattr(info, "brand", "") or "").strip()
    model = str(getattr(info, "model", "") or "").strip()
    if brand:
        aliases = tuple(SEARCH_BRAND_ALIASES.get(brand, (brand,))) or (brand,)
        # The standalone word "on" is too ambiguous to be a safe brand anchor.
        if brand == "On":
            aliases = tuple(alias for alias in aliases if _fold(alias) != "on") or ("on cloud", "on running")
        if not any(_contains_phrase(title, alias) for alias in aliases):
            return False
    if model:
        model_fold = _fold(model)
        # Search-understanding already strips the brand when it identifies the
        # model, so an exact phrase anchor is a strong reusable-catalogue guard.
        if model_fold and not _contains_phrase(title, model_fold):
            return False
    return True


def _catalog_search_text(item: dict, query: str) -> str:
    fields = [
        query,
        item.get("titre") or item.get("title") or "",
        item.get("reference") or "",
        item.get("categorie") or "",
        item.get("marketplace") or item.get("source") or "",
    ]
    return _fold(" ".join(str(value or "") for value in fields))[:1200]


def _type_matches(title: str, product_type: str | None) -> bool:
    if not product_type:
        return True
    aliases = SEARCH_TYPE_ALIASES.get(product_type, (product_type,))
    folded = _fold(title)
    return any(_contains_phrase(folded, alias) for alias in aliases)


def _query_match_score(item: dict, query: str, *, info=None, q_key: str | None = None) -> float:
    """Cheap deterministic rank used after FTS/global-catalog retrieval.

    V3.7.x : le score provient du quality gate central ``evaluate_offer``
    (marque/modèle/gamme/catégorie en dur, couleur/sexe/matière en bonus).
    Il est purement déterministe : même requête + même offre => même score.
    """
    if _gate_evaluate is not None:
        try:
            result = _gate_evaluate_item(item, _intent_for(query))
            if result is not None:
                return result.score
        except Exception:  # pragma: no cover - defensive
            pass
    title = str(item.get("titre") or item.get("title") or "")
    title_fold = _fold(title)
    q_key = q_key if q_key is not None else canonical_query(query)
    info = info if info is not None else _query_info(query)
    score = _safe_float(item.get("score_identite"), 0.0)
    if q_key and _contains_phrase(title_fold, q_key):
        score += 80.0
    if info is not None:
        brand = str(getattr(info, "brand", "") or "")
        model = str(getattr(info, "model", "") or "")
        product_type = getattr(info, "product_type", None)
        if brand and any(_contains_phrase(title, alias) for alias in SEARCH_BRAND_ALIASES.get(brand, (brand,))):
            score += 40.0
        if model and _contains_phrase(title, model):
            score += 70.0
        if product_type and _type_matches(title, product_type):
            score += 20.0
    return score


def _brand_domain_rank(item: dict, q_key: str) -> int:
    """Désambiguise une marque commerciale sans supprimer les homonymes."""
    if q_key != "columbia":
        return 0
    title = _fold(str(item.get("titre") or item.get("title") or ""))
    apparel = (
        "veste", "jacket", "manteau", "coat", "pantalon", "pants", "trouser",
        "jogging", "short", "shirt", "chemise", "polaire", "fleece", "sweat",
        "chaussure", "shoe", "boot", "sandale", "casquette", "cap", "bonnet",
        "outdoor", "randonnee", "hiking", "omni tech", "omni heat", "titanium",
    )
    media = (
        "vinyl", " vinyle", " lp ", " cd ", " dvd", "blu ray", "album",
        "disque", "record", "soundtrack", "bande originale", "musique",
        "music", "single", "cassette", "film", "cinema",
    )
    padded = f" {title} "
    if any(term in padded for term in apparel):
        return 0
    if any(term in padded for term in media):
        return 2
    return 1


# Schéma initialisé une seule fois par chemin de base : les connexions
# suivantes ne rejouent plus le DDL (verrous exclusifs) sur le chemin chaud.
# Sans cela, le collecteur (écritures continues) faisait échouer les lectures
# web en « database is locked » sur Render (V3.8 prod).
_SCHEMA_READY_PATHS: set[str] = set()
_SCHEMA_READY_LOCK = threading.Lock()
_SCHEMA_PROBE_TIMEOUT_MS = 150
_SCHEMA_LOCK_WAIT_SECONDS = 0.25


def _schema_is_current_fast(db_path: Path) -> bool:
    """Detecte une DB deja migree sans rejouer le DDL sur le chemin web."""
    if not db_path.exists():
        return False
    probe = None
    try:
        probe = sqlite3.connect(
            str(db_path),
            timeout=_SCHEMA_PROBE_TIMEOUT_MS / 1000.0,
        )
        probe.execute(f"PRAGMA busy_timeout={_SCHEMA_PROBE_TIMEOUT_MS}")
        probe.execute("PRAGMA query_only=ON")
        row = probe.execute(
            "SELECT value FROM index_meta WHERE key='schema_version'"
        ).fetchone()
        return bool(row and int(row[0]) >= SCHEMA_VERSION)
    except (sqlite3.Error, TypeError, ValueError):
        return False
    finally:
        if probe is not None:
            probe.close()


@contextmanager
def _connect(path: Path | None = None):
    db_path = (path or default_db_path()).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    path_key = str(db_path)

    # Apres un restart Render, la DB persistante est normalement deja au bon
    # schema. Une lecture fail-fast suffit alors : pas de WAL/DDL/migration.
    schema_ready = path_key in _SCHEMA_READY_PATHS
    if not schema_ready and _schema_is_current_fast(db_path):
        schema_ready = True
        # Cache best-effort only: never wait on this lock on the web path.
        if _SCHEMA_READY_LOCK.acquire(blocking=False):
            try:
                _SCHEMA_READY_PATHS.add(path_key)
            finally:
                _SCHEMA_READY_LOCK.release()

    conn = sqlite3.connect(str(db_path), timeout=8.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA foreign_keys=ON")

        if not schema_ready and path_key not in _SCHEMA_READY_PATHS:
            # Ne jamais faire attendre indefiniment les threads web derriere
            # une initialisation de schema deja bloquee par SQLite.
            acquired = _SCHEMA_READY_LOCK.acquire(
                timeout=_SCHEMA_LOCK_WAIT_SECONDS
            )
            if not acquired:
                raise sqlite3.OperationalError("schema initialization busy")
            try:
                if path_key not in _SCHEMA_READY_PATHS:
                    conn.execute("PRAGMA journal_mode=WAL")
                    _ensure_schema(conn)
                    _SCHEMA_READY_PATHS.add(path_key)
            finally:
                _SCHEMA_READY_LOCK.release()

        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS indexed_results (
            query_key TEXT NOT NULL,
            offer_key TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            title TEXT NOT NULL,
            title_folded TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            total_price REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'EUR',
            identity_level TEXT NOT NULL DEFAULT 'possible',
            identity_score REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL DEFAULT 'unknown',
            category TEXT NOT NULL DEFAULT '',
            reference TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            image TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            first_seen REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (query_key, offer_key)
        );
        CREATE INDEX IF NOT EXISTS idx_results_query_updated
            ON indexed_results(query_key, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_results_query_market
            ON indexed_results(query_key, marketplace);
        CREATE INDEX IF NOT EXISTS idx_results_query_price
            ON indexed_results(query_key, total_price, price);
        CREATE INDEX IF NOT EXISTS idx_results_query_identity
            ON indexed_results(query_key, identity_level, identity_score DESC);

        CREATE TABLE IF NOT EXISTS catalog_offers (
            offer_key TEXT PRIMARY KEY,
            primary_query_key TEXT NOT NULL,
            search_text TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            title TEXT NOT NULL,
            title_folded TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            total_price REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'EUR',
            identity_level TEXT NOT NULL DEFAULT 'possible',
            identity_score REAL NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL DEFAULT 'unknown',
            category TEXT NOT NULL DEFAULT '',
            reference TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            image TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            first_seen REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_catalog_updated
            ON catalog_offers(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_catalog_market
            ON catalog_offers(marketplace);
        CREATE INDEX IF NOT EXISTS idx_catalog_price
            ON catalog_offers(total_price, price);
        CREATE INDEX IF NOT EXISTS idx_catalog_identity
            ON catalog_offers(identity_level, identity_score DESC);
        CREATE INDEX IF NOT EXISTS idx_catalog_title
            ON catalog_offers(title_folded);

        CREATE TABLE IF NOT EXISTS collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seed_query TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            page INTEGER NOT NULL DEFAULT 1,
            raw INTEGER NOT NULL DEFAULT 0,
            parsed INTEGER NOT NULL DEFAULT 0,
            relevant INTEGER NOT NULL DEFAULT 0,
            rejected INTEGER NOT NULL DEFAULT 0,
            new INTEGER NOT NULL DEFAULT 0,
            duplicates INTEGER NOT NULL DEFAULT 0,
            has_more INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            walked_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collector_seed_time
            ON collector_runs(seed_query, walked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_collector_market_time
            ON collector_runs(marketplace, walked_at DESC);

        CREATE TABLE IF NOT EXISTS collector_pending (
            seed_query TEXT PRIMARY KEY,
            folded TEXT NOT NULL,
            price_max REAL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collector_diag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collector_progress (
            query_key TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            next_page INTEGER NOT NULL DEFAULT 1,
            completed INTEGER NOT NULL DEFAULT 0,
            last_progress_at REAL NOT NULL,
            PRIMARY KEY(query_key, marketplace)
        );
        """
    )
    # Additive lifecycle migration: old databases remain usable in place.
    for table in ("indexed_results", "catalog_offers"):
        for column, ddl in (
            ("state", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
            ("last_seen", "REAL NOT NULL DEFAULT 0"),
            ("last_verified", "REAL NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass
        conn.execute(
            f"UPDATE {table} SET last_seen=updated_at WHERE last_seen<=0"
        )
        conn.execute(
            f"UPDATE {table} SET last_verified=updated_at WHERE last_verified<=0"
        )
    fts = "0"
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts "
            "USING fts5(offer_key UNINDEXED, search_text, tokenize='unicode61 remove_diacritics 2')"
        )
        fts = "1"
    except sqlite3.OperationalError:
        # Some minimal SQLite builds omit FTS5. LIKE fallback remains correct.
        fts = "0"

    # V3.8 : table collector_pending (vagues) avec colonne folded. Une base
    # antérieure n'a pas la colonne ni l'index : ALTER défensif puis index
    # unique replié (dédoublonnage canonical_query des vagues en attente).
    try:
        conn.execute("ALTER TABLE collector_pending ADD COLUMN folded TEXT")
    except sqlite3.OperationalError:
        pass  # colonne déjà présente (base neuve)
    try:
        conn.execute("UPDATE collector_pending SET folded=seed_query WHERE folded IS NULL OR folded=''")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_folded ON collector_pending(folded)"
        )
    except sqlite3.OperationalError:
        pass

    # One-time V3.6 -> V3.7 migration: reuse already warmed exact-query rows
    # instead of asking the user to rebuild Stone Island/Nike/etc. from zero.
    previous = conn.execute(
        "SELECT value FROM index_meta WHERE key='schema_version'"
    ).fetchone()
    try:
        previous_version = int(previous[0]) if previous else 0
    except (TypeError, ValueError):
        previous_version = 0
    if previous_version < SCHEMA_VERSION:
        legacy_rows = conn.execute(
            "SELECT query_key, offer_key, marketplace, title, title_folded, price, total_price, "
            "currency, identity_level, identity_score, score, confidence, risk_level, category, "
            "reference, url, image, payload_json, first_seen, updated_at "
            "FROM indexed_results ORDER BY updated_at DESC LIMIT 100000"
        ).fetchall()
        migrated = []
        for row in legacy_rows:
            try:
                item = json.loads(row["payload_json"])
            except Exception:
                continue
            if not isinstance(item, dict) or not _catalog_accept(item, str(row["query_key"] or "")):
                continue
            migrated.append((
                row["offer_key"], row["query_key"], _catalog_search_text(item, str(row["query_key"] or "")),
                row["marketplace"], row["title"], row["title_folded"], row["price"], row["total_price"],
                row["currency"], row["identity_level"], row["identity_score"], row["score"], row["confidence"],
                row["risk_level"], row["category"], row["reference"], row["url"], row["image"], row["payload_json"],
                row["first_seen"], row["updated_at"],
            ))
        if migrated:
            conn.executemany(
                "INSERT INTO catalog_offers(offer_key,primary_query_key,search_text,marketplace,title,title_folded,"
                "price,total_price,currency,identity_level,identity_score,score,confidence,risk_level,category,"
                "reference,url,image,payload_json,first_seen,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(offer_key) DO UPDATE SET search_text=excluded.search_text, marketplace=excluded.marketplace, "
                "title=excluded.title, title_folded=excluded.title_folded, price=excluded.price, total_price=excluded.total_price, "
                "currency=excluded.currency, identity_level=excluded.identity_level, identity_score=MAX(catalog_offers.identity_score,excluded.identity_score), "
                "score=MAX(catalog_offers.score,excluded.score), confidence=MAX(catalog_offers.confidence,excluded.confidence), "
                "risk_level=excluded.risk_level, category=excluded.category, reference=excluded.reference, url=excluded.url, "
                "image=excluded.image, payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                migrated,
            )
            if fts == "1":
                keys = [row[0] for row in migrated]
                conn.executemany("DELETE FROM catalog_fts WHERE offer_key = ?", ((key,) for key in keys))
                conn.executemany(
                    "INSERT INTO catalog_fts(offer_key, search_text) VALUES(?, ?)",
                    ((row[0], row[2]) for row in migrated),
                )

    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES('fts5', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (fts,),
    )


def upsert_results(results, query: str, *, path: Path | None = None, now: float | None = None) -> int:
    if not index_enabled():
        return 0
    query_key = canonical_query(query)
    if not query_key:
        return 0
    now = float(now if now is not None else time.time())
    rows = []
    catalog_rows = []
    for raw in results or []:
        if not isinstance(raw, dict):
            continue
        item = _publicish_payload(raw)
        # V3.7.x : horodatage de fraîcheur exposé sur chaque annonce indexée.
        # first_seen_at reste stable (colonne SQLite first_seen, jamais écrasée),
        # last_seen_at/last_verified_at sont rafraîchis à chaque scan réel.
        item["last_seen_at"] = now
        item["last_verified_at"] = now
        title = str(item.get("titre") or item.get("title") or "").strip()
        marketplace = str(item.get("marketplace") or item.get("source") or "").strip()
        if not title or not marketplace:
            continue
        price = _safe_float(item.get("prix"), 0.0)
        total_price = _safe_float(item.get("prix_total"), price)
        if total_price <= 0:
            total_price = price
        item["lien"] = _clean_url(item.get("lien") or item.get("url") or "")
        item["url"] = _clean_url(item.get("url") or item.get("lien") or "")
        offer_key = _offer_key(item)
        base = (
            marketplace[:120],
            title[:600],
            _fold(title),
            price,
            total_price,
            str(item.get("devise") or "EUR")[:12],
            _identity_level(item),
            _safe_float(item.get("score_identite"), 0.0),
            _safe_float(item.get("score"), 0.0),
            _safe_float(item.get("score_confiance"), 0.0),
            _risk_level(item),
            str(item.get("categorie") or "")[:120],
            str(item.get("reference") or "")[:180],
            item.get("lien") or item.get("url") or "",
            _clean_url(item.get("image") or ""),
            json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            now,
            now,
        )
        rows.append((query_key, offer_key, *base))
        if _catalog_accept(item, query):
            catalog_rows.append((
                offer_key, query_key, _catalog_search_text(item, query), *base
            ))
    if not rows:
        return 0
    exact_sql = """
        INSERT INTO indexed_results(
            query_key, offer_key, marketplace, title, title_folded,
            price, total_price, currency, identity_level, identity_score,
            score, confidence, risk_level, category, reference, url, image,
            payload_json, first_seen, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(query_key, offer_key) DO UPDATE SET
            marketplace=excluded.marketplace,
            title=excluded.title,
            title_folded=excluded.title_folded,
            price=excluded.price,
            total_price=excluded.total_price,
            currency=excluded.currency,
            identity_level=excluded.identity_level,
            identity_score=excluded.identity_score,
            score=excluded.score,
            confidence=excluded.confidence,
            risk_level=excluded.risk_level,
            category=excluded.category,
            reference=excluded.reference,
            url=excluded.url,
            image=excluded.image,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
    """
    catalog_sql = """
        INSERT INTO catalog_offers(
            offer_key, primary_query_key, search_text, marketplace, title, title_folded,
            price, total_price, currency, identity_level, identity_score, score,
            confidence, risk_level, category, reference, url, image, payload_json,
            first_seen, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(offer_key) DO UPDATE SET
            primary_query_key=excluded.primary_query_key,
            search_text=excluded.search_text,
            marketplace=excluded.marketplace,
            title=excluded.title,
            title_folded=excluded.title_folded,
            price=excluded.price,
            total_price=excluded.total_price,
            currency=excluded.currency,
            identity_level=excluded.identity_level,
            identity_score=MAX(catalog_offers.identity_score, excluded.identity_score),
            score=MAX(catalog_offers.score, excluded.score),
            confidence=MAX(catalog_offers.confidence, excluded.confidence),
            risk_level=excluded.risk_level,
            category=excluded.category,
            reference=excluded.reference,
            url=excluded.url,
            image=excluded.image,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
    """
    with _connect(path) as conn:
        conn.executemany(exact_sql, rows)
        if catalog_rows:
            conn.executemany(catalog_sql, catalog_rows)
            fts_enabled = str(conn.execute(
                "SELECT value FROM index_meta WHERE key='fts5'"
            ).fetchone()[0]) == "1"
            if fts_enabled:
                keys = [row[0] for row in catalog_rows]
                # executemany keeps this robust on SQLite builds with a small
                # variable limit and the warmed batches are intentionally bounded.
                conn.executemany("DELETE FROM catalog_fts WHERE offer_key = ?", ((key,) for key in keys))
                refreshed = conn.execute(
                    f"SELECT offer_key, search_text FROM catalog_offers WHERE offer_key IN ({','.join('?' for _ in keys)})",
                    keys,
                ).fetchall() if keys else []
                conn.executemany(
                    "INSERT INTO catalog_fts(offer_key, search_text) VALUES(?, ?)",
                    ((row["offer_key"], row["search_text"]) for row in refreshed),
                )
        keys = [row[1] for row in rows]
        if keys:
            marks = [(now, now, query_key, key) for key in keys]
            conn.executemany(
                "UPDATE indexed_results SET state='ACTIVE',last_seen=?,last_verified=? "
                "WHERE query_key=? AND offer_key=?", marks,
            )
        if catalog_rows:
            conn.executemany(
                "UPDATE catalog_offers SET state='ACTIVE',last_seen=?,last_verified=? WHERE offer_key=?",
                ((now, now, row[0]) for row in catalog_rows),
            )
    return len(rows)


def collector_progress(query: str, marketplace: str, *, path: Path | None = None) -> dict:
    """Return a persisted pagination cursor for one query/source pair."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT next_page,completed,last_progress_at FROM collector_progress "
            "WHERE query_key=? AND marketplace=?",
            (canonical_query(query), str(marketplace)),
        ).fetchone()
    return dict(row) if row else {"next_page": 1, "completed": 0, "last_progress_at": 0.0}


def save_collector_progress(query: str, marketplace: str, next_page: int, completed: bool,
                            *, path: Path | None = None, now: float | None = None) -> None:
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO collector_progress(query_key,marketplace,next_page,completed,last_progress_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(query_key,marketplace) DO UPDATE SET "
            "next_page=excluded.next_page,completed=excluded.completed,last_progress_at=excluded.last_progress_at",
            (canonical_query(query), str(marketplace), max(1, int(next_page)), int(bool(completed)),
             float(now if now is not None else time.time())),
        )


def mark_offers_dead(offer_keys, *, path: Path | None = None) -> int:
    """Hide offers confirmed gone by a connector/verifier; never guesses from age."""
    keys = [str(key) for key in offer_keys or [] if str(key)]
    if not keys:
        return 0
    with _connect(path) as conn:
        changed = 0
        for key in keys:
            changed += conn.execute(
                "UPDATE catalog_offers SET state='DEAD' WHERE offer_key=? AND state!='DEAD'", (key,)
            ).rowcount
            conn.execute("UPDATE indexed_results SET state='DEAD' WHERE offer_key=?", (key,))
    return changed


def record_collector_run(*, seed_query, marketplace, page, raw=0, parsed=0, relevant=0,
                         rejected=0, new=0, duplicates=0, has_more=False, blocked=False,
                         latency_ms=0, path: Path | None = None, now: float | None = None) -> bool:
    """Trace une marche source/page du collecteur (hors hot path).

    `raw` = cartes rendues par le connecteur ; `parsed` = cartes normalisées
    avec clé stable ; `relevant` = cartes acceptées (gate/catalogue) ;
    `rejected` = parsed - relevant (rejets de pertinence) ;
    `new` = clés réellement insérées (jamais présentes avant) ;
    `duplicates` = parsed - new. Aucune valeur n'est inventée : tout provient
    d'une vraie passe réseau.
    """
    if not index_enabled():
        return False
    now = float(now if now is not None else time.time())
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO collector_runs(seed_query, marketplace, page, raw, parsed, relevant, "
            "rejected, new, duplicates, has_more, blocked, latency_ms, walked_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(seed_query or ""), str(marketplace or ""), int(page or 1),
             int(raw or 0), int(parsed or 0), int(relevant or 0), int(rejected or 0),
             int(new or 0), int(duplicates or 0), 1 if has_more else 0, 1 if blocked else 0,
             int(latency_ms or 0), now),
        )
    return True


def known_offer_keys(offer_keys, query: str, *, path: Path | None = None) -> set[str]:
    """Sous-ensemble de clés déjà présentes (requête exacte ou catalogue).

    Permet au collecteur de compter des offres réellement nouvelles par page :
    une clé est « new » seulement si absente d'`indexed_results(query_key)` et
    de `catalog_offers`. Une passe re-marchée ne compte jamais une annonce
    déjà collectée comme nouvelle.
    """
    keys = [str(k) for k in (offer_keys or []) if k]
    if not keys or not index_enabled():
        return set()
    query_key = canonical_query(query)
    known: set[str] = set()
    placeholders = ",".join("?" for _ in keys)
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT offer_key FROM indexed_results WHERE query_key=? AND offer_key IN ({placeholders})",
            [query_key, *keys],
        ).fetchall()
        known.update(row[0] for row in rows)
        if len(known) < len(keys):
            remaining = [k for k in keys if k not in known]
            rows = conn.execute(
                f"SELECT offer_key FROM catalog_offers WHERE offer_key IN ({','.join('?' for _ in remaining)})",
                remaining,
            ).fetchall()
            known.update(row[0] for row in rows)
    return known


def collector_stats(*, seed_query=None, marketplace=None, path: Path | None = None) -> dict:
    """Totaux agrégés des passes du collecteur (pages, doublons, latence...)."""
    result = {
        "seed_query": seed_query,
        "marketplace": marketplace,
        "runs": 0, "pages": 0, "raw": 0, "parsed": 0, "relevant": 0,
        "rejected": 0, "new": 0, "duplicates": 0, "blocked_pages": 0, "has_more_pages": 0,
        "last_run_at": None, "sources": {},
    }
    if not index_enabled():
        return result
    where = ["1=1"]
    params = []
    if seed_query:
        where.append("seed_query=?")
        params.append(seed_query)
    if marketplace:
        where.append("marketplace=?")
        params.append(marketplace)
    where_sql = " AND ".join(where)
    with _connect(path) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS runs, COALESCE(SUM(raw),0) AS raw, COALESCE(SUM(parsed),0) AS parsed, "
            f"COALESCE(SUM(relevant),0) AS relevant, COALESCE(SUM(rejected),0) AS rejected, "
            f"COALESCE(SUM(new),0) AS new, "
            f"COALESCE(SUM(duplicates),0) AS duplicates, COALESCE(SUM(blocked),0) AS blocked, "
            f"COALESCE(SUM(has_more),0) AS has_more, MAX(walked_at) AS last_run_at "
            f"FROM collector_runs WHERE {where_sql}",
            params,
        ).fetchone()
        result["runs"] = int(row["runs"] or 0)
        result["pages"] = result["runs"]
        result["raw"] = int(row["raw"] or 0)
        result["parsed"] = int(row["parsed"] or 0)
        result["relevant"] = int(row["relevant"] or 0)
        result["rejected"] = int(row["rejected"] or 0)
        result["new"] = int(row["new"] or 0)
        result["duplicates"] = int(row["duplicates"] or 0)
        result["blocked_pages"] = int(row["blocked"] or 0)
        result["has_more_pages"] = int(row["has_more"] or 0)
        result["last_run_at"] = row["last_run_at"]
        per_source = conn.execute(
            f"SELECT marketplace, COUNT(*) AS runs, COALESCE(SUM(raw),0) AS raw, "
            f"COALESCE(SUM(parsed),0) AS parsed, COALESCE(SUM(relevant),0) AS relevant, "
            f"COALESCE(SUM(rejected),0) AS rejected, "
            f"COALESCE(SUM(new),0) AS new, COALESCE(SUM(duplicates),0) AS duplicates, "
            f"COALESCE(SUM(blocked),0) AS blocked, COALESCE(SUM(has_more),0) AS has_more, "
            f"MAX(walked_at) AS last_run_at FROM collector_runs WHERE {where_sql} "
            f"GROUP BY marketplace ORDER BY new DESC",
            params,
        ).fetchall()
        result["sources"] = {r["marketplace"]: dict(r) for r in per_source}
    return result


def collector_has_recent(seed_query, max_age_seconds: int = 24 * 60 * 60, *, path: Path | None = None) -> bool:
    """Vrai si une passe collector existe pour le seed dans `max_age_seconds`."""
    if not index_enabled():
        return False
    now = time.time()
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM collector_runs WHERE seed_query=? AND walked_at >= ?",
            (seed_query, now - float(max_age_seconds)),
        ).fetchone()
        return bool(row and row["n"] > 0)


def recent_collector_runs(limit: int = 8, *, path: Path | None = None) -> list[dict]:
    """Dernières passes source/page enregistrées (partagées entre process).

    Permet au panneau de statut d'être sincère même quand le process qui sert
    la requête n'est pas celui qui marche (multi-workers) : les runs sont lues
    directement depuis la table partagée.
    """
    if not index_enabled():
        return []
    limit = max(1, min(int(limit or 8), 20))
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT seed_query, marketplace, page, raw, parsed, relevant, rejected, "
            "new, duplicates, has_more, blocked, latency_ms, walked_at "
            "FROM collector_runs ORDER BY walked_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "query": str(r["seed_query"] or ""),
            "marketplace": str(r["marketplace"] or ""),
            "page": int(r["page"] or 1),
            "new": int(r["new"] or 0),
            "raw": int(r["raw"] or 0),
            "relevant": int(r["relevant"] or 0),
            "rejected": int(r["rejected"] or 0),
            "duplicates": int(r["duplicates"] or 0),
            "blocked": bool(r["blocked"]),
            "latency_ms": int(r["latency_ms"] or 0),
            "walked_at": r["walked_at"],
        }
        for r in rows
    ]


def collector_status_snapshot(*, path: Path | None = None, recent_limit: int = 8,
                              diag_limit: int = 12, timeout_ms: int = 150) -> dict:
    """Snapshot lecture seule et fail-fast pour ``/api/collector/status``.

    Le panneau de diagnostic ne doit jamais hériter du ``busy_timeout`` global
    de 8 secondes. On ouvre une seule connexion sans DDL/WAL et on regroupe
    stats + dernières passes + diagnostics. En cas de contention, l'appel rend
    immédiatement un état partiel au lieu de bloquer un thread web.
    """
    stats = {
        "seed_query": None, "marketplace": None,
        "runs": 0, "pages": 0, "raw": 0, "parsed": 0, "relevant": 0,
        "rejected": 0, "new": 0, "duplicates": 0, "blocked_pages": 0,
        "has_more_pages": 0, "last_run_at": None, "sources": {},
    }
    snapshot = {
        "db_available": False,
        "error": None,
        "stats": stats,
        "recent_runs": [],
        "diag": [],
    }
    if not index_enabled():
        snapshot["db_available"] = True
        return snapshot

    db_path = (path or default_db_path()).resolve()
    if not db_path.exists():
        snapshot["error"] = "index not initialized"
        return snapshot

    recent_limit = max(1, min(int(recent_limit or 8), 20))
    diag_limit = max(1, min(int(diag_limit or 12), 30))
    timeout_ms = max(10, min(int(timeout_ms or 150), 1000))
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=timeout_ms / 1000.0)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        conn.execute("PRAGMA query_only=ON")

        row = conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(raw),0) AS raw, "
            "COALESCE(SUM(parsed),0) AS parsed, COALESCE(SUM(relevant),0) AS relevant, "
            "COALESCE(SUM(rejected),0) AS rejected, COALESCE(SUM(new),0) AS new, "
            "COALESCE(SUM(duplicates),0) AS duplicates, COALESCE(SUM(blocked),0) AS blocked, "
            "COALESCE(SUM(has_more),0) AS has_more, MAX(walked_at) AS last_run_at "
            "FROM collector_runs"
        ).fetchone()
        stats.update({
            "runs": int(row["runs"] or 0),
            "pages": int(row["runs"] or 0),
            "raw": int(row["raw"] or 0),
            "parsed": int(row["parsed"] or 0),
            "relevant": int(row["relevant"] or 0),
            "rejected": int(row["rejected"] or 0),
            "new": int(row["new"] or 0),
            "duplicates": int(row["duplicates"] or 0),
            "blocked_pages": int(row["blocked"] or 0),
            "has_more_pages": int(row["has_more"] or 0),
            "last_run_at": row["last_run_at"],
        })
        per_source = conn.execute(
            "SELECT marketplace, COUNT(*) AS runs, COALESCE(SUM(raw),0) AS raw, "
            "COALESCE(SUM(parsed),0) AS parsed, COALESCE(SUM(relevant),0) AS relevant, "
            "COALESCE(SUM(rejected),0) AS rejected, COALESCE(SUM(new),0) AS new, "
            "COALESCE(SUM(duplicates),0) AS duplicates, COALESCE(SUM(blocked),0) AS blocked, "
            "COALESCE(SUM(has_more),0) AS has_more, MAX(walked_at) AS last_run_at "
            "FROM collector_runs GROUP BY marketplace ORDER BY new DESC"
        ).fetchall()
        stats["sources"] = {r["marketplace"]: dict(r) for r in per_source}

        rows = conn.execute(
            "SELECT seed_query, marketplace, page, raw, parsed, relevant, rejected, "
            "new, duplicates, has_more, blocked, latency_ms, walked_at "
            "FROM collector_runs ORDER BY walked_at DESC, rowid DESC LIMIT ?",
            (recent_limit,),
        ).fetchall()
        snapshot["recent_runs"] = [
            {
                "query": str(r["seed_query"] or ""),
                "marketplace": str(r["marketplace"] or ""),
                "page": int(r["page"] or 1),
                "new": int(r["new"] or 0),
                "raw": int(r["raw"] or 0),
                "relevant": int(r["relevant"] or 0),
                "rejected": int(r["rejected"] or 0),
                "duplicates": int(r["duplicates"] or 0),
                "blocked": bool(r["blocked"]),
                "latency_ms": int(r["latency_ms"] or 0),
                "walked_at": r["walked_at"],
            }
            for r in rows
        ]
        diag_rows = conn.execute(
            "SELECT ts, message FROM collector_diag ORDER BY id DESC LIMIT ?",
            (diag_limit,),
        ).fetchall()
        snapshot["diag"] = [
            {"ts": float(r["ts"]), "message": str(r["message"])}
            for r in diag_rows
        ]
        snapshot["db_available"] = True
    except sqlite3.Error as exc:
        snapshot["error"] = str(exc)[:160]
    finally:
        if conn is not None:
            conn.close()
    return snapshot


def collector_diag_write(message: str, *, path: Path | None = None) -> bool:
    """Trace diagnostique partagée entre process (boot walker, sortie thread)."""
    if not index_enabled():
        return False
    try:
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO collector_diag(ts, message) VALUES(?,?)",
                (time.time(), str(message or "")[:400]),
            )
        return True
    except Exception:  # noqa: BLE001 - le diagnostic ne doit jamais casser l'app
        return False


def collector_diag_read(limit: int = 12, *, path: Path | None = None) -> list[dict]:
    if not index_enabled():
        return []
    limit = max(1, min(int(limit or 12), 30))
    try:
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT ts, message FROM collector_diag ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"ts": float(r["ts"]), "message": str(r["message"])} for r in rows]


def queue_collector_trigger(seed_query: str, price_max=None, *, path: Path | None = None) -> bool:
    """Persiste un déclencheur de vague dans la base (partagée entre process).

    En multi-workers, le process qui reçoit la requête web n'est pas forcément
    celui dont le thread collecteur marche : une file en mémoire y serait
    perdue. La table collector_pending garantit qu'un process vivant absorbera
    la vague (drain_collector_triggers). Retourne True si inséré, False si déjà
    en attente (dédupliqué).
    """
    query = str(seed_query or "").strip()
    if not query or not index_enabled():
        return False
    try:
        price_max = float(price_max) if price_max not in (None, "") else None
        if price_max is not None and price_max <= 0:
            price_max = None
    except (TypeError, ValueError):
        price_max = None
    with _connect(path) as conn:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO collector_pending(seed_query, folded, price_max, created_at) "
                "VALUES(?,?,?,?)",
                (query, canonical_query(query), price_max, time.time()),
            )
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                return False
            raise
        return bool(cur.rowcount and cur.rowcount > 0)


def drain_collector_triggers(limit: int = 20, *, path: Path | None = None) -> list[tuple[str, float]]:
    """Absorbe atomiquement les déclencheurs en attente (une fois par vague).

    SELECT puis DELETE dans la même transaction : chaque vague est livrée à
    exactement un process (au pire un doublon si deux process la volent).
    """
    if not index_enabled():
        return []
    limit = max(1, min(int(limit or 20), 100))
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT seed_query, price_max FROM collector_pending ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return []
        conn.executemany(
            "DELETE FROM collector_pending WHERE seed_query=?",
            [(r["seed_query"],) for r in rows],
        )
        return [(str(r["seed_query"]), r["price_max"]) for r in rows]


def count_query_offers(query, *, path: Path | None = None) -> dict:
    """Offres indexées pour une requête : table exacte, catalogue, total.

    Utilisé par le benchmark avant/après du collecteur pour mesurer la
    profondeur réelle gagnée par seed, sans jamais compter d'annonce inventée.
    """
    result = {"exact": 0, "catalog": 0, "catalog_total": 0}
    if not index_enabled():
        return result
    query_key = canonical_query(query)
    with _connect(path) as conn:
        result["exact"] = int(conn.execute(
            "SELECT COUNT(*) AS n FROM indexed_results WHERE query_key=?", (query_key,)
        ).fetchone()["n"] or 0)
        result["catalog"] = int(conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_offers WHERE primary_query_key=?", (query_key,)
        ).fetchone()["n"] or 0)
        result["catalog_total"] = int(conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_offers"
        ).fetchone()["n"] or 0)
    return result


def _sort_clause(sort: str) -> str:
    return {
        "price_asc": "COALESCE(NULLIF(total_price,0), price) ASC, identity_score DESC, updated_at DESC",
        "price_desc": "COALESCE(NULLIF(total_price,0), price) DESC, identity_score DESC, updated_at DESC",
        "score": "score DESC, identity_score DESC, updated_at DESC",
        "confidence": "confidence DESC, identity_score DESC, updated_at DESC",
        "marketplace": "marketplace COLLATE NOCASE ASC, identity_score DESC, updated_at DESC",
        "relevance": "identity_score DESC, score DESC, confidence DESC, updated_at DESC",
    }.get(str(sort or "relevance"), "identity_score DESC, score DESC, confidence DESC, updated_at DESC")


def _apply_sql_filters(where: list[str], params: list[object], *, marketplace, price_min, price_max, identity, risk):
    if marketplace and marketplace != "Toutes":
        where.append("marketplace = ?")
        params.append(str(marketplace))
    if price_min not in (None, ""):
        where.append("COALESCE(NULLIF(total_price,0), price) >= ?")
        params.append(max(0.0, _safe_float(price_min, 0.0)))
    if price_max not in (None, ""):
        where.append("COALESCE(NULLIF(total_price,0), price) <= ?")
        params.append(max(0.0, _safe_float(price_max, 0.0)))
    identity = str(identity or "all")
    if identity == "confirmed":
        where.append("identity_level IN ('fort','possible')")
    elif identity == "strong":
        where.append("identity_level = 'fort'")
    elif identity == "unverified":
        where.append("identity_level != 'fort'")
    if risk == "hide_high":
        where.append("risk_level != 'high'")
    elif risk == "low_only":
        where.append("risk_level = 'low'")


def _fts_terms(query: str) -> list[str]:
    key = canonical_query(query)
    tokens = [tok for tok in key.split() if len(tok) >= 2 or tok.isdigit()]
    info = _query_info(query)
    if info is not None:
        brand = str(getattr(info, "brand", "") or "")
        model = str(getattr(info, "model", "") or "")
        if brand:
            required = _fold(brand).split()
            if model:
                required.extend(_fold(model).split())
            elif not getattr(info, "product_type", None):
                # Les modèles libres que le dictionnaire ne connaît pas encore
                # (ex. « Columbia Tech Wind ») doivent tout de même raffiner la
                # marque. Avant ce garde-fou, comprendre Columbia suffisait et
                # les mots Tech/Wind étaient supprimés : la requête renvoyait
                # exactement les mêmes milliers de lignes que « Columbia ».
                brand_tokens = set(_fold(brand).split())
                residual = [token for token in tokens if token not in brand_tokens]
                required.extend(residual)
            # Recherche par famille : les pantalons Nike Trail peuvent être
            # publiés sous ACG, Dawn Range, Phenom Elite ou Storm-FIT sans le
            # mot « trail ». Récupérer le catalogue Nike, puis laisser le gate
            # central exiger pantalon + appartenance à cette famille.
            if (
                brand == "Nike"
                and _fold(model) == "trail"
                and str(getattr(info, "product_type", "") or "") == "pantalon"
            ):
                required = _fold(brand).split()
            # Product-type refinements are filtered after retrieval so French
            # "veste" can match English "jacket" titles.
            if required:
                tokens = required
    seen = set()
    return [tok for tok in tokens if not (tok in seen or seen.add(tok))][:8]


def _global_candidates(conn, query: str, *, cutoff: float, marketplace, price_min, price_max, identity, risk, cap: int):
    where = ["updated_at >= ?", "state != 'DEAD'"]
    params: list[object] = [cutoff]
    _apply_sql_filters(where, params, marketplace=marketplace, price_min=price_min, price_max=price_max, identity=identity, risk=risk)
    terms = _fts_terms(query)
    if not terms:
        return []
    fts_row = conn.execute("SELECT value FROM index_meta WHERE key='fts5'").fetchone()
    fts_enabled = bool(fts_row and str(fts_row[0]) == "1")
    rows = []
    if fts_enabled:
        expression = " AND ".join(f"{term}*" for term in terms)
        try:
            rows = conn.execute(
                "SELECT c.payload_json, c.updated_at, c.offer_key, c.first_seen "
                "FROM catalog_fts f JOIN catalog_offers c ON c.offer_key=f.offer_key "
                f"WHERE catalog_fts MATCH ? AND {' AND '.join('c.' + part if part.startswith(('updated_at','marketplace','COALESCE','identity_level','risk_level')) else part for part in where)} "
                "ORDER BY bm25(catalog_fts), c.identity_score DESC, c.updated_at DESC LIMIT ?",
                [expression, *params, cap],
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    if not rows:
        like_where = list(where)
        like_params = list(params)
        for term in terms:
            like_where.append("search_text LIKE ?")
            like_params.append(f"%{term}%")
        rows = conn.execute(
            f"SELECT payload_json, updated_at, offer_key, first_seen FROM catalog_offers WHERE {' AND '.join(like_where)} "
            "ORDER BY identity_score DESC, score DESC, updated_at DESC LIMIT ?",
            [*like_params, cap],
        ).fetchall()
    # Ne pas exécuter le quality gate ici : ``search`` fusionne d'abord ces
    # lignes avec le cache de requête exacte, puis applique le même gate une
    # seule fois sur les offres uniques. Le faire avant ET après la fusion
    # doublait presque le coût des recherches populaires (Adidas Samba).
    out = []
    for row in rows:
        try:
            item = json.loads(row["payload_json"])
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        item["_index_cached"] = True
        item["_index_global"] = True
        item["_index_updated_at"] = float(row["updated_at"])
        item["_index_first_seen"] = float(row["first_seen"] or row["updated_at"])
        out.append((str(row["offer_key"]), item, float(row["updated_at"])))
    return out


def _final_tiebreak(item: dict) -> tuple:
    """Dernier critère de tri : rend l'ordre total déterministe (aucun hasard)."""
    return (
        str(item.get("marketplace") or "").casefold(),
        _fold(str(item.get("titre") or item.get("title") or "")),
        _safe_float(item.get("prix_total"), _safe_float(item.get("prix"), 0.0)),
        str(item.get("lien") or item.get("url") or ""),
    )


def _python_sort(items: list[dict], sort: str, query: str, learning_scores: dict | None = None) -> list[dict]:
    identity_order = {"fort": 0, "possible": 1, "rejet": 2}
    info = _query_info(query)
    q_key = canonical_query(query)
    learning_scores = learning_scores or {}
    rank_cache = {}
    def rank(item):
        key = id(item)
        if key not in rank_cache:
            rank_cache[key] = _query_match_score(item, query, info=info, q_key=q_key)
        return rank_cache[key]
    if sort == "price_asc":
        return sorted(items, key=lambda item: (_safe_float(item.get("prix_total"), _safe_float(item.get("prix"), float("inf"))), -rank(item), *_final_tiebreak(item)))
    if sort == "price_desc":
        return sorted(items, key=lambda item: (-_safe_float(item.get("prix_total"), _safe_float(item.get("prix"), -1)), -rank(item), *_final_tiebreak(item)))
    if sort == "marketplace":
        return sorted(items, key=lambda item: (str(item.get("marketplace") or "").casefold(), -rank(item), *_final_tiebreak(item)))
    if sort == "score":
        return sorted(items, key=lambda item: (-_safe_float(item.get("score"), 0), -rank(item), *_final_tiebreak(item)))
    if sort == "confidence":
        return sorted(items, key=lambda item: (-_safe_float(item.get("score_confiance"), 0), -rank(item), *_final_tiebreak(item)))
    return sorted(items, key=lambda item: (
        _brand_domain_rank(item, q_key),
        1 if item.get("offer_state") == "STALE" else 0,
        identity_order.get(_identity_level(item), 1),
        -(rank(item) + max(-2.0, min(2.0, _safe_float(
            learning_scores.get(str(item.get("marketplace") or ""), learning_scores.get("", 0.0)), 0.0
        )))),
        -_safe_float(item.get("score_identite"), 0),
        -_safe_float(item.get("score"), 0),
        *_final_tiebreak(item),
    ))


def search(
    query: str,
    *,
    price_max: float | None = None,
    price_min: float | None = None,
    marketplace: str = "Toutes",
    identity: str = "all",
    risk: str = "all",
    sort: str = "relevance",
    limit: int | None = None,
    offset: int = 0,
    max_age: int | None = None,
    path: Path | None = None,
) -> IndexSearch:
    if not index_enabled():
        return IndexSearch([], 0, None, canonical_query(query))
    query_key = canonical_query(query)
    if not query_key:
        return IndexSearch([], 0, None, query_key)
    limit = max(1, min(int(limit or query_limit()), query_limit()))
    offset = max(0, int(offset))
    now_ts = time.time()
    # Freshness window. When max_age is explicit (tests, callers) it stays a hard
    # cap. By default the SQL window widens to the loosest marketplace TTL so the
    # per-marketplace freshness below can actually keep slower offers (eBay,
    # retail) available while Vinted still expires after 6h.
    if max_age is not None:
        cutoff = now_ts - max(60, int(max_age))
    else:
        window = max(
            max_age_seconds(),
            *[_marketplace_ttl_seconds(m) for m in MARKETPLACE_TTL_SECONDS],
            30 * 24 * 60 * 60,
        )
        cutoff = now_ts - max(60, window)
    # Catalogue bounded to <= 10k rows: fetching the complete candidate set keeps
    # deep pagination totals exact (page 64, 100, etc.) while SQLite stays fast.
    fetch_cap = query_limit()

    exact_where = ["query_key = ?", "updated_at >= ?", "state != 'DEAD'"]
    exact_params: list[object] = [query_key, cutoff]
    _apply_sql_filters(exact_where, exact_params, marketplace=marketplace, price_min=price_min, price_max=price_max, identity=identity, risk=risk)
    with _connect(path) as conn:
        exact_rows = conn.execute(
            f"SELECT payload_json, updated_at, offer_key, first_seen FROM indexed_results WHERE {' AND '.join(exact_where)} "
            f"ORDER BY {_sort_clause(sort)} LIMIT ?",
            [*exact_params, fetch_cap],
        ).fetchall()
        global_rows = _global_candidates(
            conn, query, cutoff=cutoff, marketplace=marketplace, price_min=price_min,
            price_max=price_max, identity=identity, risk=risk, cap=query_limit(),
        )
        learning_scores = {}
        if LEARN_RANKING_ENABLED:
            try:
                learning_scores = {
                    str(row["marketplace"]): float(row["bonus"] or 0.0)
                    for row in conn.execute(
                        "SELECT marketplace,bonus FROM learn_signals WHERE query_key=?",
                        (query_key,),
                    ).fetchall()
                }
            except sqlite3.OperationalError:
                learning_scores = {}

    merged: dict[str, tuple[dict, float]] = {}
    for row in exact_rows:
        try:
            item = json.loads(row["payload_json"])
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        item["_index_cached"] = True
        item["_index_updated_at"] = float(row["updated_at"])
        item["_index_first_seen"] = float(row["first_seen"] or row["updated_at"])
        merged[str(row["offer_key"])] = (item, float(row["updated_at"]))
    for offer_key, item, updated in global_rows:
        current = merged.get(offer_key)
        if current is None or updated > current[1]:
            merged[offer_key] = (item, updated)

    # V3.7.x : fraîcheur PAR MARKETPLACE. Chaque offre est expirée selon son
    # propre TTL ; une annonce Vinted de 12h disparaît alors qu'une offre eBay
    # équivalente de 48h reste visible.
    now_ttl = time.time()
    # Keep old, unconfirmed offers for cold starts, but never present them as
    # fresh. A connector/verifier must explicitly mark a missing offer DEAD.
    for item, updated in merged.values():
        item["offer_state"] = (
            "ACTIVE" if updated >= now_ttl - _marketplace_ttl_seconds(item.get("marketplace")) else "STALE"
        )

    # V3.7.x : gate de pertinence central sur TOUTES les offres fusionnées
    # (lignes exact-query comprises). Garantit la propriété de sous-ensemble :
    # résultats("casquette Nike Trail") ⊆ résultats("Nike Trail").
    intent = _intent_for(query)
    if intent is not None:
        merged = {
            key: (item, updated)
            for key, (item, updated) in merged.items()
            if _gate_accepts(item, intent)
        }

    # Expose explicit freshness markers on every returned offer.
    for item, updated in merged.values():
        first = float(item.get("_index_first_seen") or updated or 0.0)
        item["first_seen_at"] = first
        item["last_seen_at"] = updated
        item["last_verified_at"] = updated

    ordered = _python_sort(
        [item for item, _updated in merged.values()], sort, query, learning_scores
    )
    for item in ordered:
        item.pop("_quality", None)
    total = min(len(ordered), query_limit())
    page = ordered[offset:offset + limit]
    newest = max((float(item.get("_index_updated_at") or 0) for item in page), default=0.0)
    age = max(0.0, time.time() - newest) if newest else None
    return IndexSearch(page, total, age, query_key)


def search_universe(
    brands,
    query: str = "",
    *,
    price_max: float | None = None,
    price_min: float | None = None,
    marketplace: str = "Toutes",
    identity: str = "confirmed",
    risk: str = "all",
    sort: str = "relevance",
    limit: int = 200,
    offset: int = 0,
    path: Path | None = None,
) -> IndexSearch:
    """Universe browsing (ex. Luxe): all fresh catalogue offers whose title
    carries one of the configured brand entities, ranked against ``query``.

    Never treats the universe name as literal text: ``luxe`` is not a product.
    Only real connector offers that mention one of the brands can appear.
    """
    if not index_enabled():
        return IndexSearch([], 0, None, canonical_query(query))
    brands = [b for b in (brands or []) if _fold(b)]
    if not brands:
        return IndexSearch([], 0, None, canonical_query(query))
    limit = max(1, min(int(limit or query_limit()), query_limit()))
    offset = max(0, int(offset))
    window = max(
        max_age_seconds(),
        *[_marketplace_ttl_seconds(m) for m in MARKETPLACE_TTL_SECONDS],
    )
    cutoff = time.time() - max(60, window)
    where = ["updated_at >= ?"]
    params: list[object] = [cutoff]
    _apply_sql_filters(where, params, marketplace=marketplace, price_min=price_min, price_max=price_max, identity=identity, risk=risk)
    brand_clauses = []
    for brand in brands:
        phrase = _fold(brand)
        if not phrase:
            continue
        escaped = phrase.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        brand_clauses.append("title_folded LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    if not brand_clauses:
        return IndexSearch([], 0, None, canonical_query(query))
    where.append("(" + " OR ".join(brand_clauses) + ")")
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT payload_json, updated_at, offer_key, first_seen FROM catalog_offers "
            f"WHERE {' AND '.join(where)} ORDER BY identity_score DESC, updated_at DESC LIMIT ?",
            [*params, query_limit()],
        ).fetchall()
    items = []
    now_ttl = time.time()
    for row in rows:
        try:
            item = json.loads(row["payload_json"])
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        if float(row["updated_at"]) < now_ttl - _marketplace_ttl_seconds(item.get("marketplace")):
            continue
        item["_index_cached"] = True
        item["_index_global"] = True
        item["_index_updated_at"] = float(row["updated_at"])
        item["_index_first_seen"] = float(row["first_seen"] or row["updated_at"])
        item["first_seen_at"] = float(row["first_seen"] or row["updated_at"])
        item["last_seen_at"] = float(row["updated_at"])
        item["last_verified_at"] = float(row["updated_at"])
        items.append(item)
    ordered = _python_sort(items, sort, query)
    total = min(len(ordered), query_limit())
    return IndexSearch(ordered[offset:offset + limit], total, None, canonical_query(query))


def suggest(query: str, *, limit: int = 8, path: Path | None = None) -> list[dict]:
    """Fast catalogue-backed search assistance for header/home/radar inputs."""
    key = canonical_query(query)
    if len(key) < 2 or not index_enabled():
        return []
    limit = max(1, min(int(limit or 8), 12))
    now_cutoff = time.time() - max_age_seconds()
    out = []
    seen = set()
    with _connect(path) as conn:
        query_rows = conn.execute(
            "SELECT query_key, COUNT(*) AS n, MIN(COALESCE(NULLIF(total_price,0),price)) AS min_price "
            "FROM indexed_results WHERE updated_at >= ? AND identity_level IN ('fort','possible') AND query_key LIKE ? "
            "GROUP BY query_key ORDER BY n DESC, query_key LIMIT ?",
            (now_cutoff, f"{key}%", limit),
        ).fetchall()
        for row in query_rows:
            value = str(row["query_key"] or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append({
                "value": value, "label": value, "kind": "catalogue",
                "count": int(row["n"] or 0), "price": _safe_float(row["min_price"], 0.0),
            })
        if len(out) < limit:
            terms = _fts_terms(query)
            rows = []
            fts_row = conn.execute("SELECT value FROM index_meta WHERE key='fts5'").fetchone()
            if terms and fts_row and str(fts_row[0]) == "1":
                expr = " AND ".join(f"{term}*" for term in terms)
                try:
                    rows = conn.execute(
                        "SELECT c.title, c.title_folded, MIN(COALESCE(NULLIF(c.total_price,0),c.price)) AS min_price, "
                        "COUNT(*) AS n FROM catalog_fts f JOIN catalog_offers c ON c.offer_key=f.offer_key "
                        "WHERE catalog_fts MATCH ? AND c.updated_at >= ? "
                        "GROUP BY c.title_folded ORDER BY bm25(catalog_fts), n DESC LIMIT ?",
                        (expr, now_cutoff, limit * 2),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                rows = conn.execute(
                    "SELECT title, title_folded, MIN(COALESCE(NULLIF(total_price,0),price)) AS min_price, COUNT(*) AS n "
                    "FROM catalog_offers WHERE updated_at >= ? AND search_text LIKE ? "
                    "GROUP BY title_folded ORDER BY n DESC, updated_at DESC LIMIT ?",
                    (now_cutoff, f"%{key}%", limit * 2),
                ).fetchall()
            for row in rows:
                value = str(row["title"] or "").strip()
                k = _fold(value)
                if not value or k in seen:
                    continue
                seen.add(k)
                out.append({
                    "value": value, "label": value, "kind": "produit indexé",
                    "count": int(row["n"] or 0), "price": _safe_float(row["min_price"], 0.0),
                })
                if len(out) >= limit:
                    break
    return out[:limit]


def stats(*, path: Path | None = None) -> dict:
    if not index_enabled():
        return {"enabled": False, "offers": 0, "catalog_offers": 0, "queries": 0, "db_path": str(default_db_path())}
    try:
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS offers, COUNT(DISTINCT query_key) AS queries, MAX(updated_at) AS newest FROM indexed_results"
            ).fetchone()
            catalog = conn.execute(
                "SELECT COUNT(*) AS offers, MAX(updated_at) AS newest, "
                "SUM(CASE WHEN state='DEAD' THEN 1 ELSE 0 END) AS dead, "
                "SUM(CASE WHEN state='ACTIVE' THEN 1 ELSE 0 END) AS active FROM catalog_offers"
            ).fetchone()
            lifecycle_rows = conn.execute(
                "SELECT marketplace,state,updated_at FROM catalog_offers"
            ).fetchall()
            fts_row = conn.execute("SELECT value FROM index_meta WHERE key='fts5'").fetchone()
        newest = max(float(row["newest"] or 0.0), float(catalog["newest"] or 0.0))
        return {
            "enabled": True,
            "offers": int(row["offers"] or 0),
            "catalog_offers": int(catalog["offers"] or 0),
            "active": sum(
                1 for life in lifecycle_rows
                if life["state"] != "DEAD" and float(life["updated_at"] or 0) >=
                time.time() - _marketplace_ttl_seconds(life["marketplace"])
            ),
            "stale": sum(
                1 for life in lifecycle_rows
                if life["state"] != "DEAD" and float(life["updated_at"] or 0) <
                time.time() - _marketplace_ttl_seconds(life["marketplace"])
            ),
            "dead": int(catalog["dead"] or 0),
            "queries": int(row["queries"] or 0),
            "newest_age_seconds": max(0.0, time.time() - newest) if newest else None,
            "db_path": str((path or default_db_path()).resolve()),
            "max_age_seconds": max_age_seconds(),
            "query_limit": query_limit(),
            "min_instant_results": 1,
            "fts5": bool(fts_row and str(fts_row[0]) == "1"),
            "schema_version": SCHEMA_VERSION,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "offers": 0,
            "catalog_offers": 0,
            "queries": 0,
            "db_path": str((path or default_db_path()).resolve()),
            "error": str(exc)[:240],
        }


def prune(*, path: Path | None = None, older_than_seconds: int = 7 * 24 * 60 * 60) -> int:
    cutoff = time.time() - max(60, int(older_than_seconds))
    with _connect(path) as conn:
        # Age alone is not proof that an offer disappeared: retain it as STALE.
        conn.execute("UPDATE indexed_results SET state='STALE' WHERE updated_at < ? AND state='ACTIVE'", (cutoff,))
        conn.execute("UPDATE catalog_offers SET state='STALE' WHERE updated_at < ? AND state='ACTIVE'", (cutoff,))
        stale_keys = [row[0] for row in conn.execute(
            "SELECT offer_key FROM catalog_offers WHERE state='DEAD' AND updated_at < ? LIMIT 500", (cutoff,)
        ).fetchall()]
        for key in stale_keys:
            conn.execute("DELETE FROM catalog_offers WHERE offer_key=?", (key,))
            conn.execute("DELETE FROM indexed_results WHERE offer_key=?", (key,))
        if stale_keys:
            try:
                conn.executemany("DELETE FROM catalog_fts WHERE offer_key = ?", ((key,) for key in stale_keys))
            except sqlite3.OperationalError:
                pass
        return len(stale_keys)



__all__ = [
    "IndexSearch",
    "canonical_query",
    "default_db_path",
    "index_enabled",
    "max_age_seconds",
    "min_instant_results",
    "prune",
    "query_limit",
    "search",
    "search_universe",
    "suggest",
    "stats",
    "upsert_results",
]
