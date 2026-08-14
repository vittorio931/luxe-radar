from __future__ import annotations

import re
import unicodedata

# Ces marqueurs servent uniquement à prévenir l'utilisateur. Ils ne certifient
# jamais qu'un article est authentique ou contrefait.
HIGH_RISK_PHRASES = (
    "1:1",
    "1 1 quality",
    "replica",
    "replique",
    "réplique",
    "counterfeit",
    "contrefacon",
    "contrefaçon",
    "super fake",
    "mirror quality",
    "aaa quality",
    "unauthorized authentic",
    "ua batch",
    "clone",
)

MEDIUM_RISK_PHRASES = (
    "dupe",
    "inspired by",
    "inspire",
    "inspiré",
    "inspired",
    "same style",
    "same design",
)

# Prudence de source : ce niveau signifie seulement que l'authenticité n'est
# pas vérifiée par LUXE RADAR. Il ne signifie pas que les produits sont faux.
SOURCE_REVIEW_RECOMMENDED = {
    "dhgate",
    "1688",
    "hacoo",
    "aliexpress",
}


def _norm(value):
    text = "" if value is None else str(value)
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("-", " ").replace("_", " ").replace("’", "'")
    text = text.replace(":", " ")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(text, phrase):
    text_n = _norm(text)
    phrase_n = _norm(phrase)
    if not text_n or not phrase_n:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase_n) + r"(?![a-z0-9])", text_n) is not None


def authenticity_assessment(item, marketplace=""):
    """Retourne un niveau de prudence d'authenticité et une explication.

    Niveaux : faible, modere, eleve. Ils décrivent uniquement les signaux
    visibles dans l'annonce/la source et ne constituent pas une expertise.
    """
    if not isinstance(item, dict):
        return "modere", "Authenticité non vérifiable automatiquement", ""

    title = str(item.get("titre") or item.get("title") or "")
    description = " ".join(
        str(item.get(key) or "")
        for key in ("description", "texte", "condition", "etat")
    )
    full = f"{title} {description}".strip()

    high = [phrase for phrase in HIGH_RISK_PHRASES if _has(full, phrase)]
    if high:
        signal = ", ".join(high[:3])
        return (
            "eleve",
            "Risque élevé de contrefaçon : signal explicite dans l'annonce",
            signal,
        )

    medium = [phrase for phrase in MEDIUM_RISK_PHRASES if _has(full, phrase)]
    if medium:
        signal = ", ".join(medium[:3])
        return (
            "modere",
            "Authenticité à vérifier : formulation ambiguë dans l'annonce",
            signal,
        )

    source = str(marketplace or item.get("marketplace") or "").strip().lower()
    if source in SOURCE_REVIEW_RECOMMENDED:
        return (
            "modere",
            "Authenticité non vérifiée par LUXE RADAR sur cette source",
            "source à vérifier",
        )

    return (
        "faible",
        "Aucun signal explicite détecté — authenticité non garantie",
        "",
    )


def annotate_authenticity(item, marketplace=""):
    if not isinstance(item, dict):
        return item
    level, warning, signals = authenticity_assessment(item, marketplace=marketplace)
    item["risque_contrefacon"] = level
    item["alerte_authenticite"] = warning
    item["signaux_authenticite"] = signals
    return item
