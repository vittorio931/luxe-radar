"""SearchIntent : représentation structurée d'une requête shopping (V3.7.x).

Une requête ``casquette Nike Trail`` n'est pas une simple liste de tokens :
c'est une entité avec marque, gamme/usage, catégorie, couleur, sexe, matière.
Ce module construit cette représentation une seule fois et de façon déterministe.

Dépendances stdlib uniquement (rejoint l'exigence d'index_engine sans
dépendance). La source de vérité du vocabulaire catégorie provient de
``search_understanding.TYPE_ALIASES`` étendue ici (casquette, chaussettes,
manteau, robe…), alignée sur ``radar_engine._TYPES_RECHERCHE_MULTI``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata

from search_understanding import (
    BRAND_ALIASES as SEARCH_BRAND_ALIASES,
    TYPE_ALIASES as BASE_TYPE_ALIASES,
    canonicalize_search_query,
    understand_query,
)
from modeles import MARQUES_MODELES


def _fold(text) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9' ]", " ", s).strip()


def _tokens(text) -> list[str]:
    return _fold(text).split()


def _norm_alnum(text) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def _contains_word(text_folded: str, word: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text_folded))


# ---------------------------------------------------------------------------
# Vocabulaire unique étendu (superset de search_understanding.TYPE_ALIASES)
# ---------------------------------------------------------------------------

_EXTRA_TYPE_ALIASES = {
    "casquette": ("casquette", "casquettes", "cap", "caps", "casq"),
    "chaussettes": ("chaussette", "chaussettes", "sock", "socks"),
    "gilet": ("gilet", "gilets", "bodywarmer", "gilet sans manches"),
    "maillot": ("maillot", "maillots", "jersey", "jerseys", "maillot de bain"),
    "manteau": ("manteau", "manteaux", "overcoat", "parka", "trench"),
    "doudoune": ("doudoune", "doudounes", "puffer", "down jacket", "duvet"),
    "jean": ("jean", "jeans", "denim"),
    "jogging": ("jogging", "jogging pantalon", "trackpant", "track pants", "sweatpants"),
    "robe": ("robe", "robes", "dress", "dresses"),
    "jupe": ("jupe", "jupes", "skirt", "skirts"),
    "bonnet": ("bonnet", "bonnets", "beanie", "beanies", "tuque"),
    "sac": ("sac", "sacs", "bag", "bags", "tote", "backpack", "sac a dos"),
    "ceinture": ("ceinture", "ceintures", "belt", "belts"),
    "echarpe": ("echarpe", "echarpes", "scarf", "scarves", "snood"),
    "gants": ("gant", "gants", "glove", "gloves"),
    "debardeur": ("debardeur", "debardeurs", "tank top", "tanktop", "singlet"),
    "baskets": ("basket", "baskets", "sneaker", "sneakers", "trainers"),
    "chaussures": ("chaussure", "chaussures", "shoe", "shoes", "running shoe", "running shoes"),
    "tee-shirt": ("t shirt", "tshirt", "tee shirt", "tee", "t-shirts"),
}

TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    **BASE_TYPE_ALIASES,
    **_EXTRA_TYPE_ALIASES,
}

# Couleurs (canonical -> alias FR/EN).
COLOR_ALIASES: dict[str, tuple[str, ...]] = {
    "noir": ("noir", "noire", "black"),
    "blanc": ("blanc", "blanche", "white"),
    "rouge": ("rouge", "red"),
    "bleu": ("bleu", "bleue", "blue"),
    "vert": ("vert", "verte", "green"),
    "gris": ("gris", "grise", "grey", "gray"),
    "beige": ("beige", "ecru"),
    "marron": ("marron", "brown", "tan"),
    "jaune": ("jaune", "yellow"),
    "rose": ("rose", "pink"),
    "violet": ("violet", "violette", "purple", "lilac"),
    "orange": ("orange",),
    "kaki": ("kaki", "khaki", "olive"),
    "bordeaux": ("bordeaux", "burgundy", "wine"),
    "navy": ("navy", "marine", "navy blue"),
    "or": ("gold", "golden"),
    "argent": ("argent", "argente", "silver"),
    "creme": ("creme", "cream"),
    "multicolore": ("multicolore", "multicolor", "camouflage", "multi color"),
    "denim": ("denim", "blue denim"),
}

# Sexes / publics (canonical -> alias FR/EN).
GENDER_ALIASES: dict[str, tuple[str, ...]] = {
    "homme": ("homme", "men", "mens", "male", "man"),
    "femme": ("femme", "women", "womens", "female", "woman"),
    "enfant": ("enfant", "children", "kids", "kid", "child", "junior"),
    "fille": ("fille", "girl", "girls"),
    "garcon": ("garcon", "garcons", "boy", "boys"),
    "unisexe": ("unisexe", "unisex", "unisexe"),
}

# Gammes / usages (canonical -> alias FR/EN). Une gamme n'est PAS un modèle exact :
# ``Nike Trail`` admet ``Nike Pegasus Trail 5``.
LINE_ALIASES: dict[str, tuple[str, ...]] = {
    "trail": ("trail", "offroad", "off road", "off-road", "trail running"),
    "running": ("running", "course a pied", "run"),
    "miler": ("miler", "milers"),
    "lifestyle": ("lifestyle", "street", "streetwear", "casual"),
    "basketball": ("basketball", "basket", "basket ball", "b-ball"),
    "soccer": ("soccer", "football", "foot", "futsal"),
    "tennis": ("tennis",),
    "golf": ("golf",),
    "hiking": ("hiking", "hike", "randonnee", "randonner"),
    "climbing": ("climbing", "escalade"),
    "ski": ("ski", "skiing"),
    "snowboard": ("snowboard",),
    "cycling": ("cycling", "velo", "bike", "biking", "cyclisme"),
    "gym": ("gym", "fitness", "training", "crossfit", "workout"),
    "yoga": ("yoga",),
    "outdoor": ("outdoor", "camping", "technique"),
}

# Matières (canonical -> alias FR/EN).
MATTER_ALIASES: dict[str, tuple[str, ...]] = {
    "cuir": ("cuir", "leather"),
    "laine": ("laine", "wool", "merino"),
    "coton": ("coton", "cotton"),
    "cachemire": ("cachemire", "cashmere"),
    "soie": ("soie", "silk"),
    "polyester": ("polyester",),
    "nylon": ("nylon",),
    "goretex": ("goretex", "gore tex", "gore-tex"),
}

# Clés de MARQUES_MODELES qui décrivent une GAMME, pas un modèle unique.
_LINE_MODEL_KEYS = {"Trail", "Miler", "Phenom Elite", "Dri-FIT ADV"}


@dataclass(frozen=True)
class SearchIntent:
    original: str
    canonical: str
    brand: str | None = None
    model: str | None = None
    line: str | None = None
    product_type: str | None = None
    color: str | None = None
    gender: str | None = None
    matter: str | None = None
    is_reference: bool = False
    reference_token: str | None = None
    # Tokens significatifs pour la récupération de candidats (FTS/LIKE).
    required_tokens: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "canonical": self.canonical,
            "brand": self.brand,
            "model": self.model,
            "line": self.line,
            "product_type": self.product_type,
            "color": self.color,
            "gender": self.gender,
            "matter": self.matter,
            "is_reference": self.is_reference,
        }


def _find_canonical(alias_map: dict[str, tuple[str, ...]], text_folded: str):
    """Retourne (canonical, alias trouvé) ou (None, None) pour le premier alias
    présent dans le texte replié (phrase exacte, bornes de mot)."""
    best = None
    best_alias = None
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            if alias and _contains_word(text_folded, alias):
                return canonical, alias
    return best, best_alias


def _infer_unique_model_brand(text_folded: str):
    """Infère (marque, modèle) quand un modèle connu suffit à lui seul.

    Exemples : ``Air Force 1`` -> Nike / Air Force 1, ``Samba`` -> Adidas /
    Samba. L'inférence reste volontairement conservatrice :
    - uniquement des alias exacts présents dans MARQUES_MODELES ;
    - aucune inférence pour les types génériques (polo, short, t-shirt...) ;
    - aucune inférence pour les gammes (Trail, Miler...) ;
    - si le même alias appartient à plusieurs couples marque/modèle, on ne
      choisit rien.
    """
    if not text_folded:
        return None, None

    generic_aliases = {
        _fold(alias)
        for aliases in TYPE_ALIASES.values()
        for alias in aliases
        if alias
    }
    generic_aliases.update(
        _fold(alias)
        for aliases in LINE_ALIASES.values()
        for alias in aliases
        if alias
    )

    matches = []
    for brand, models in MARQUES_MODELES.items():
        if not isinstance(models, dict):
            continue
        for model, aliases in models.items():
            if model in _LINE_MODEL_KEYS or model.casefold() in LINE_ALIASES:
                continue
            for alias in (model, *(aliases or ())):
                alias_folded = _fold(alias)
                if not alias_folded or alias_folded in generic_aliases:
                    continue
                alias_alnum = _norm_alnum(alias_folded)
                min_length = 3 if any(ch.isdigit() for ch in alias_alnum) else 4
                if len(alias_alnum) < min_length:
                    continue
                if _contains_word(text_folded, alias_folded):
                    matches.append((len(alias_folded), str(brand), str(model), alias_folded))

    if not matches:
        return None, None
    longest = max(length for length, _brand, _model, _alias in matches)
    winners = {(brand, model) for length, brand, model, _alias in matches if length == longest}
    if len(winners) != 1:
        return None, None
    return next(iter(winners))


def parse_search_intent(query: str) -> SearchIntent:
    """Parse une requête libre en intent structurée et déterministe."""
    query = str(query or "").strip()
    if not query:
        return SearchIntent(original="", canonical="")

    canonical = canonicalize_search_query(query) if canonicalize_search_query else query
    query_folded = _fold(query)

    # Un modèle exact connu peut lui-même ressembler à une référence (``XT-6``,
    # ``P-6000``). On tente donc l'inférence catalogue AVANT le fallback
    # « référence produit pure ».
    inferred_brand, inferred_model = _infer_unique_model_brand(query_folded)
    is_reference = bool(
        not inferred_model
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,}", query.strip())
        and re.search(r"\d", query)
    )

    info = None
    try:
        info = understand_query(query)
    except Exception:
        info = None

    brand = str(getattr(info, "brand", "") or "").strip() or None
    model = str(getattr(info, "model", "") or "").strip() or None

    # Une requête peut contenir un modèle mondialement identifiable sans sa
    # marque (``Air Force 1``, ``Samba``, ``XT-6``...). ``understand_query``
    # exige volontairement une marque pour son fuzzy ; ici on ajoute seulement
    # une inférence exacte et non ambiguë depuis le catalogue de modèles.
    if not brand and not is_reference and inferred_brand and inferred_model:
        brand, model = inferred_brand, inferred_model

    # Gamme ou modèle exact ? ``Nike Trail`` = gamme (admets Pegasus Trail 5) ;
    # ``Nike P-6000`` = modèle exact (rejette Air Force 1).
    line = None
    if model:
        if model in _LINE_MODEL_KEYS or model.casefold() in LINE_ALIASES:
            line = model.casefold() if model.casefold() in LINE_ALIASES else model.casefold()
            model = None
        elif model.casefold() in LINE_ALIASES:
            line = model.casefold()
            model = None
    if line is None:
        found_line, _alias = _find_canonical(LINE_ALIASES, query_folded)
        if found_line:
            line = found_line

    product_type = None
    found_type, _t_alias = _find_canonical(TYPE_ALIASES, query_folded)
    if found_type:
        product_type = found_type

    color, _c_alias = _find_canonical(COLOR_ALIASES, query_folded)
    gender, _g_alias = _find_canonical(GENDER_ALIASES, query_folded)
    matter, _m_alias = _find_canonical(MATTER_ALIASES, query_folded)

    # Token de référence libre (inconnu des modèles) : ex. ``DM4652-040`` dans
    # une requête mixte ``Nike DM4652-040``.
    reference_token = None
    if not is_reference:
        for tok in _tokens(query):
            alnum = _norm_alnum(tok)
            if len(alnum) >= 4 and re.search(r"\d", alnum):
                brand_l = _fold(brand) if brand else ""
                if brand_l and alnum.startswith(brand_l.replace(" ", "")):
                    continue
                reference_token = alnum
                break

    required = []
    if brand:
        required.extend(_fold(brand).split())
    if model:
        required.extend(_fold(model).split())
    if line:
        required.extend(_fold(line).split())
    if reference_token:
        required.append(reference_token)
    seen = set()
    required = tuple(tok for tok in required if len(tok) >= 2 and not (tok in seen or seen.add(tok)))[:8]

    return SearchIntent(
        original=query,
        canonical=canonical,
        brand=brand,
        model=model,
        line=line,
        product_type=product_type,
        color=color,
        gender=gender,
        matter=matter,
        is_reference=is_reference,
        reference_token=reference_token,
        required_tokens=required,
    )


def brand_aliases(brand: str) -> tuple[str, ...]:
    """Alias repliés de la marque (les mots isolés ambigus sont écartés)."""
    aliases = tuple(SEARCH_BRAND_ALIASES.get(brand, (brand,))) or (brand,)
    if brand == "On":
        aliases = tuple(a for a in aliases if _fold(a) != "on") or ("on cloud", "on running")
    return tuple(_fold(a) for a in aliases)


def model_aliases(brand: str | None, model: str) -> tuple[str, ...]:
    """Alias repliés du modèle exact (depuis le catalogue marque->modèles)."""
    base = _fold(model)
    if brand:
        family = MARQUES_MODELES.get(brand, {})
        aliases = family.get(model, ()) if isinstance(family, dict) else ()
        return tuple(_fold(a) for a in aliases) or (base,)
    return (base,)
