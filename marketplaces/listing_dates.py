"""Dates de publication sourcées ; jamais confondues avec la détection locale."""
from datetime import datetime, timezone
import math
import re
import time
import unicodedata


def timestamp(value):
    try:
        if isinstance(value, (float, int)):
            result = float(value)
        else:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if date.tzinfo is None:
                return None
            result = date.timestamp()
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def vinted_publication(text, now=None):
    """La borne ancienne de l'arrondi empêche de sous-estimer l'âge d'une annonce."""
    now = time.time() if now is None else now
    normal = unicodedata.normalize("NFKD", text).casefold()
    normal = " ".join("".join(c for c in normal if not unicodedata.combining(c)).split())
    instant = re.search(r"\bajoute\s+(?:a l['’ ]instant|il y a moins d['’ ]une minute)\b", normal)
    if instant:
        return {"listed_at": now - 60, "listed_at_approximate": True,
                "listing_date_source": "Page publique Vinted", "listing_date_label": instant[0]}
    match = re.search(r"\bajoute\s+(?:il y a\s+)?(\d+|une?|quelques)\s+(secondes?|minutes?|heures?|jours?|semaines?|mois|ans?)\b", normal)
    if not match:
        return {}
    value, unit = match.groups()
    amount = int(value) if value.isdigit() else 1 if value in ("un", "une") else 5
    seconds = next(v for key, v in (("seconde", 1), ("minute", 60), ("heure", 3600), ("jour", 86400), ("semaine", 604800), ("mois", 2678400), ("an", 31622400)) if unit.startswith(key))
    return {
        "listed_at": now - (amount + 1) * seconds,
        "listed_at_approximate": True,
        "listing_date_source": "Page publique Vinted",
        "listing_date_label": match[0],
    }


def is_recent(offer, max_age_seconds=900, now=None):
    now = time.time() if now is None else now
    published = timestamp(offer.get("listed_at"))
    return published is not None and 0 <= now - published <= max_age_seconds
