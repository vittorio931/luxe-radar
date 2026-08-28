"""Bounded, health-aware source planning for progressive marketplace waves."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .catalog import get_definition
from .source_health import registry as health_registry


MAX_GLOBAL_SOURCE_JOBS = max(1, min(int(os.environ.get("LUXE_RADAR_MAX_GLOBAL_SOURCE_JOBS", "8")), 32))
MAX_PLANNED_SOURCES = max(4, min(int(os.environ.get("LUXE_RADAR_MAX_PLANNED_SOURCES", "40")), 100))

_LUXURY = {"stone island", "balenciaga", "gucci", "prada", "dior", "moncler", "burberry", "rolex", "cartier"}
_RUNNING = {"nike trail", "salomon", "asics", "new balance", "hoka", "on cloud", "running", "trail"}
_SNEAKERS = {"air force", "samba", "p-6000", "2002r", "sneaker", "jordan", "yeezy"}


def query_families(query: str) -> set[str]:
    text = str(query or "").casefold()
    families = {"general"}
    if any(term in text for term in _LUXURY): families.add("luxury")
    if any(term in text for term in _RUNNING): families.add("running")
    if any(term in text for term in _SNEAKERS): families.add("sneakers")
    return families


def _category_affinity(definition, families: set[str]) -> int:
    category = " ".join(definition.categories).casefold() if definition else ""
    score = 0
    if "luxury" in families and any(x in category for x in ("luxe", "premium", "seconde main", "multimarques")): score -= 35
    if "running" in families and any(x in category for x in ("running", "sport", "sneaker")): score -= 35
    if "sneakers" in families and any(x in category for x in ("sneaker", "streetwear", "sport")): score -= 30
    return score


@dataclass(frozen=True)
class PlannedSource:
    name: str
    tier: int
    score: int
    connector: object


def plan_sources(query: str, available: dict, *, limit: int | None = None) -> list[PlannedSource]:
    """Plan a bounded wave; cooldown sources consume no slot.

    Tiers remain exploratory: an unknown/new source is late, never permanently
    excluded. The caller may request a later/deeper wave explicitly.
    """
    families = query_families(query)
    planned = []
    for fallback_rank, (name, connector) in enumerate(available.items()):
        if health_registry.skip_source(name):
            continue
        definition = get_definition(name)
        tier = int(definition.tier if definition else 4)
        base = tier * 100 + fallback_rank + _category_affinity(definition, families)
        dynamic = health_registry.priority_score(name, base)
        if dynamic is None:
            continue
        planned.append(PlannedSource(name, tier, int(dynamic), connector))
    planned.sort(key=lambda item: (item.score, item.name.casefold()))
    return planned[:max(1, int(limit or MAX_PLANNED_SOURCES))]


def wave_groups(query: str, available: dict) -> dict[int, list[PlannedSource]]:
    groups = {1: [], 2: [], 3: [], 4: []}
    for item in plan_sources(query, available):
        groups[item.tier].append(item)
    return groups
