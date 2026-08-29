from __future__ import annotations

"""Collecteur de catalogue profond persistant pour LUXE RADAR.

Hors hot path web : un thread daemon marche chaque source pageable seed par
seed, alimente ``index_engine.upsert_results`` par page et trace chaque
source/page dans la table ``collector_runs`` (raw / parsed / relevant /
rejected / new / duplicates / has_more / blocked / latency).

Règles strictes :
- aucune annonce inventée : seul le résultat réel d'un connecteur est indexé ;
- aucun contournement anti-bot : une source bloquée passe en cooldown via
  ``source_health.registry`` et est simplement sautée ;
- borne par ``max_pages`` du connecteur, par le seuil de pages vides
  consécutives et par un budget temps par seed ;
- la web app continue de fonctionner si le collecteur meurt : l'index est sur
  disque et le collecteur ne lit que le même contrat registre que le live.

Exemples CLI :
  python collector.py --seed "Nike P-6000" --price 250
  python collector.py --seed "On Cloud 5" --sources "eBay,Vinted" --dry-run
  python collector.py --stats
"""

import argparse
import json
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import perf_counter, sleep, time

try:
    import fcntl  # Linux (Render)
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover - Linux
    msvcrt = None  # type: ignore[assignment]

import index_engine
from connector_registry import get_available_connectors
from marketplaces import source_health
from marketplaces.source_scheduler import plan_sources

# ---------------------------------------------------------------------------
# Configuration (env-aware, aucune valeur inventée)
# ---------------------------------------------------------------------------

DEFAULT_SEEDS = [
    ("Nike P-6000", 250),
    ("Nike Trail", 250),
    ("Nike", 250),
    ("On Cloud 5", 250),
    ("On Cloudmonster", 250),
    ("On", 250),
    ("Stone Island", 300),
    ("Adidas Samba", 200),
    ("New Balance 2002R", 250),
    ("Salomon XT-6", 250),
]

# PageSize profond par source : profondeur réelle accessible (clamps
# connecteurs / API), pas un plafond arbitraire.
DEEP_PAGE_LIMITS = {
    "eBay": 200,
    "Vinted": 50,
    "Zalando": 100,
}
DEFAULT_DEEP_PAGE_LIMIT = 100

DEFAULT_PRIORITY = [
    "eBay", "AliExpress", "67behaviour", "Vinted", "Zalando", "ASOS", "SSENSE",
    "Grailed", "DHgate", "Cdiscount", "1688", "i-Run", "Direct Running",
    "Alltricks", "Deporvillage", "21RUN", "Running Point", "MisterRunning",
    "Hardloop", "Ekosport", "Courir", "JD Sports",
]

# ---------------------------------------------------------------------------
# Coordinateur multi-process (Render : plusieurs workers gunicorn partagent le
# même disque éphémère). Un seul process "walke" le catalogue ; les autres
# restent observateurs : leur statut est lu depuis la base partagée et leurs
# vagues sont persistées dans collector_pending, absorbées par le walker.
# ---------------------------------------------------------------------------

_LOCK_UNSUPPORTED = object()


def _try_lock_file(lock_path: Path):
    """Verrou exclusif non bloquant (flock/msvcrt) sur un fichier côté index.

    Retourne un handle à garder ouvert (verrou tenu), ``None`` si un autre
    process détient déjà le verrou, ou ``_LOCK_UNSUPPORTED`` si aucun verrou
    n'est possible (comportement ancien : chaque process marche).
    """
    if fcntl is None and msvcrt is None:  # pragma: no cover - jamais atteint
        return _LOCK_UNSUPPORTED
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
    except OSError:
        return _LOCK_UNSUPPORTED
    try:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"\x00")
            handle.flush()
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[union-attr]
        return handle
    except OSError:
        try:
            handle.close()
        except OSError:
            pass
        return None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name) or "")
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name) or "")
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _is_render_runtime() -> bool:
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_SERVICE_ID")
        or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    )


# Le collecteur profond partage actuellement le petit processus web. Il reste
# donc explicitement opt-in : l'activer implicitement sur Render peut épuiser
# le worker pendant qu'une deuxième recherche progressive démarre.
COLLECTOR_ON_DEMAND_MODE = _env_bool("LUXE_RADAR_COLLECTOR_ON_DEMAND_MODE", False)
COLLECTOR_ENABLED = _env_bool("LUXE_RADAR_COLLECTOR_ENABLED", True)
COLLECTOR_STARTUP_SEEDS_ENABLED = (
    _env_bool("LUXE_RADAR_COLLECTOR_STARTUP_SEEDS_ENABLED", True)
    and not COLLECTOR_ON_DEMAND_MODE
)
COLLECTOR_FRESHNESS_SECONDS = _env_int("LUXE_RADAR_COLLECTOR_FRESHNESS_SECONDS", 24 * 60 * 60, 300, 30 * 24 * 60 * 60)
COLLECTOR_SLEEP_SECONDS = _env_float("LUXE_RADAR_COLLECTOR_SLEEP_SECONDS", 5.0, 1.0, 300.0)
COLLECTOR_PAGE_DELAY_SECONDS = _env_float("LUXE_RADAR_COLLECTOR_PAGE_DELAY_SECONDS", 0.3, 0.0, 60.0)
COLLECTOR_EMPTY_PAGES = _env_int("LUXE_RADAR_COLLECTOR_EMPTY_PAGES", 0, 0, 20)
COLLECTOR_SEED_BUDGET_SECONDS = _env_float(
    "LUXE_RADAR_COLLECTOR_SEED_BUDGET_SECONDS", 120.0 if COLLECTOR_ON_DEMAND_MODE else 300.0, 30.0, 3600.0)
COLLECTOR_IDLE_SECONDS = _env_float(
    "LUXE_RADAR_COLLECTOR_IDLE_SECONDS", 10.0 if COLLECTOR_ON_DEMAND_MODE else 60.0, 10.0, 3600.0)
COLLECTOR_TRIGGER_WINDOW_SECONDS = _env_int("LUXE_RADAR_COLLECTOR_TRIGGER_WINDOW_SECONDS", 300, 30, 86400)


def _allowed_source_names() -> set[str]:
    """Allowlist optionnelle, surtout utile sur les petits workers web."""
    raw = os.environ.get("LUXE_RADAR_COLLECTOR_ALLOWED_SOURCES") or ""
    if not raw and COLLECTOR_ON_DEMAND_MODE:
        raw = "eBay,SSENSE,The Outnet"
    return {name.strip().casefold() for name in raw.split(",") if name.strip()}


def parse_seeds(raw: str | None = None) -> list[tuple[str, float]]:
    """Seeds « query|prix » (ou liste JSON) depuis l'environnement."""
    value = (raw if raw is not None else (os.environ.get("LUXE_RADAR_COLLECTOR_SEEDS") or "")).strip()
    if not value:
        return list(DEFAULT_SEEDS)
    seeds: list[tuple[str, float]] = []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, list) and len(entry) >= 1:
                try:
                    price = float(entry[1]) if len(entry) >= 2 and entry[1] else 0.0
                except (TypeError, ValueError):
                    price = 0.0
                seeds.append((str(entry[0]).strip(), price))
            elif isinstance(entry, str) and "|" in entry:
                query, _, raw_price = entry.partition("|")
                try:
                    price = float(raw_price) if raw_price.strip() else 0.0
                except (TypeError, ValueError):
                    price = 0.0
                seeds.append((query.strip(), price))
        if seeds:
            return seeds
    for line in value.split(","):
        line = line.strip()
        if not line:
            continue
        query, _, raw_price = line.partition("|")
        try:
            price = float(raw_price) if raw_price.strip() else 0.0
        except (TypeError, ValueError):
            price = 0.0
        seeds.append((query.strip(), price))
    return [seed for seed in seeds if seed[0]] or list(DEFAULT_SEEDS)


def deep_page_limit(connector) -> int:
    name = str(getattr(connector, "name", "") or "").strip()
    if name in DEEP_PAGE_LIMITS:
        return int(DEEP_PAGE_LIMITS[name])
    if getattr(connector, "supports_pagination", False):
        return DEFAULT_DEEP_PAGE_LIMIT
    return int(getattr(connector, "expansion_page_size", 0) or DEFAULT_DEEP_PAGE_LIMIT)


def _source_priority(name: str) -> int:
    try:
        return DEFAULT_PRIORITY.index(name)
    except ValueError:
        return len(DEFAULT_PRIORITY) + 1


def ordered_sources(available: dict | None = None, query: str = "") -> list:
    """Sources disponibles ordonnées productif d'abord, cooldowns sautés."""
    available = available if available is not None else get_available_connectors()
    allowed = _allowed_source_names()
    if allowed:
        available = {
            name: connector for name, connector in available.items()
            if name.casefold() in allowed
        }
    if query:
        return [item.connector for item in plan_sources(query, available)]
    ordered = []
    for name, connector in available.items():
        if source_health.registry.skip_source(name):
            continue
        score = source_health.registry.priority_score(name, _source_priority(name))
        if score is not None:
            ordered.append((score, name, connector))
    ordered.sort(key=lambda triple: (triple[0], triple[1].casefold()))
    return [connector for _, _, connector in ordered]


def _publicish(item: dict) -> dict:
    try:
        return index_engine._publicish_payload(item)
    except Exception:  # pragma: no cover - defensive
        return dict(item)


# ---------------------------------------------------------------------------
# Marche d'un seed
# ---------------------------------------------------------------------------

def collect_seed(query, price_max=None, *, sources: list[str] | None = None,
                 dry_run: bool = False, budget_seconds: float | None = None,
                 path: Path | None = None, log=None) -> dict:
    """Marche chaque source pour un seed et alimente l'index.

    Retourne un résumé complet (par source et par page) utilisable par le
    benchmark avant/après et le panneau de statut. En `dry_run`, aucune
    écriture index/trace/santé n'est faite : c'est un audit de profondeur.
    """
    log = log or print
    query = str(query or "").strip()
    if not query:
        return {"error": "query vide", "sources": {}}
    try:
        price_max = float(price_max) if price_max not in (None, "") else None
        if price_max is not None and price_max <= 0:
            price_max = None
    except (TypeError, ValueError):
        price_max = None

    budget = float(budget_seconds if budget_seconds is not None else COLLECTOR_SEED_BUDGET_SECONDS)
    available = get_available_connectors()
    wanted = [name.strip() for name in (sources or []) if name and name.strip()]
    if wanted:
        connectors = []
        for name in wanted:
            connector = available.get(name) or next(
                (c for k, c in available.items() if str(c.name).lower() == name.lower()), None
            )
            if connector is not None:
                connectors.append(connector)
            else:
                log(f"[COLLECTOR] {query} | source inconnue/désactivée : {name}")
    else:
        connectors = ordered_sources(available, query=query)

    if not connectors:
        return {"error": "aucune source disponible (toutes en cooldown ?)", "sources": {}}

    before = index_engine.count_query_offers(query, path=path) if not dry_run else {"exact": 0, "catalog": 0, "catalog_total": 0}
    summary: dict = {
        "query": query,
        "price_max": price_max,
        "dry_run": bool(dry_run),
        "sources": {},
        "pages_walked": 0,
        "raw": 0,
        "parsed": 0,
        "relevant": 0,
        "rejected": 0,
        "new": 0,
        "duplicates": 0,
        "blocked_pages": 0,
        "errors": 0,
        "budget_exceeded": False,
        "duration_s": 0.0,
        "before": before,
    }
    started = perf_counter()
    for connector in connectors:
        if perf_counter() - started >= budget:
            summary["budget_exceeded"] = True
            log(f"[COLLECTOR] {query} | budget dépassé, arrêt avant {connector.name}")
            break
        walked = _walk_source(
            connector, query, price_max,
            dry_run=dry_run, path=path, log=log,
        )
        summary["sources"][connector.name] = walked
        summary["pages_walked"] += walked.get("pages", 0)
        summary["raw"] += walked.get("raw", 0)
        summary["parsed"] += walked.get("parsed", 0)
        summary["relevant"] += walked.get("relevant", 0)
        summary["rejected"] += walked.get("rejected", 0)
        summary["new"] += walked.get("new", 0)
        summary["duplicates"] += walked.get("duplicates", 0)
        summary["blocked_pages"] += walked.get("blocked_pages", 0)
        summary["errors"] += 1 if walked.get("error") else 0
    summary["duration_s"] = perf_counter() - started
    if not dry_run:
        summary["after"] = index_engine.count_query_offers(query, path=path)
    return summary


def _walk_source(connector, query, price_max, *, dry_run=False, path=None, log=None) -> dict:
    log = log or print
    name = str(getattr(connector, "name", "") or "").strip()
    limit = deep_page_limit(connector)
    max_pages = max(0, int(getattr(connector, "max_pages", 0) or 0))
    paged = bool(getattr(connector, "supports_pagination", False)) and max_pages > 1
    pages_total = max_pages if paged else 1
    empty_threshold = COLLECTOR_EMPTY_PAGES or max(1, int(getattr(connector, "empty_pages_threshold", 0) or 2))
    page_delay = max(float(getattr(connector, "cooldown_seconds", 0) or 0), COLLECTOR_PAGE_DELAY_SECONDS)

    summary = {
        "source": name,
        "paged": paged,
        "pages_total": pages_total,
        "page_size": limit,
        "pages": 0,
        "raw": 0,
        "parsed": 0,
        "relevant": 0,
        "rejected": 0,
        "new": 0,
        "duplicates": 0,
        "blocked_pages": 0,
        "skipped": False,
        "reason": "",
        "error": "",
        "pages_detail": [],
    }

    if source_health.registry.skip_source(name):
        summary["skipped"] = True
        reason = source_health.registry.snapshot([name]).get(name, {}).get("cooldown_reason") or "cooldown"
        summary["reason"] = reason
        log(f"[COLLECTOR] {query} | {name} | sautée (cooldown : {reason})")
        return summary

    consecutive_empty = 0
    seen_keys: set[str] = set()
    progress = index_engine.collector_progress(query, name, path=path) if (paged and not dry_run) else {}
    page = max(1, min(int(progress.get("next_page", 1) or 1), pages_total))
    if progress.get("completed"):
        page = 1
    summary["resumed_at_page"] = page
    while True:
        page_started = perf_counter()
        try:
            if paged:
                results = connector.search_page(query, price_max=price_max, limit=limit, page=page)
            else:
                results = connector.search(query, price_max=price_max, limit=limit)
            failed = None
        except Exception as exc:  # noqa: BLE001 - le collecteur doit survivre
            failed = str(exc)[:200]
            results = []

        latency_ms = int((perf_counter() - page_started) * 1000)

        if failed is not None:
            if not dry_run:
                source_health.registry.record_exception(name)
                index_engine.record_collector_run(
                    seed_query=query, marketplace=name, page=page,
                    raw=0, parsed=0, relevant=0, rejected=0, new=0, duplicates=0,
                    has_more=False, blocked=True, latency_ms=latency_ms, path=path,
                )
                index_engine.save_collector_progress(query, name, page, False, path=path)
            summary["error"] = failed
            summary["blocked_pages"] += 1
            summary["pages"] += 1
            summary["pages_detail"].append({"page": page, "blocked": True, "error": failed, "latency_ms": latency_ms})
            log(f"[COLLECTOR] {query} | {name} p{page} | erreur : {failed}")
            break

        # Normalisation : mêmes clés stables que l'index live.
        items: list[dict] = []
        keys: list[str] = []
        for raw in results or []:
            if not isinstance(raw, dict):
                continue
            item = _publicish(raw)
            title = str(item.get("titre") or item.get("title") or "").strip()
            marketplace = str(item.get("marketplace") or item.get("source") or "").strip()
            if not title or not marketplace:
                continue
            item["lien"] = index_engine._clean_url(item.get("lien") or item.get("url") or "")
            item["url"] = index_engine._clean_url(item.get("url") or item.get("lien") or "")
            key = index_engine._offer_key(item)
            if not key:
                continue
            items.append(item)
            keys.append(key)

        parsed = len(items)
        known = index_engine.known_offer_keys(keys, query, path=path) if not dry_run else set()
        relevant = sum(1 for item in items if index_engine._catalog_accept(item, query))
        rejected = parsed - relevant

        new_count = 0
        new_items: list[dict] = []
        for item, key in zip(items, keys):
            if key in seen_keys or key in known:
                continue
            seen_keys.add(key)
            new_count += 1
            new_items.append(item)
        duplicates = parsed - new_count

        has_more = False
        if paged and page < pages_total:
            has_more = True
            if new_count == 0:
                consecutive_empty += 1
                if consecutive_empty >= empty_threshold:
                    has_more = False
            else:
                consecutive_empty = 0

        if not dry_run:
            if new_items:
                index_engine.upsert_results(new_items, query, path=path)
            index_engine.record_collector_run(
                seed_query=query, marketplace=name, page=page,
                raw=parsed, parsed=parsed, relevant=relevant, rejected=rejected,
                new=new_count, duplicates=duplicates,
                has_more=has_more, blocked=False, latency_ms=latency_ms, path=path,
            )
            source_health.registry.record_outcome(name, parsed, relevant, network_elapsed=latency_ms / 1000.0)
            index_engine.save_collector_progress(
                query, name, page + 1 if has_more else 1, not has_more, path=path,
            )

        summary["pages"] += 1
        summary["raw"] += parsed
        summary["parsed"] += parsed
        summary["relevant"] += relevant
        summary["rejected"] += rejected
        summary["new"] += new_count
        summary["duplicates"] += duplicates
        summary["pages_detail"].append({
            "page": page, "raw": parsed, "parsed": parsed, "relevant": relevant,
            "rejected": rejected, "new": new_count, "duplicates": duplicates,
            "has_more": has_more, "blocked": False, "latency_ms": latency_ms,
        })
        log(
            f"[COLLECTOR] {query} | {name} p{page}/{pages_total if paged else 1} "
            f"raw={parsed} relevant={relevant} rejected={rejected} new={new_count} "
            f"dup={duplicates} has_more={has_more} {latency_ms}ms"
        )

        if not has_more:
            break
        page += 1
        if page_delay > 0:
            sleep(page_delay)

    return summary


# ---------------------------------------------------------------------------
# Scheduler background (hors hot path)
# ---------------------------------------------------------------------------

class Collector:
    """Thread daemon : seeds par défaut + déclencheurs utilisateur.

    Ne bloque jamais la web app : une seule passe à la fois, sommeli entre
    seeds et en inactivité, et l'index vit sur disque de toute façon.
    """

    def __init__(self, *, path: Path | None = None):
        self._path = path
        self._lock = Lock()
        self._queue: list[tuple[str, float]] = []
        self._in_flight: tuple[str, float] | None = None
        self._recent_runs: list[dict] = []
        self._stop = Event()
        self._thread: Thread | None = None
        self._walker: bool | None = None
        self._lock_handle = None
        self._last_trigger_sweep = 0.0

    # -- API publique -------------------------------------------------------

    def _lock_path(self) -> Path:
        base = self._path if self._path is not None else index_engine.default_db_path()
        return Path(str(base) + ".collector.lock")

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        # Multi-workers : un seul process marche (verrou fichier partagé côté
        # index). Les observateurs restent utiles : statut lu depuis la base
        # partagée et vagues persistées dans collector_pending.
        lock = _try_lock_file(self._lock_path())
        if lock is not _LOCK_UNSUPPORTED and lock is None:
            self._walker = False
            index_engine.collector_diag_write(
                f"boot pid={os.getpid()} walker=False (verrou tenu par un autre process)", path=self._path)
            return
        self._walker = True
        self._lock_handle = lock if lock is not _LOCK_UNSUPPORTED else None
        self._stop.clear()
        self._thread = Thread(target=self._loop, name="luxe-radar-collector", daemon=True)
        index_engine.collector_diag_write(
            f"boot pid={os.getpid()} walker=True lock={'ok' if self._lock_handle else 'unsupported'}", path=self._path)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def enqueue(self, query, price_max=None):
        """Déclencheur : collecte un seed sans re-collecter en boucle.

        Vague persistée dans la base (table collector_pending) : en
        multi-workers le process qui reçoit la requête n'est pas forcément
        celui qui marche, et un thread mort ne doit pas faire perdre la vague.
        Retourne True si la vague a été acceptée (nouvelle).
        """
        query = str(query or "").strip()
        if not query:
            return False
        try:
            price_max = float(price_max) if price_max not in (None, "") else None
            if price_max is not None and price_max <= 0:
                price_max = None
        except (TypeError, ValueError):
            price_max = None
        # I/O SQLite HORS du verrou : la route /api/collector/status ne doit
        # jamais attendre derrière une passe du collecteur (voir V3.8 prod).
        if index_engine.collector_has_recent(query, COLLECTOR_TRIGGER_WINDOW_SECONDS, path=self._path):
            return False
        return index_engine.queue_collector_trigger(query, price_max, path=self._path)

    def _absorb_pending(self, limit: int = 20):
        """Absorbe les vagues persistées dans la file locale (dédupliquées)."""
        try:
            pending = index_engine.drain_collector_triggers(limit, path=self._path)
        except Exception:  # noqa: BLE001 - une vague perdue n'est pas fatale
            return 0
        added = 0
        with self._lock:
            for q, p in pending:
                folded = index_engine.canonical_query(q)
                if self._in_flight and index_engine.canonical_query(self._in_flight[0]) == folded:
                    continue
                if any(index_engine.canonical_query(x[0]) == folded for x in self._queue):
                    continue
                self._queue.append((q, p))
                added += 1
        return added

    def status(self) -> dict:
        with self._lock:
            queue = [{"query": q, "price_max": p} for q, p in self._queue]
            recent = list(self._recent_runs)
            in_flight = self._in_flight
            thread_alive = self._thread is not None and self._thread.is_alive()

        # Un seul snapshot SQLite lecture seule, borné à ~150 ms. Le panneau
        # de diagnostic ne doit jamais consommer les 8 s du busy_timeout global.
        try:
            snapshot = index_engine.collector_status_snapshot(path=self._path)
        except Exception as exc:  # noqa: BLE001 - jamais bloquer /status
            snapshot = {
                "db_available": False, "error": str(exc)[:160],
                "stats": {"last_run_at": None, "runs": 0, "new": 0, "sources": {}},
                "recent_runs": [], "diag": [],
            }
        stats = snapshot.get("stats") or {"last_run_at": None}
        if thread_alive:
            running = True
            recent_out = recent[-8:] or list(snapshot.get("recent_runs") or [])[-8:]
        elif self._walker is False:
            # Observateur multi-process : il n'a pas de thread local, mais peut
            # déclarer le walker distant actif si la DB partagée est fraîche.
            last = stats.get("last_run_at")
            window = COLLECTOR_IDLE_SECONDS + 4 * COLLECTOR_SLEEP_SECONDS + 5.0
            running = bool(last and (time() - float(last)) < window)
            recent_out = list(snapshot.get("recent_runs") or [])[-8:]
        else:
            # Ce process est le walker (ou n'a jamais été démarré) : un thread
            # local mort doit rester running=False. Ne pas masquer sa mort avec
            # une ancienne passe encore fraîche en SQLite.
            running = False
            recent_out = list(snapshot.get("recent_runs") or [])[-8:]
        return {
            "enabled": COLLECTOR_ENABLED,
            "pid": os.getpid(),
            "thread_alive": bool(thread_alive),
            "walker": self._walker,
            "running": bool(running),
            "busy": in_flight is not None,
            "in_flight": {"query": in_flight[0], "price_max": in_flight[1]} if in_flight else None,
            "queue": queue,
            "recent_runs": recent_out,
            "diag": list(snapshot.get("diag") or []),
            "stats": stats,
            "db_available": bool(snapshot.get("db_available")),
            "db_error": snapshot.get("error"),
            "freshness_seconds": COLLECTOR_FRESHNESS_SECONDS,
        }

    # -- Boucle interne ------------------------------------------------------

    def _dequeue(self):
        with self._lock:
            if not self._queue:
                return None
            job = self._queue.pop(0)
            self._in_flight = job
        return job

    def _seed_stale(self, query, price_max):
        if index_engine.collector_has_recent(query, COLLECTOR_FRESHNESS_SECONDS, path=self._path):
            return False
        with self._lock:
            folded = index_engine.canonical_query(query)
            if self._in_flight and index_engine.canonical_query(self._in_flight[0]) == folded:
                return False
            for q, _p in self._queue:
                if index_engine.canonical_query(q) == folded:
                    return False
        return True

    def _refill_defaults(self):
        if not COLLECTOR_STARTUP_SEEDS_ENABLED:
            return 0
        added = 0
        for query, price_max in parse_seeds():
            if self._seed_stale(query, price_max):
                with self._lock:
                    self._queue.append((query, price_max))
                added += 1
        return added

    def _run_seed(self, query, price_max):
        summary = collect_seed(query, price_max, path=self._path)
        with self._lock:
            self._in_flight = None
            self._recent_runs.append(summary)
            if len(self._recent_runs) > 20:
                self._recent_runs = self._recent_runs[-20:]

    def _loop(self):
        try:
            self._loop_body()
        except BaseException:  # noqa: BLE001 - trace le motif RÉEL de mort du thread
            import traceback
            try:
                index_engine.collector_diag_write(
                    "loop_exit: " + traceback.format_exc()[:400], path=self._path)
            except Exception:  # noqa: BLE001
                pass

    def _loop_body(self):
        while not self._stop.is_set():
            try:
                if not self._queue:
                    self._absorb_pending()
                job = self._dequeue()
                if job is None:
                    if self._refill_defaults() == 0:
                        sleep(COLLECTOR_IDLE_SECONDS)
                    continue
                query, price_max = job
                try:
                    self._run_seed(query, price_max)
                except Exception as exc:  # noqa: BLE001 - une passe ne doit jamais tuer le thread
                    with self._lock:
                        self._in_flight = None
                        self._recent_runs.append({"query": query, "error": str(exc)[:200]})
            except Exception as exc:  # noqa: BLE001 - erreur transitoire (DB au boot...) : on saute un cycle
                with self._lock:
                    self._recent_runs.append({"error": str(exc)[:200]})
                try:
                    sleep(2.0)
                except Exception:
                    return
            sleep(COLLECTOR_SLEEP_SECONDS)


# ---------------------------------------------------------------------------
# CLI (audit de profondeur / collection manuelle / stats)
# ---------------------------------------------------------------------------

def _parse_args():
    parser = argparse.ArgumentParser(description="Collecteur de catalogue profond LUXE RADAR.")
    parser.add_argument("--seed", action="append", dest="seeds", help="Seed 'query|prix' (répétable).")
    parser.add_argument("--price", type=float, default=None, help="Prix max par défaut.")
    parser.add_argument("--sources", default="", help="Sources séparées par des virgules (défaut : toutes).")
    parser.add_argument("--dry-run", action="store_true", help="Audit sans écrire index/traces.")
    parser.add_argument("--budget", type=float, default=None, help="Budget secondes par seed.")
    parser.add_argument("--stats", action="store_true", help="Afficher les statistiques collector.")
    parser.add_argument("--seed-query", default="", help="Filtrer les stats par seed.")
    return parser.parse_args()


def _main():
    args = _parse_args()
    if args.stats:
        stats = index_engine.collector_stats(seed_query=args.seed_query or None)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    seeds: list[tuple[str, float]] = []
    for raw in (args.seeds or []):
        query, _, price = raw.partition("|")
        try:
            price = float(price) if price.strip() else (args.price or 0.0)
        except (TypeError, ValueError):
            price = args.price or 0.0
        seeds.append((query.strip(), price))
    if not seeds:
        seeds = parse_seeds()
    if args.price:
        seeds = [(q, args.price) for q, _p in seeds]
    sources = [part.strip() for part in args.sources.split(",") if part.strip()] if args.sources else None
    for query, price in seeds:
        summary = collect_seed(
            query, price,
            sources=sources,
            dry_run=args.dry_run,
            budget_seconds=args.budget,
        )
        print("=" * 60)
        print(f"[COLLECTOR] seed : {query} | prix_max={price} | dry_run={args.dry_run}")
        for source, walked in summary.get("sources", {}).items():
            if walked.get("skipped"):
                print(f"  - {source}: sautée ({walked.get('reason')})")
                continue
            detail = walked.get("pages_detail") or []
            pages = ", ".join(
                f"p{d.get('page')}: new={d.get('new')} rel={d.get('relevant')}"
                + (" bloquée" if d.get("blocked") else "")
                for d in detail
            ) or "aucune page"
            print(f"  - {source}: pages={walked.get('pages')} raw={walked.get('raw')} "
                  f"relevant={walked.get('relevant')} rejected={walked.get('rejected')} "
                  f"new={walked.get('new')} dup={walked.get('duplicates')} | {pages}")
            if walked.get("error"):
                print(f"      erreur: {walked['error']}")
        before = summary.get("before") or {}
        after = summary.get("after")
        if after is not None:
            print(f"[COLLECTOR] avant: exact={before.get('exact')} catalog={before.get('catalog')} "
                  f"total={before.get('catalog_total')} | après: exact={after.get('exact')} "
                  f"catalog={after.get('catalog')} total={after.get('catalog_total')} "
                  f"| pages={summary.get('pages_walked')} new={summary.get('new')} "
                  f"durée={summary.get('duration_s'):.1f}s")
        else:
            print(f"[COLLECTOR] (dry-run) pages={summary.get('pages_walked')} "
                  f"raw={summary.get('raw')} relevant={summary.get('relevant')} "
                  f"durée={summary.get('duration_s'):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
