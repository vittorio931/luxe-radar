from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

from modeles import MARQUES_MODELES


# ---------------------------------------------------------------------------
# Normalisation / vocabulaire
# ---------------------------------------------------------------------------

def normalize(text):
    text = "" if text is None else str(text)
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("-", " ").replace("_", " ")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Corrections ciblées et sûres vues dans le projet / logs.
    text = re.sub(r"\b(?:essantials|essencials|essensials|essentails)\b", "essentials", text)
    text = re.sub(r"\bmiller\b", "miler", text)
    return text


def tokens(text):
    return re.findall(r"[a-z0-9]+", normalize(text))


def has_phrase(text, phrase):
    hay = normalize(text)
    needle = normalize(phrase)
    if not hay or not needle:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay))


def _dedupe(values):
    out = []
    seen = set()
    for value in values:
        value = normalize(value)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


BRAND_ALIASES = {
    "Essentials": [
        "essentials", "fear of god essentials", "fear of god", "fog essentials", "fog",
    ],
    "Under Armour": ["under armour", "underarmour", "ua"],
    "C.P. Company": ["c p company", "cp company", "c.p. company"],
    "New Balance": ["new balance", "nb"],
    "Stone Island": ["stone island"],
    "Jordan": ["jordan", "air jordan"],
}

# Ajoute automatiquement les marques du catalogue, en conservant les alias manuels.
for _brand in MARQUES_MODELES:
    BRAND_ALIASES.setdefault(_brand, [_brand])
    if _brand not in BRAND_ALIASES[_brand]:
        BRAND_ALIASES[_brand].append(_brand)


TYPE_ALIASES = {
    "ensemble": [
        "ensemble", "tracksuit", "track suit", "survetement", "matching set", "co ord", "coord",
        "two piece", "2 piece", "2pcs", "2 pcs", "hoodie set", "sweat set", "jogging set",
        "top bottom", "top and bottom", "hoodie joggers", "hoodie pants", "hoodie sweatpants",
        "sweatshirt joggers", "sweatshirt pants", "outfit", "sweatsuit", "jogging suit",
    ],
    "tshirt": ["t shirt", "tshirt", "tee shirt", "tee", "short sleeve tee", "camiseta"],
    "pantalon": [
        "pantalon", "pants", "trousers", "jogger", "joggers", "sweatpants", "sweatpant", "bottoms",
    ],
    "short": ["short", "shorts", "bermuda"],
    "veste": ["veste", "jacket", "coat", "blouson", "windbreaker", "anorak", "overshirt"],
    "sweat": ["sweat", "sweatshirt", "hoodie", "crewneck", "hooded"],
    "pull": ["pull", "sweater", "pullover", "knit", "fleece"],
    "chaussures": ["chaussure", "chaussures", "shoes", "shoe", "sneaker", "sneakers", "trainers", "basket"],
    "polo": ["polo"],
    "chemise": ["chemise", "shirt", "overshirt"],
    "cargo": ["cargo", "cargo pants", "cargo trousers"],
    "gilet": ["gilet", "vest", "bodywarmer"],
    "maillot": ["maillot", "jersey"],
}

# Type compatibility: useful when marketplaces translate/rename a product.
TYPE_COMPATIBLE = {
    "sweat": {"pull"},
    "pull": {"sweat"},
    "pantalon": {"cargo"},
    "cargo": {"pantalon"},
    "veste": {"chemise"},  # overshirt / light jacket ambiguity
}

# Strong fashion evidence for ambiguous words like "set".
FASHION_TERMS = _dedupe(
    alias
    for aliases in TYPE_ALIASES.values()
    for alias in aliases
) + [
    "loungewear", "sportswear", "activewear", "menswear", "womenswear", "clothing", "apparel",
]

NON_FASHION_TERMS = [
    "skincare", "skin care", "beauty", "brush", "makeup", "cosmetic", "shampoo", "conditioner",
    "haircare", "hair care", "body wash", "perfume", "fragrance", "cologne", "serum", "cleanser",
    "moisturizer", "moisturiser", "lotion", "candle", "toothbrush", "toothpaste", "remote control",
    "remote holder", "phone case", "wall mount", "kitchen", "food cover", "storage bag", "saran wrap",
    "car accessory", "pet", "dog", "cat", "toy", "toys", "jewelry", "jewellery", "watch strap",
]

STOPWORDS = {
    "de", "du", "des", "le", "la", "les", "un", "une", "pour", "avec", "et", "en", "sur",
    "the", "for", "with", "and", "a", "an", "of", "to", "men", "mens", "man", "women", "womens",
    "woman", "homme", "femme", "taille", "size", "new", "nouveau", "original", "authentic",
}

GENERIC_MODEL_TOKENS = {
    "hoodie", "sweat", "sweatshirt", "tshirt", "shirt", "tee", "pantalon", "pants", "jogger",
    "short", "shorts", "jacket", "veste", "ensemble", "set", "shoes", "shoe", "sneaker", "polo",
}

IMPORTANT_DESCRIPTORS = {
    "trail", "running", "run", "hybrid", "miler", "tech", "fleece", "goretex", "gore", "tex",
    "waterproof", "reflective", "windrunner", "division", "pro", "elite", "performance",
}

SOURCE_TITLE_TOLERANCE = {
    # Wholesale sources frequently omit/obfuscate brands in titles.
    "DHgate": 8,
    "AliExpress": 6,
    "1688": 8,
    "Hacoo": 8,
    # Structured fashion marketplaces usually have informative titles.
    "ASOS": 0,
    "SSENSE": 0,
    "Vinted": 2,
    "eBay": 2,
    "Grailed": 2,
}


@dataclass(frozen=True)
class QueryProfile:
    original: str
    normalized: str
    brand: str | None
    brand_aliases: tuple[str, ...]
    model: str | None
    model_aliases: tuple[str, ...]
    type_name: str | None
    type_aliases: tuple[str, ...]
    descriptors: tuple[str, ...]


@dataclass(frozen=True)
class RecognitionResult:
    score: int
    level: str
    accepted: bool
    reasons: tuple[str, ...]
    conflicts: tuple[str, ...]
    profile: QueryProfile
    detected_brand: str | None = None
    detected_type: str | None = None


def _brand_aliases(brand):
    return tuple(_dedupe(BRAND_ALIASES.get(brand, [brand])))


def _detect_brand(text):
    tn = normalize(text)
    candidates = []
    for brand, aliases in BRAND_ALIASES.items():
        for alias in _dedupe(aliases):
            if len(alias) <= 2 and alias not in {"ua", "nb"}:
                continue
            if has_phrase(tn, alias):
                # Prefer longest explicit alias to avoid "Jordan" vs other noise.
                candidates.append((len(alias), brand))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]




def _detect_brands(text):
    tn = normalize(text)
    found = []
    for brand, aliases in BRAND_ALIASES.items():
        if any(has_phrase(tn, alias) for alias in _dedupe(aliases)):
            found.append(brand)
    return tuple(found)

def _detect_type(text):
    tn = normalize(text)
    matches = []
    for type_name, aliases in TYPE_ALIASES.items():
        for alias in aliases:
            if has_phrase(tn, alias):
                # Penalise bare "outfit" and generic expressions by preferring longer aliases.
                matches.append((len(normalize(alias)), type_name))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def _find_model_in_query(query, brand):
    if not brand:
        return None, ()
    models = MARQUES_MODELES.get(brand, {})
    qn = normalize(query)
    candidates = []
    for model, variants in models.items():
        aliases = _dedupe([model, *variants])
        for alias in aliases:
            an = normalize(alias)
            # Remove brand words from long catalogue variants so
            # "Under Armour Hybrid" still recognizes query "Hybrid".
            if has_phrase(qn, an):
                candidates.append((len(an), model, aliases))
            else:
                brand_aliases = _brand_aliases(brand)
                stripped = an
                for ba in sorted(brand_aliases, key=len, reverse=True):
                    stripped = re.sub(rf"(?<![a-z0-9]){re.escape(ba)}(?![a-z0-9])", " ", stripped)
                stripped = re.sub(r"\s+", " ", stripped).strip()
                if stripped and stripped not in GENERIC_MODEL_TOKENS and has_phrase(qn, stripped):
                    candidates.append((len(stripped), model, aliases))
    if not candidates:
        return None, ()
    candidates.sort(reverse=True)
    _, model, aliases = candidates[0]
    return model, tuple(aliases)


def build_query_profile(query):
    original = str(query or "").strip()
    qn = normalize(original)
    brand = _detect_brand(qn)
    brand_aliases = _brand_aliases(brand) if brand else ()
    model, model_aliases = _find_model_in_query(qn, brand)
    type_name = _detect_type(qn)
    type_aliases = tuple(_dedupe(TYPE_ALIASES.get(type_name, ()))) if type_name else ()

    # Remove vocabulary already represented by brand / model / type.
    removable = set(STOPWORDS)
    for phrase in (*brand_aliases, *model_aliases, *type_aliases):
        removable.update(tokens(phrase))

    q_tokens = tokens(qn)
    descriptors = []
    for tok in q_tokens:
        if tok in removable or tok in STOPWORDS or len(tok) <= 1:
            continue
        if tok not in descriptors:
            descriptors.append(tok)

    # A model alias may have swallowed a meaningful descriptor (e.g. Hybrid).
    # Keep distinctive model words as descriptors too, but not generic garment words.
    if model:
        for tok in tokens(model):
            if tok not in GENERIC_MODEL_TOKENS and tok not in STOPWORDS and tok not in descriptors:
                descriptors.append(tok)

    return QueryProfile(
        original=original,
        normalized=qn,
        brand=brand,
        brand_aliases=tuple(brand_aliases),
        model=model,
        model_aliases=tuple(model_aliases),
        type_name=type_name,
        type_aliases=tuple(type_aliases),
        descriptors=tuple(descriptors),
    )


def _phrase_or_close_present(title_n, phrase):
    phrase_n = normalize(phrase)
    if not phrase_n:
        return False
    if has_phrase(title_n, phrase_n):
        return True
    # Only fuzzy-match a single substantial token. This handles mild SEO typo,
    # not arbitrary semantic similarity.
    pt = tokens(phrase_n)
    if len(pt) == 1 and len(pt[0]) >= 5:
        target = pt[0]
        for tok in tokens(title_n):
            if len(tok) >= 5 and SequenceMatcher(None, target, tok).ratio() >= 0.88:
                return True
    return False


def _type_evidence(title_n, requested_type):
    if not requested_type:
        return 0, None

    if requested_type == "ensemble":
        if any(has_phrase(title_n, bad) for bad in NON_FASHION_TERMS):
            return -35, None
        strong = [
            "tracksuit", "track suit", "survetement", "matching set", "co ord", "coord", "two piece",
            "2 piece", "2pcs", "2 pcs", "hoodie joggers", "hoodie pants", "hoodie sweatpants",
            "sweatshirt joggers", "sweatshirt pants", "top bottom", "top and bottom", "sweatsuit",
            "jogging suit",
        ]
        if any(has_phrase(title_n, p) for p in strong):
            return 30, "ensemble"
        tops = ["hoodie", "sweatshirt", "sweat", "crewneck", "jacket", "top", "t shirt", "tshirt", "tee", "shirt"]
        bottoms = ["pants", "trousers", "sweatpants", "jogger", "joggers", "short", "shorts", "bottoms"]
        if any(has_phrase(title_n, p) for p in tops) and any(has_phrase(title_n, p) for p in bottoms):
            return 30, "ensemble"
        # Bare "set/outfit" is only medium evidence when a fashion term exists.
        if (has_phrase(title_n, "set") or has_phrase(title_n, "outfit")) and any(
            has_phrase(title_n, p) for p in FASHION_TERMS
        ):
            return 18, "ensemble"
        return -14, _detect_type(title_n)

    aliases = TYPE_ALIASES.get(requested_type, ())
    if any(has_phrase(title_n, alias) for alias in aliases):
        return 28, requested_type

    detected = _detect_type(title_n)
    if detected in TYPE_COMPATIBLE.get(requested_type, set()):
        return 12, detected
    if detected and detected != requested_type:
        return -24, detected
    return -10, detected


def recognize(title, query, marketplace=None, extra_text=""):
    profile = build_query_profile(query)
    title_n = normalize(title)
    extra_n = normalize(extra_text)
    combined = f"{title_n} {extra_n}".strip()
    reasons = []
    conflicts = []
    score = 20  # neutral floor; evidence must build the identity.

    if not title_n:
        return RecognitionResult(0, "rejet", False, (), ("titre vide",), profile)

    # Explicit non-fashion content is a hard conflict when the query asks for apparel/footwear.
    if profile.type_name and any(has_phrase(title_n, bad) for bad in NON_FASHION_TERMS):
        conflicts.append("catégorie manifestement hors mode")
        score -= 45

    # Brand evidence / conflict.
    detected_brand = _detect_brand(title_n)
    detected_brands = _detect_brands(title_n)
    if profile.brand:
        brand_present = any(has_phrase(combined, alias) for alias in profile.brand_aliases)
        if brand_present:
            score += 30
            reasons.append(f"marque {profile.brand} détectée")
        else:
            tolerance = SOURCE_TITLE_TOLERANCE.get(str(marketplace or ""), 0)
            score -= max(5, 18 - tolerance)
            reasons.append("marque absente du titre")

        competitors = [b for b in detected_brands if b != profile.brand]
        if competitors:
            # Important pour Essentials : le mot est utilisé comme ligne par
            # adidas/Tommy/etc. La présence du mot demandé ne doit pas masquer
            # une autre marque explicitement affichée.
            score -= 42
            conflicts.append(f"autre marque explicite : {competitors[0]}")
    else:
        # No requested brand: a detected brand is useful context, not a requirement.
        if detected_brand:
            score += 4

    # Model / line evidence.
    if profile.model:
        model_hit = any(_phrase_or_close_present(combined, alias) for alias in profile.model_aliases)
        if model_hit:
            score += 24
            reasons.append(f"modèle/ligne {profile.model} détecté")
        else:
            # Distinctive model tokens can be enough if catalogue variants contain the brand too.
            model_tokens = [t for t in tokens(profile.model) if t not in GENERIC_MODEL_TOKENS and len(t) > 2]
            if model_tokens and all(_phrase_or_close_present(combined, t) for t in model_tokens):
                score += 18
                reasons.append(f"signature modèle {profile.model} détectée")
            else:
                score -= 15
                reasons.append(f"modèle/ligne {profile.model} non confirmé")

    # Product type evidence.
    type_delta, detected_type = _type_evidence(title_n, profile.type_name)
    score += type_delta
    if profile.type_name:
        if type_delta >= 20:
            reasons.append(f"type {profile.type_name} confirmé")
        elif type_delta < -20:
            conflicts.append(f"type incompatible : {detected_type or 'non identifié'}")
        elif type_delta < 0:
            reasons.append(f"type {profile.type_name} peu explicite")

    # Descriptors: weighted individually, not all-or-nothing.
    if profile.descriptors:
        matched = []
        missing = []
        for descriptor in profile.descriptors:
            if _phrase_or_close_present(combined, descriptor):
                matched.append(descriptor)
            else:
                missing.append(descriptor)
        for descriptor in matched:
            weight = 12 if descriptor in IMPORTANT_DESCRIPTORS else 7
            score += weight
        for descriptor in missing:
            weight = 8 if descriptor in IMPORTANT_DESCRIPTORS else 3
            score -= weight
        if matched:
            reasons.append("descripteurs : " + ", ".join(matched[:4]))
        if missing:
            reasons.append("descripteurs absents : " + ", ".join(missing[:4]))

    # Known collision: Trail Blazers != Nike Trail/running.
    if "trail" in profile.descriptors or has_phrase(profile.normalized, "trail"):
        if has_phrase(title_n, "trail blazers") or any(has_phrase(title_n, x) for x in ("portland", "nba", "lillard")):
            score -= 60
            conflicts.append("Trail Blazers/NBA ≠ produit trail running")

    # Bare Essentials + explicit competitor brand is a hard collision.
    if profile.brand == "Essentials":
        competitors = [b for b in detected_brands if b != "Essentials"]
        if competitors:
            conflicts.append("Essentials utilisé par une autre marque")

    # Generic title noise without fashion evidence shouldn't pass a fashion query.
    if profile.type_name and not any(has_phrase(title_n, p) for p in FASHION_TERMS):
        # Shoes often lack the word "fashion" but do contain model evidence; use a softer penalty.
        score -= 8

    score = max(0, min(100, int(round(score))))

    hard_conflict = any(
        c.startswith("catégorie manifestement")
        or c.startswith("autre marque explicite")
        or "Trail Blazers" in c
        or c.startswith("type incompatible")
        for c in conflicts
    )

    # Preserve recall on wholesale sources without accepting obvious conflicts.
    threshold = 56
    possible_threshold = 45
    if str(marketplace or "") in {"DHgate", "AliExpress", "1688", "Hacoo"}:
        threshold = 52
        possible_threshold = 42

    if hard_conflict or score < possible_threshold:
        level = "rejet"
        accepted = False
    elif score >= threshold:
        level = "fort"
        accepted = True
    else:
        level = "possible"
        accepted = True

    return RecognitionResult(
        score=score,
        level=level,
        accepted=accepted,
        reasons=tuple(_dedupe(reasons)),
        conflicts=tuple(_dedupe(conflicts)),
        profile=profile,
        detected_brand=detected_brand,
        detected_type=detected_type,
    )
