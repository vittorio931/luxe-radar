from __future__ import annotations

import os
import re
import unicodedata

from .authenticity import annotate_authenticity

# Filtre volontairement conservateur : il retire surtout les faux positifs
# évidents. Il ne prétend jamais certifier l'authenticité d'un produit.

BRAND_ALIASES = {
    "nike": ("nike",),
    "adidas": ("adidas",),
    "jordan": ("jordan", "air jordan"),
    "hoka": ("hoka", "hoka one one"),
    "under armour": ("under armour", "underarmour"),
    "asics": ("asics",),
    "puma": ("puma",),
    "new balance": ("new balance",),
    "timberland": ("timberland",),
    "salomon": ("salomon",),
    "saucony": ("saucony",),
    "reebok": ("reebok",),
    "on": ("on running", "on cloud", "onrunning"),
    "lacoste": ("lacoste",),
    "ralph lauren": ("ralph lauren", "polo ralph lauren"),
    "stone island": ("stone island",),
    "the north face": ("the north face", "north face"),
    "arcteryx": ("arc teryx", "arcteryx"),
    "patagonia": ("patagonia",),
    "carhartt": ("carhartt", "carhartt wip"),
    "moncler": ("moncler",),
    "gucci": ("gucci",),
    "prada": ("prada",),
    "dior": ("dior",),
    "balenciaga": ("balenciaga",),
    "versace": ("versace",),
    "burberry": ("burberry",),
    "off white": ("off white", "offwhite"),
    "supreme": ("supreme",),
    "stussy": ("stussy",),
    "palace": ("palace",),
    "fear of god": ("fear of god", "essentials"),
    "amiri": ("amiri",),
    "cp company": ("c p company", "cp company"),
}

NO_BUY_PHRASES = (
    "ne pas acheter",
    "ne pas achete",
    "n achetez pas",
    "n achete pas",
    "do not buy",
    "dont buy",
    "don't buy",
    "fake listing",
    "scam listing",
    "annonce test",
    "test annonce",
)

EXPLICIT_FAKE_PHRASES = (
    "counterfeit",
    "contrefacon",
    "contrefaçon",
    "super fake",
    "mirror quality",
    "aaa quality",
    "unauthorized authentic",
)


def _recall_mode_enabled():
    return str(os.environ.get("LUXE_RADAR_RECALL_MODE", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _norm(value):
    text = "" if value is None else str(value)
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("-", " ").replace("_", " ").replace("’", "'")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(text, phrase):
    text_n = _norm(text)
    phrase_n = _norm(phrase)
    if not text_n or not phrase_n:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase_n) + r"(?![a-z0-9])", text_n) is not None


def _detected_brands(text):
    found = set()
    for canonical, aliases in BRAND_ALIASES.items():
        if any(_has(text, alias) for alias in aliases):
            found.add(canonical)
    return found


def evaluate_result(item, query="", marketplace=""):
    """Retourne (keep: bool, reason: str|None)."""
    if not isinstance(item, dict):
        return False, "résultat non structuré"

    title = str(item.get("titre") or item.get("title") or "").strip()
    description = str(item.get("description") or item.get("texte") or "").strip()
    full = f"{title} {description}".strip()
    q = _norm(query)
    t = _norm(title)

    if not title:
        return False, "titre vide"

    for phrase in NO_BUY_PHRASES:
        if _has(full, phrase):
            return False, f"annonce à ignorer ({phrase})"

    # V2.8.11 : en couverture maximale, on ne retire plus les vraies cartes
    # pour simple doute de pertinence. Les filtres de marque/modèle restent
    # disponibles comme métadonnées et dans l'interface.
    if _recall_mode_enabled():
        return True, None

    # Nike Trail != Portland Trail Blazers.
    q_tokens = set(re.findall(r"[a-z0-9]+", q))
    if "trail" in q_tokens and "blazers" not in q_tokens:
        if "trail blazers" in t or ("trail" in t and "portland" in t and "blazers" in t):
            return False, "faux positif Portland Trail Blazers"

    # Bourrage de marques : 5 marques distinctes dans un titre court/normal est
    # presque toujours du SEO/spam pour une recherche produit spécifique.
    # On laisse passer les recherches explicitement de lots/bundles.
    allow_bundle = any(word in q_tokens for word in {"lot", "bundle", "pack", "ensemble"})
    brands = _detected_brands(title)
    query_brands = _detected_brands(query)

    if not allow_bundle and len(brands) >= 5:
        # Le titre doit contenir beaucoup plus de marques que la requête.
        if len(brands - query_brands) >= 3:
            return False, f"bourrage de marques ({len(brands)} marques)"

    # Autre signal de keyword stuffing : longue suite de marques concurrentes.
    tokens = t.split()
    if not allow_bundle and len(tokens) >= 10 and len(brands) >= 4:
        return False, f"titre SEO multi-marques ({len(brands)} marques)"

    return True, None


def filter_results(results, query="", marketplace=""):
    kept = []
    debug = os.environ.get("LUXE_RADAR_DEBUG_FILTERS") == "1"
    for item in results or []:
        ok, reason = evaluate_result(item, query=query, marketplace=marketplace)
        if ok:
            kept.append(annotate_authenticity(item, marketplace=marketplace))
        elif debug:
            title = item.get("titre") if isinstance(item, dict) else repr(item)
            print(f"[QUALITE] Rejet {marketplace}: {title} -> {reason}")
    return kept
