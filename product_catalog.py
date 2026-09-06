"""Catalogue de suggestions ouvert pour les recherches produit.

Le catalogue améliore la saisie et les fautes courantes, mais ne constitue
jamais une liste blanche : une marque ou référence inconnue reste acceptée.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

from modeles import MARQUES_MODELES
from discord_radar import CPU_EXAMPLES, is_cpu_query


def _normalise(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


@lru_cache(maxsize=1)
def catalog_entries() -> tuple[tuple[str, tuple[str, ...]], ...]:
    entries = [(model, (_normalise(model),)) for model in CPU_EXAMPLES]
    for brand, models in MARQUES_MODELES.items():
        entries.append((brand, (_normalise(brand),)))
        for model, aliases in models.items():
            canonical = f"{brand} {model}"
            search_terms = {_normalise(canonical), _normalise(model)}
            search_terms.update(_normalise(alias) for alias in aliases)
            entries.append((canonical, tuple(term for term in search_terms if term)))
    return tuple(entries)


def suggestions(query: str, limit: int = 25) -> list[str]:
    needle = _normalise(query)
    scored = []
    for canonical, terms in catalog_entries():
        canonical_n = _normalise(canonical)
        if not needle:
            score = 0
        elif needle in canonical_n or any(needle in term for term in terms):
            score = 100 - canonical_n.find(needle)
        else:
            score = max(SequenceMatcher(None, needle, term).ratio() for term in terms) * 70
        if not needle or score >= 38:
            scored.append((score, canonical.casefold(), canonical))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored[:max(1, min(int(limit), 25))]]


def correct_query(query: str) -> str:
    """Corrige une faute très probable, sinon conserve strictement la saisie."""
    original = " ".join(str(query or "").split())[:120]
    if is_cpu_query(original):
        return original
    needle = _normalise(original)
    if len(needle) < 4:
        return original
    best = (0.0, original)
    for canonical, terms in catalog_entries():
        score = max(SequenceMatcher(None, needle, term).ratio() for term in terms)
        if score > best[0]:
            best = (score, canonical)
    return best[1] if best[0] >= 0.88 else original
