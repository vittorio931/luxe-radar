"""Registre de sante des sources, conscient de l'environnement.

But : decider de l'ordonnancement des recherches progressives SANS contourner
aucune protection. Une source en cooldown ou bloquee pour l'environnement
courant est simplement omise des vagues : aucun worker consomme, aucun appel
reseau repete, aucune promesse de resultats non tenue.

Architecture volontairement generique :
- l'environnement est une chaine courte ("render" = datacenter heberge,
  "local" = poste/self-hosted) derivee des variables reelles, extensible ;
- chaque reglage (durees de cooldown, seuils) depend de l'environnement ;
- les mesures restent en memoire de processus (single worker Gunicorn),
  suffisant pour un service a instance unique.

Les connecteurs signalent les blocages observes (403/429/challenge) via
`record_http` / `record_blocked`. L'orchestrateur (app_web) enregistre les
resultats observes (`record_outcome`) puis ne re-planifie pas ce qui est en
cooldown.
"""
from __future__ import annotations

import os
import statistics
import threading
import time
from collections import deque

_ENV_ALIASES = {
    "production": "render",
    "prod": "render",
    "hosted": "render",
    "selfhosted": "local",
    "desktop": "local",
    "dev": "local",
    "development": "local",
    "local": "local",
}


def current_environment():
    """Environnement courant : "render" ou "local" par defaut."""
    raw = (os.environ.get("LUXE_RADAR_ENV") or "").strip().lower()
    if not raw:
        raw = (
            "render"
            if (
                os.environ.get("RENDER")
                or os.environ.get("RENDER_SERVICE_ID")
                or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
            )
            else "local"
        )
    return _ENV_ALIASES.get(raw, "local")


_ENV = current_environment()
_IS_DATACENTER = _ENV == "render"

# Durees de cooldown : longues pour un datacenter (egress souvent bloque par
# les marchands), courtes en local pour re-tester vite apres une reprise.
COOLDOWN_BLOCKED_SECONDS = 600 if _IS_DATACENTER else 90
COOLDOWN_EMPTY_SECONDS = 120 if _IS_DATACENTER else 60
COOLDOWN_TIMEOUT_SECONDS = 90 if _IS_DATACENTER else 30

# Une source vide ET lente (temps reseau significatif) est probablement bloquee
# ou indisponible pour cet environnement : on arrete d'y consacrer des workers.
EMPTY_SLOW_SECONDS = 4.0
CONSECUTIVE_EMPTY_TO_DEPRIORITIZE = 2
CONSECUTIVE_FAILURES_TO_COOLDOWN = 3

BLOCKED_HTTP_STATUSES = {400, 403, 407, 429}

STATUS_ACTIVE = "active"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"
STATUS_TEMP_UNAVAILABLE = "temporarily_unavailable"
STATUS_UNKNOWN = "unknown"

# Poids d'ordonnancement (plus petit = plus tot dans la vague).
_TIER_A_BONUS = 50
_TIER_C_PENALTY = 60


def _pct(samples, q):
    """Percentile simple (50/95) d'un iterable, arrondi en millisecondes."""
    values = [max(0.0, float(value)) for value in (samples or []) if value is not None]
    if not values:
        return None
    values.sort()
    if q >= 100:
        return round(values[-1] * 1000)
    index = (len(values) - 1) * (q / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    frac = index - lower
    return round((values[lower] * (1 - frac) + values[upper] * frac) * 1000)


class _SourceHealthEntry:
    def __init__(self, name):
        self.name = name
        self.last_http_status = None
        self.last_success_at = None
        self.last_failure_at = None
        self.cooldown_until = 0.0
        self.cooldown_reason = None
        self.consecutive_empty = 0
        self.consecutive_failures = 0
        self.blocked = False
        self.runs = 0
        self.successful_runs = 0
        self.failed_runs = 0
        self.parsing_failures = 0
        self.timeout_failures = 0
        self.network_samples = deque(maxlen=12)
        self.queue_samples = deque(maxlen=12)
        self.relevant_samples = deque(maxlen=8)
        self.received_samples = deque(maxlen=8)


class SourceHealthRegistry:
    """Registre processus unique, thread-safe, indexe par nom de source."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}

    def _entry(self, name):
        name = str(name or "").strip()
        entry = self._entries.get(name)
        if entry is None:
            entry = _SourceHealthEntry(name)
            self._entries[name] = entry
        return entry

    def record_http(self, name, status):
        """Statut HTTP observe sur l'hote public (403/429 -> blocage)."""
        if not name:
            return
        try:
            status = int(status)
        except (TypeError, ValueError):
            return
        with self._lock:
            entry = self._entry(name)
            entry.last_http_status = status
            if status in BLOCKED_HTTP_STATUSES:
                entry.blocked = True
                entry.consecutive_failures += 1
                entry.last_failure_at = time.time()
                entry.cooldown_until = time.time() + COOLDOWN_BLOCKED_SECONDS
                entry.cooldown_reason = f"HTTP {status}"
                entry.failed_runs += 1

    def record_blocked(self, name, reason="refus/challenge"):
        """Blocage observe sans statut HTTP precis (challenge, page anti-bot)."""
        if not name:
            return
        with self._lock:
            entry = self._entry(name)
            entry.blocked = True
            entry.consecutive_failures += 1
            entry.failed_runs += 1
            entry.timeout_failures += 1
            entry.last_failure_at = time.time()
            entry.cooldown_until = time.time() + COOLDOWN_BLOCKED_SECONDS
            entry.cooldown_reason = str(reason or "refus/challenge")

    def preseed_blocked(self, name, reason="predeclare en config"):
        """Blocage declare en configuration (pas une mesure du process).

        Utilise par LUXE_RADAR_PRESEED_BLOCKED pour eviter de re-payer la
        taxe froide (une source bloquee pour l'environnement re-tourne a
        chaque deploiement avant d'etre reapprise). S'appuie sur des
        observations reelles consignees dans la configuration de deploiement.
        """
        if not name:
            return
        with self._lock:
            entry = self._entry(name)
            entry.blocked = True
            if not entry.cooldown_until or entry.cooldown_until <= time.time():
                entry.cooldown_until = time.time() + COOLDOWN_BLOCKED_SECONDS
            if entry.cooldown_reason and entry.cooldown_reason.startswith("HTTP"):
                entry.cooldown_reason = entry.cooldown_reason
            else:
                entry.cooldown_reason = str(reason or "predeclare en config")
            if not entry.last_failure_at:
                entry.last_failure_at = time.time()

    def record_exception(self, name):
        """Erreur reseau/lancement (timeout, echec DNS, playwright...)."""
        if not name:
            return
        with self._lock:
            entry = self._entry(name)
            entry.consecutive_failures += 1
            entry.last_failure_at = time.time()
            if entry.consecutive_failures >= CONSECUTIVE_FAILURES_TO_COOLDOWN:
                entry.cooldown_until = time.time() + COOLDOWN_TIMEOUT_SECONDS
                entry.cooldown_reason = "echecs successifs"

    def record_outcome(self, name, received, relevant, network_elapsed=None, queue_wait=None):
        """Resultat observe d'une passe complete pour la source.

        `received` : cartes brutes ; `relevant` : offres conservees apres
        analyse. Un vide lent et repetitif met la source en cooldown court.
        """
        if not name:
            return
        with self._lock:
            entry = self._entry(name)
            entry.runs += 1
            try:
                received = int(received or 0)
            except (TypeError, ValueError):
                received = 0
            try:
                relevant = int(relevant or 0)
            except (TypeError, ValueError):
                relevant = 0
            if network_elapsed is not None:
                entry.network_samples.append(max(0.0, float(network_elapsed)))
            if queue_wait is not None:
                entry.queue_samples.append(max(0.0, float(queue_wait)))
            entry.received_samples.append(received)
            entry.relevant_samples.append(relevant)
            if relevant > 0:
                entry.successful_runs += 1
                entry.last_success_at = time.time()
                entry.consecutive_empty = 0
                entry.consecutive_failures = 0
                if not entry.cooldown_until:
                    entry.blocked = False
                return
            entry.consecutive_empty += 1
            if received > 0 and relevant <= 0:
                entry.parsing_failures += 1
            if (
                entry.consecutive_empty >= 2
                and (network_elapsed or 0.0) >= EMPTY_SLOW_SECONDS
            ):
                entry.cooldown_until = time.time() + COOLDOWN_EMPTY_SECONDS
                entry.cooldown_reason = "vide lent et repetitif"

    def in_cooldown(self, name):
        if not name:
            return False
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                return False
            if entry.cooldown_until and entry.cooldown_until <= time.time():
                entry.cooldown_until = 0.0
                entry.cooldown_reason = None
                return False
            return entry.cooldown_until > time.time()

    def skip_source(self, name):
        """True si la source ne doit pas consommer de worker maintenant."""
        return self.in_cooldown(name)

    @staticmethod
    def _classify_entry(entry, now):
        """Classement sans verrou : appele sous _lock ou avec entree stable."""
        if entry is None:
            return STATUS_UNKNOWN, "b"
        if entry.cooldown_until and entry.cooldown_until > now:
            return (STATUS_BLOCKED if entry.blocked else STATUS_TEMP_UNAVAILABLE), "cooldown"
        if sum(entry.relevant_samples or [0]) > 0:
            return STATUS_ACTIVE, "a"
        if entry.runs > 0:
            return STATUS_PARTIAL, "b"
        return STATUS_UNKNOWN, "b"

    def classify(self, name):
        """Retourne (status, tier) pour affichage debug et ordonnancement."""
        with self._lock:
            entry = self._entries.get(name)
        return self._classify_entry(entry, time.time())

    def priority_score(self, name, base_rank):
        """Score d'ordonnancement dynamique ou None si la source doit etre omise.

        = classement d'intention (petit = tot) + sante observee :
        - productive recente -> tier A (devance) ;
        - vide lent et repetitif -> tier C (tout a la fin).
        """
        if self.skip_source(name):
            return None
        with self._lock:
            entry = self._entries.get(name)
        if entry is None:
            return int(base_rank or 0)
        score = int(base_rank or 0)
        if sum(entry.relevant_samples or [0]) > 0:
            score -= _TIER_A_BONUS
        slow_empty = entry.consecutive_empty >= CONSECUTIVE_EMPTY_TO_DEPRIORITIZE
        if slow_empty and entry.network_samples and statistics.median(entry.network_samples) >= EMPTY_SLOW_SECONDS:
            score += _TIER_C_PENALTY
        return score

    def skipped_sources(self, names):
        return [name for name in (names or []) if self.in_cooldown(name)]

    def snapshot(self, names=None):
        """Etat lisible pour le panneau debug/admin des sources."""
        with self._lock:
            keys = [name for name in (names or []) if name in self._entries]
            result = {}
            now = time.time()
            for name in keys:
                entry = self._entries[name]
                status, tier = self._classify_entry(entry, now)
                result[name] = {
                    "env": _ENV,
                    "status": status,
                    "health_state": (
                        "COOLDOWN" if status in {STATUS_BLOCKED, STATUS_TEMP_UNAVAILABLE}
                        else "HEALTHY" if entry.successful_runs >= 3
                        else "DEGRADED" if entry.runs > 0
                        else "EXPERIMENTAL"
                    ),
                    "tier": tier,
                    "last_http_status": entry.last_http_status,
                    "last_success_at": entry.last_success_at,
                    "last_failure_at": entry.last_failure_at,
                    "cooldown_remaining_s": max(0.0, entry.cooldown_until - now) if entry.cooldown_until else 0.0,
                    "cooldown_reason": entry.cooldown_reason,
                    "consecutive_empty": entry.consecutive_empty,
                    "consecutive_failures": entry.consecutive_failures,
                    "runs": entry.runs,
                    "successful_runs": entry.successful_runs,
                    "failed_runs": entry.failed_runs,
                    "success_rate": round(entry.successful_runs / max(1, entry.runs), 4),
                    "results_rate": round(sum(entry.relevant_samples) / max(1, sum(entry.received_samples)), 4),
                    "parsing_failures": entry.parsing_failures,
                    "timeout_failures": entry.timeout_failures,
                    "network_p50_ms": _pct(entry.network_samples, 50),
                    "network_p95_ms": _pct(entry.network_samples, 95),
                    "queue_p50_ms": _pct(entry.queue_samples, 50),
                    "queue_p95_ms": _pct(entry.queue_samples, 95),
                    "raw_recent": sum(entry.received_samples),
                    "relevant_recent": sum(entry.relevant_samples),
                }
            return result

    def summary(self, names=None):
        snap = self.snapshot(names or list(self._entries))
        states = [item.get("health_state") for item in snap.values()]
        return {
            "tracked": len(snap),
            "healthy": states.count("HEALTHY"),
            "degraded": states.count("DEGRADED"),
            "cooldown": states.count("COOLDOWN"),
            "experimental": states.count("EXPERIMENTAL"),
            "successful_today": sum(1 for item in snap.values() if item.get("last_success_at") and time.time() - item["last_success_at"] < 86400),
            "results_collected_recent": sum(int(item.get("relevant_recent") or 0) for item in snap.values()),
            "avg_latency_ms": self.network_p50(),
        }

    def eligible_for_activation(self, name):
        """True only after three real productive runs and no active cooldown."""
        if self.in_cooldown(name):
            return False
        with self._lock:
            entry = self._entries.get(str(name or "").strip())
            return bool(entry and entry.successful_runs >= 3 and sum(entry.relevant_samples) > 0)

    def _global_pct(self, samples, q):
        with self._lock:
            flat = []
            for entry in self._entries.values():
                flat.extend(samples(entry))
        return _pct(flat, q)

    def queue_p50(self):
        return self._global_pct(lambda entry: entry.queue_samples, 50)

    def queue_p95(self):
        return self._global_pct(lambda entry: entry.queue_samples, 95)

    def network_p50(self):
        return self._global_pct(lambda entry: entry.network_samples, 50)

    def network_p95(self):
        return self._global_pct(lambda entry: entry.network_samples, 95)

    def cooldown_count(self):
        now = time.time()
        with self._lock:
            return sum(1 for entry in self._entries.values() if entry.cooldown_until and entry.cooldown_until > now)

    def reset(self):
        with self._lock:
            self._entries.clear()


registry = SourceHealthRegistry()


def _preseed_from_env():
    """Pre-declare les sources bloquees pour l'environnement courant.

    Liste lue dans LUXE_RADAR_PRESEED_BLOCKED (noms separes par des virgules),
    c'est-a-dire un parametrage de deploiement derive d'observations reelles,
    jamais une valeur inventee. Les sources predeclarees restent omises des
    vagues pendant toute la duree de leur cooldown.
    """
    raw = (os.environ.get("LUXE_RADAR_PRESEED_BLOCKED") or "").strip()
    if not raw:
        return
    names = [name.strip() for name in raw.split(",") if name.strip()]
    for name in names:
        registry.preseed_blocked(name)


_preseed_from_env()
