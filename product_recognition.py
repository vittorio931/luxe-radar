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
    "On": ["on", "on running", "on-running", "on cloud", "oncloud"],
    "Columbia": ["columbia", "columbia sportswear"],
}

ESSENTIALS_COMPETITOR_BRANDS = (
    "adidas", "nike", "reebok", "puma", "asos design", "new balance",
    "under armour", "lacoste", "champion", "fila", "tommy hilfiger",
    "calvin klein", "jack jones", "jack & jones", "hugo boss", "boss",
    "ralph lauren", "river island", "weekday", "abercrombie", "hollister",
    "ellesse", "levis", "levi's",
)


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
ACCESSORY_TERMS = [
    "sock", "socks", "chaussette", "chaussettes", "cap", "caps", "casquette",
    "hat", "beanie", "bonnet", "glove", "gloves", "gants", "bag", "bags",
    "sac", "backpack", "belt", "ceinture", "wallet", "portefeuille", "keyring",
    "keychain", "lace", "laces", "lacets",
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

    @property
    def product_type(self):
        """Compatibilité avec l'API V2.8.3 (`intent.product_type`)."""
        return self.type_name


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

    @property
    def matched(self):
        """Compatibilité avec l'API V2.8.3 (`result.matched`)."""
        return self.accepted


def _brand_aliases(brand):
    return tuple(_dedupe(BRAND_ALIASES.get(brand, [brand])))


def _on_brand_context(text):
    """Détecte la marque On sans confondre le mot anglais « on »."""
    tn = normalize(text)
    if not tn:
        return False
    if has_phrase(tn, "on cloud") or has_phrase(tn, "on running") or "oncloud" in tn.replace(" ", ""):
        return True
    if not has_phrase(tn, "on"):
        return False
    for model, variants in MARQUES_MODELES.get("On", {}).items():
        for variant in (model, *variants):
            vn = normalize(variant)
            vn = re.sub(r"^(?:on\s+running\s+|on\s+)", "", vn).strip()
            if vn and len(vn) >= 4 and has_phrase(tn, vn):
                return True
    return False


def _brand_present(text, brand):
    if brand == "On":
        return _on_brand_context(text)
    return any(has_phrase(text, alias) for alias in _brand_aliases(brand))


def _detect_brand(text):
    tn = normalize(text)
    candidates = []
    for brand, aliases in BRAND_ALIASES.items():
        if brand == "On" and _on_brand_context(tn):
            candidates.append((12, brand))
            continue
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
        if brand == "On":
            if _on_brand_context(tn):
                found.append(brand)
            continue
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
        # Un nom de modèle identique à un type générique ("ensemble",
        # "short", "hoodie"...) ne doit pas devenir une contrainte de modèle.
        # C'était le cas d'Essentials/Ensemble et cela déclassait de vrais FOG.
        if normalize(model) in GENERIC_MODEL_TOKENS:
            continue
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


def _detect_known_model_in_text(text, brand):
    if not brand:
        return None
    tn = normalize(text)
    candidates = []
    for model, variants in MARQUES_MODELES.get(brand, {}).items():
        if normalize(model) in GENERIC_MODEL_TOKENS:
            continue
        for alias in _dedupe([model, *variants]):
            an = normalize(alias)
            # Retire uniquement un préfixe de marque au début ; le reste du
            # modèle (Cloud, Hybrid, P-6000...) doit rester intact.
            for ba in sorted(_brand_aliases(brand), key=len, reverse=True):
                if an.startswith(ba + " "):
                    an = an[len(ba):].strip()
                    break
            if an and has_phrase(tn, an):
                candidates.append((len(an), model))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


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


def parse_query(query):
    """Alias rétrocompatible V2.8.3 vers le profil structuré V2.8.6."""
    return build_query_profile(query)


def _requested_trailing_model_variants(profile):
    """Retourne les suffixes de version placés juste après un modèle connu.

    Exemple : ``Nike Pegasus 42`` -> ("42",). Les tailles explicites
    (``taille 42``, ``size 42``) ne sont pas prises pour une version de modèle.
    Cela permet au moteur de rester générique même lorsqu'une version récente
    n'est pas encore inscrite dans ``modeles.py``.
    """
    if not profile.model:
        return ()
    qt = tokens(profile.normalized)
    mt = tokens(profile.model)
    if not qt or not mt:
        return ()
    # Cherche la dernière occurrence contiguë du nom de modèle dans la requête.
    end = None
    for i in range(0, len(qt) - len(mt) + 1):
        if qt[i:i + len(mt)] == mt:
            end = i + len(mt)
    if end is None:
        return ()
    out = []
    size_words = {"taille", "size", "pointure", "eu", "us", "uk"}
    for tok in qt[end:end + 2]:
        idx = qt.index(tok, end) if tok in qt[end:] else -1
        if idx > 0 and qt[idx - 1] in size_words:
            break
        # Versions courantes : 2, 5, 10, 42, 2002, v2, x5, etc.
        if re.fullmatch(r"(?:v|x)?\d{1,4}[a-z]?", tok):
            if tok not in mt and tok not in out:
                out.append(tok)
            continue
        break
    return tuple(out)


def _near_model_numeric_variants(text, model):
    """Nombres/version vus près du nom de modèle dans un titre."""
    tn = tokens(text)
    mt = tokens(model)
    if not tn or not mt:
        return ()
    found = []
    for i in range(0, len(tn) - len(mt) + 1):
        if tn[i:i + len(mt)] != mt:
            continue
        for tok in tn[i + len(mt):i + len(mt) + 3]:
            if re.fullmatch(r"(?:v|x)?\d{1,4}[a-z]?", tok):
                if tok not in found:
                    found.append(tok)
                break
            if tok in {"mens", "womens", "men", "women", "shoe", "shoes", "trainer", "trainers"}:
                continue
            break
    return tuple(found)


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
            # Le mot français « ensemble » est une preuve forte quand le titre
            # contient aussi l'identité demandée. V2.8.6 le sous-notait et
            # rejetait des annonces eBay libellées exactement ainsi.
            "ensemble", "tracksuit", "track suit", "survetement", "matching set", "co ord", "coord", "two piece",
            "2 piece", "2pcs", "2 pcs", "hoodie set", "sweat set", "jogging set",
            "hoodie joggers", "hoodie pants", "hoodie sweatpants",
            "sweatshirt joggers", "sweatshirt pants", "top bottom", "top and bottom", "sweatsuit",
            "jogging suit",
        ]
        if any(has_phrase(title_n, p) for p in strong):
            return 30, "ensemble"
        tops = ["hoodie", "sweatshirt", "sweat", "crewneck", "jacket", "top", "t shirt", "tshirt", "tee", "shirt"]
        bottoms = ["pants", "trousers", "sweatpants", "jogger", "joggers", "short", "shorts", "bottoms"]
        if any(has_phrase(title_n, p) for p in tops) and any(has_phrase(title_n, p) for p in bottoms):
            return 30, "ensemble"
        # Bare "set/outfit" reste une preuve moyenne : on l'accepte seulement
        # avec un contexte habillement (terme mode ou genre vestimentaire). Les
        # catégories explicitement non-mode ont déjà été rejetées au-dessus.
        contexte_mode = any(has_phrase(title_n, p) for p in FASHION_TERMS) or any(
            has_phrase(title_n, p)
            for p in ("men", "mens", "women", "womens", "homme", "femme", "unisex")
        )
        if (has_phrase(title_n, "set") or has_phrase(title_n, "outfit")) and contexte_mode:
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
    brand_present = False
    model_hit = False
    model_signature_hit = False
    type_delta = 0

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
        brand_present = _brand_present(combined, profile.brand)
        if brand_present:
            score += 30
            reasons.append(f"marque {profile.brand} détectée")
        else:
            tolerance = SOURCE_TITLE_TOLERANCE.get(str(marketplace or ""), 0)
            score -= max(5, 18 - tolerance)
            reasons.append("marque absente du titre")

        # "Essentials" est aussi un mot de gamme très générique (ex. Nike
        # Sportswear Club Essentials) : hors d'une recherche FOG/Essentials,
        # il ne doit pas être traité comme une marque concurrente explicite.
        competitors = [
            b for b in detected_brands
            if b != profile.brand and not (b == "Essentials" and profile.brand != "Essentials")
        ]
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

    # ESSENTIALS est un cas collision très fréquent : certaines marques ne sont
    # pas nécessairement dans le catalogue de modèles, mais leur nom explicite
    # doit quand même empêcher de confondre leur gamme "Essentials" avec FOG.
    if profile.brand == "Essentials":
        explicit_competitor = next(
            (brand for brand in ESSENTIALS_COMPETITOR_BRANDS if has_phrase(title_n, brand)),
            None,
        )
        if explicit_competitor and not any(
            has_phrase(title_n, marker)
            for marker in ("fear of god", "fog essentials", "essentials fear of god")
        ):
            score -= 42
            conflicts.append(f"autre marque explicite : {explicit_competitor}")

    # Model / line evidence.
    if profile.model:
        model_hit = any(_phrase_or_close_present(combined, alias) for alias in profile.model_aliases)
        if model_hit:
            score += 24
            reasons.append(f"modèle/ligne {profile.model} détecté")
        else:
            # Distinctive model tokens can be enough if catalogue variants contain the brand too.
            model_tokens = [t for t in tokens(profile.model) if t not in GENERIC_MODEL_TOKENS and t not in STOPWORDS]
            if model_tokens and all(_phrase_or_close_present(combined, t) for t in model_tokens):
                model_signature_hit = True
                score += 18
                reasons.append(f"signature modèle {profile.model} détectée")
            else:
                score -= 15
                reasons.append(f"modèle/ligne {profile.model} non confirmé")

    if profile.model and profile.brand:
        detected_model = _detect_known_model_in_text(combined, profile.brand)
        if detected_model and detected_model != profile.model:
            score -= 38
            conflicts.append(f"autre modèle explicite : {detected_model}")

    if profile.model:
        requested_variants = _requested_trailing_model_variants(profile)
        if requested_variants:
            missing_variants = [v for v in requested_variants if not has_phrase(combined, v)]
            seen_variants = _near_model_numeric_variants(combined, profile.model)
            if missing_variants and seen_variants:
                score -= 35
                conflicts.append(
                    f"autre version du modèle : {'/'.join(seen_variants)} au lieu de {'/'.join(missing_variants)}"
                )

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
            weight = 20 if descriptor in IMPORTANT_DESCRIPTORS else 7
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

    # Un accessoire explicite ne doit jamais passer pour un pantalon, sweat,
    # veste, etc. Exemple réel de régression : « Nike Trail socks » était
    # accepté comme pantalon parce que marque + Trail donnaient assez de points.
    if profile.type_name and any(has_phrase(title_n, term) for term in ACCESSORY_TERMS):
        score -= 55
        conflicts.append("accessoire incompatible avec le type demandé")

    # Generic title noise without fashion evidence shouldn't pass a fashion query.
    if profile.type_name and not any(has_phrase(title_n, p) for p in FASHION_TERMS):
        # Shoes often lack the word "fashion" but do contain model evidence; use a softer penalty.
        score -= 8

    score = max(0, min(100, int(round(score))))

    # V2.8.7 : une ligne/modèle explicitement demandé devient une contrainte
    # d'identité. « Under Armour Hybrid » ne doit pas accepter Challenger/Rival
    # uniquement parce que la marque et le type correspondent.
    if profile.model and not (model_hit or model_signature_hit):
        conflicts.append(f"modèle/ligne {profile.model} absent")

    # Une marque demandée doit être réellement visible dans le titre ou les
    # métadonnées. Sur un grossiste, l'absence de marque n'est tolérée que si
    # un modèle distinctif demandé est lui-même identifié. Le simple retour de
    # la page de recherche n'est pas une preuve suffisante.
    if profile.brand and not brand_present:
        # V2.8.8 : si l'utilisateur écrit explicitement une marque, elle doit
        # être visible dans le titre/métadonnées. Un nom de modèle seul ne
        # prouve pas la marque (ex. un produit générique "Trail" sur DHgate).
        conflicts.append(f"marque {profile.brand} non confirmée")

    hard_conflict = any(
        c.startswith("catégorie manifestement")
        or c.startswith("autre marque explicite")
        or c.startswith("autre modèle explicite")
        or c.startswith("autre version du modèle")
        or "Trail Blazers" in c
        or c.startswith("type incompatible")
        or c.startswith("accessoire incompatible")
        or (c.startswith("modèle/ligne") and c.endswith("absent"))
        or (c.startswith("marque ") and c.endswith("non confirmée"))
        for c in conflicts
    )

    # V2.8.6 : précision et rappel sont séparés. Les marketplaces grossistes
    # ont souvent des titres qui omettent la marque ; on garde alors un résultat
    # comme "possible" s'il présente le bon type/modèle, mais on ne le laisse
    # jamais devenir "fort" sans preuves suffisantes. Les conflits explicites
    # restent des rejets fermes.
    marketplace_name = str(marketplace or "")
    wholesale = marketplace_name in {"DHgate", "AliExpress", "1688", "Hacoo"}
    threshold = 56
    possible_threshold = 45
    if wholesale:
        threshold = 58
        possible_threshold = 38

    strong_allowed = True
    if profile.brand and not brand_present:
        strong_allowed = False
    if profile.model and not (model_hit or model_signature_hit):
        strong_allowed = False
    if profile.type_name and type_delta < 20:
        strong_allowed = False

    if hard_conflict or score < possible_threshold:
        level = "rejet"
        accepted = False
    elif score >= threshold and strong_allowed:
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


def recognize_product(query, title, marketplace=None, extra_text=""):
    """API V2.8.3 conservée : arguments `(query, title)` dans cet ordre.

    Le moteur interne V2.8.6 utilise `recognize(title, query, ...)`. Garder ce
    wrapper évite de casser d'anciens tests/scripts sans créer deux moteurs.
    """
    return recognize(title, query, marketplace=marketplace, extra_text=extra_text)
