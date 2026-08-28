"""Quality gate central (V3.7.x) : une seule définition de la pertinence.

``evaluate_offer`` est LA porte d'entrée utilisée partout (index chaud, rang,
analyses radar) pour décider si une offre est pertinente pour une requête.
- Filtres DURS (rejet si absent) : marque, modèle exact, gamme, catégorie.
- Filtres SOFT (bonus, jamais de rejet) : couleur, sexe, matière.
- Déterministe : même requête + même offre => même score, aucun hasard.
- Les requêtes sans dimension contraignante (référence produit, mot seul)
  restent acceptées pour ne jamais créer de faux négatifs.

Dépendances stdlib uniquement.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from search_intent import (
    COLOR_ALIASES,
    GENDER_ALIASES,
    LINE_ALIASES,
    MATTER_ALIASES,
    TYPE_ALIASES,
    _fold,
    _norm_alnum,
    brand_aliases,
    model_aliases,
    parse_search_intent,
)


_NIKE_TRAIL_PANTS_FAMILY = (
    "trail", "trail running", "acg", "all conditions gear", "dawn range",
    "phenom elite", "trail repel", "storm fit", "storm-fit",
)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _title_of(offer: dict) -> str:
    return str(offer.get("titre") or offer.get("title") or "").strip()


@dataclass(frozen=True)
class QualityResult:
    accepted: bool
    score: float
    brand_match: bool = False
    model_match: bool = False
    line_match: bool = False
    category_match: bool = False
    color_match: bool = False
    gender_match: bool = False
    matter_match: bool = False
    reason: str = ""


def _match_alias(title_fold: str, alias: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(_fold(alias))}(?![a-z0-9])", title_fold))



# Mod?les dont la famille produit est sans ambigu?t? dans le catalogue actuel.
# Important : on n'exige PAS le mot "chaussures" dans le titre.
# On rejette seulement un type explicitement incompatible.
_FOOTWEAR_MODEL_KEYS = frozenset({
    ("Nike", "Air Force 1"),
    ("Nike", "P-6000"),
    ("Adidas", "Samba"),
    ("Salomon", "XT-6"),
    ("On", "Cloud 5"),
})

_NON_FOOTWEAR_TYPES = frozenset({
    "pantalon", "sweat", "t-shirt", "tee-shirt", "veste", "short",
    "ensemble", "pull", "polo", "chemise", "cargo",
    "casquette", "chaussettes", "gilet", "maillot", "manteau",
    "doudoune", "jean", "jogging", "robe", "jupe", "bonnet",
    "sac", "ceinture", "echarpe", "gants", "debardeur", "brassiere",
})

# Une seule expression compilée remplace plusieurs dizaines de ``re.search``
# par offre pour les modèles chaussures connus (Samba, AF1, P-6000...).
_NON_FOOTWEAR_ALIASES = tuple(dict.fromkeys(
    _fold(alias)
    for canonical, aliases in TYPE_ALIASES.items()
    if canonical in _NON_FOOTWEAR_TYPES
    for alias in aliases
    if _fold(alias)
))
_NON_FOOTWEAR_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(re.escape(alias) for alias in sorted(_NON_FOOTWEAR_ALIASES, key=len, reverse=True))
    + r")(?![a-z0-9])"
)


def _explicit_non_footwear_type(title_fold: str) -> str | None:
    match = _NON_FOOTWEAR_PATTERN.search(title_fold)
    return match.group(0) if match else None


def evaluate_offer(intent_or_query, offer: dict) -> QualityResult:
    """Score [0-100] et acceptation d'une offre pour une requête.

    ``intent_or_query`` peut être un ``SearchIntent`` (déjà parsé, recommandé
    sur les chemins chauds) ou une chaîne brute (pratique pour les audits).
    """
    if isinstance(intent_or_query, str):
        intent = parse_search_intent(intent_or_query)
    else:
        intent = intent_or_query

    if intent is None:
        return QualityResult(accepted=True, score=_safe_float(offer.get("score_identite"), 50.0))

    title = _title_of(offer)
    if not title:
        return QualityResult(accepted=False, score=0.0, reason="titre absent")
    if str(offer.get("niveau_identite") or "") == "rejet":
        return QualityResult(accepted=False, score=0.0, reason="identite rejet")

    title_fold = _fold(title)
    title_norm = _norm_alnum(title)
    ref_norm = _norm_alnum(str(offer.get("reference") or ""))

    score = 25.0
    hard_failures: list[str] = []
    brand_match = False
    model_match = False
    line_match = False
    category_match = False
    color_match = False
    gender_match = False
    matter_match = False

    # --- Marque (dur sauf requête-référence) ---------------------------------
    if intent.brand:
        brand_match = any(_match_alias(title_fold, alias) for alias in brand_aliases(intent.brand))
        if brand_match:
            score += 25.0
        elif not (intent.is_reference or intent.reference_token):
            hard_failures.append("marque absente du titre")

    # --- Modèle exact (dur sauf requête-référence) ---------------------------
    if intent.model:
        aliases = model_aliases(intent.brand, intent.model)
        model_norm = _norm_alnum(intent.model)
        model_match = (
            any(_match_alias(title_fold, alias) for alias in aliases)
            or bool(model_norm and model_norm in title_norm)
        )
        if model_match:
            score += 30.0
        elif not (intent.is_reference or intent.reference_token):
            hard_failures.append("modele exact absent")

    # Un mod?le connu comme chaussure peut ?tre vendu sous un titre qui ne dit
    # jamais "shoe/sneaker". On ne l'exige donc pas. En revanche, si le titre
    # annonce explicitement une autre famille produit (bra, hoodie, socks...),
    # c'est un conflit dur ? sauf si l'utilisateur a lui-m?me demand? ce type.
    if (
        intent.model
        and not intent.product_type
        and (intent.brand, intent.model) in _FOOTWEAR_MODEL_KEYS
    ):
        conflicting_type = _explicit_non_footwear_type(title_fold)
        if conflicting_type:
            hard_failures.append(
                f"categorie contradictoire avec modele chaussure: {conflicting_type}"
            )

    # --- Gamme / usage (dur) ---------------------------------------------------
    if intent.line:
        aliases = LINE_ALIASES.get(intent.line, (intent.line,))
        if intent.brand == "Nike" and intent.line == "trail" and intent.product_type == "pantalon":
            aliases = tuple(dict.fromkeys((*aliases, *_NIKE_TRAIL_PANTS_FAMILY)))
        line_match = any(_match_alias(title_fold, alias) for alias in aliases)
        if line_match:
            score += 15.0
        elif not intent.reference_token:
            hard_failures.append("gamme absente du titre")

    # --- Catégorie / type (dur) ------------------------------------------------
    if intent.product_type:
        aliases = TYPE_ALIASES.get(intent.product_type, (intent.product_type,))
        category_match = any(_match_alias(title_fold, alias) for alias in aliases)
        if category_match:
            score += 15.0
        elif not intent.reference_token:
            hard_failures.append("categorie absente du titre")

    # --- Couleur / sexe / matière (soft) ----------------------------------------
    if intent.color:
        color_match = any(_match_alias(title_fold, alias) for alias in COLOR_ALIASES.get(intent.color, ()))
        if color_match:
            score += 8.0
    if intent.gender:
        gender_match = any(_match_alias(title_fold, alias) for alias in GENDER_ALIASES.get(intent.gender, ()))
        if gender_match:
            score += 5.0
    if intent.matter:
        matter_match = any(_match_alias(title_fold, alias) for alias in MATTER_ALIASES.get(intent.matter, ()))
        if matter_match:
            score += 5.0

    # --- Référence produit (soft, renforce le score sans rejeter) ---------------
    if intent.reference_token:
        if intent.reference_token in title_norm or (ref_norm and intent.reference_token in ref_norm):
            score += 40.0
        elif intent.brand:
            hard_failures.append("reference absente")
    elif intent.is_reference:
        if ref_norm or intent.canonical and _norm_alnum(intent.canonical) in title_norm:
            score += 40.0
        else:
            score += 10.0

    # --- Phrase canonique exacte dans le titre (boost) --------------------------
    if intent.canonical and len(intent.canonical) >= 4:
        for alias in (intent.canonical, intent.original):
            if alias and _match_alias(title_fold, alias):
                score += 10.0
                break

    identity = _safe_float(offer.get("score_identite"), 0.0)
    score += min(30.0, identity * 0.3)

    accepted = not hard_failures
    reason = ", ".join(hard_failures) if hard_failures else "ok"
    return QualityResult(
        accepted=accepted,
        score=round(min(100.0, max(0.0, score)), 2),
        brand_match=brand_match,
        model_match=model_match,
        line_match=line_match,
        category_match=category_match,
        color_match=color_match,
        gender_match=gender_match,
        matter_match=matter_match,
        reason=reason,
    )


def matches_intent(intent_or_query, offer: dict) -> bool:
    return evaluate_offer(intent_or_query, offer).accepted


def gate_reasons(intent_or_query, offer: dict) -> QualityResult:
    """Expose le résultat complet (score + composantes + raison) pour les audits."""
    return evaluate_offer(intent_or_query, offer)
