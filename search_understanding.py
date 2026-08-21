from __future__ import annotations

from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
import re
import unicodedata

from modeles import MARQUES_MODELES


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("-", " ").replace("_", " ")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))


# Alias manuels : ils servent de garde-fou pour les marques qui ont des formes
# courtes ou très utilisées. Le fuzzy complète cette liste, il ne la remplace pas.
BRAND_ALIASES = {
    "Under Armour": ("under armour", "underarmour", "ua"),
    "On": ("on", "on running", "on-running", "on cloud", "oncloud"),
    "Columbia": ("columbia", "columbia sportswear"),
    "Arc'teryx": ("arc'teryx", "arcteryx", "arc teryx"),
    "Hoka": ("hoka", "hoka one one"),
    "Dr. Martens": ("dr martens", "dr. martens", "doc martens", "docs"),
    "Stone Island": ("stone island",),
    "C.P. Company": ("cp company", "c p company", "c.p. company"),
    "New Balance": ("new balance", "nb"),
    "The North Face": ("the north face", "north face", "tnf"),
    "Ralph Lauren": ("ralph lauren", "polo ralph lauren"),
    "Essentials": ("essentials", "fear of god essentials", "fog essentials"),
    "Nike": ("nike", "nkike", "nikee", "nikke"),
    "Adidas": ("adidas", "addidas", "adiddas"),
    "Puma": ("puma", "pumma"),
    "Vans": ("vans", "vanss"),
    "Dior": ("dior", "diior"),
    "Hoka": ("hoka", "hoka one one", "hokaa", "hkka"),
    "Asics": ("asics", "asisc"),
    "Reebok": ("reebok", "reeebok"),
    "Jordan": ("jordan", "jordann"),
    "Salomon": ("salomon", "saulmon", "salomn"),
    "Saucony": ("saucony", "sauconi"),
    "Converse": ("converse", "converce"),
    "Carhartt": ("carhartt", "carhart"),
    "Timberland": ("timberland", "timbrland"),
    "Tommy Hilfiger": ("tommy hilfiger", "tommy hilfigar"),
    "Lacoste": ("lacoste", "lacost"),
    "Burberry": ("burberry", "burbery"),
    "Moncler": ("moncler", "moncleer"),
    "Gucci": ("gucci", "gucchi"),
    "Prada": ("prada", "pradda"),
    "Balenciaga": ("balenciaga", "balanciaga"),
    "Patagonia": ("patagonia", "patagoina"),
}
for _brand in MARQUES_MODELES:
    BRAND_ALIASES.setdefault(_brand, (_brand,))

# Certaines marques contiennent dans leurs alias des mots qui sont en réalité
# le début d'un modèle. Pour On, « cloud » ne doit jamais être supprimé quand
# on cherche le modèle (On Cloud 5, On Cloudmonster, etc.).
MODEL_STRIP_ALIASES = {
    "On": ("on running", "onrunning", "on"),
}
PRETTY_BRAND_ALIASES = {
    "On": ("on running", "onrunning", "on"),
}


# Fautes observées dans les logs + variantes très sûres. Elles sont appliquées
# seulement mot pour mot, avant le fuzzy marque/modèle.
SAFE_WORD_FIXES = {
    "essantials": "essentials",
    "essencials": "essentials",
    "essensials": "essentials",
    "essentails": "essentials",
    "essentials": "essentials",
    "miller": "miler",
}

TYPE_ALIASES = {
    "pantalon": ("pantalon", "pantalons", "pant", "pants", "trouser", "trousers", "jogger", "joggers"),
    "sweat": ("sweat", "sweatshirt", "hoodie", "crewneck"),
    "t-shirt": ("t shirt", "tshirt", "tee shirt", "tee"),
    "veste": ("veste", "jacket", "coat", "blouson", "windbreaker"),
    "short": ("short", "shorts", "bermuda"),
    "ensemble": ("ensemble", "tracksuit", "track suit", "matching set", "survetement", "co ord"),
    "chaussures": ("chaussure", "chaussures", "shoe", "shoes", "sneaker", "sneakers", "trainers", "basket", "baskets"),
    "pull": ("pull", "sweater", "pullover", "knit"),
    "polo": ("polo",),
    "chemise": ("chemise", "shirt"),
    "brassiere": ("brassiere", "brassieres", "bra", "sports bra", "sport bra", "soutien gorge", "soutien-gorge"),
    "cargo": ("cargo",),
}

# Termes qu'on ne veut jamais corriger automatiquement vers un vêtement sans
# contexte de marque : "Sweet Protection" est une marque réelle, par exemple.
CONTEXTUAL_TYPE_TYPOS = {
    "sweet": "sweat",
    "swet": "sweat",
    "sweatshrt": "sweatshirt",
    "pantallon": "pantalon",
    "pantlon": "pantalon",
}


@dataclass(frozen=True)
class SearchUnderstanding:
    original: str
    canonical: str
    corrected: bool
    confidence: float
    corrections: tuple[str, ...]
    brand: str | None = None
    model: str | None = None
    product_type: str | None = None

    def to_dict(self):
        return asdict(self)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _replace_token_sequence(tokens: list[str], start: int, length: int, replacement: str) -> list[str]:
    return tokens[:start] + _tokens(replacement) + tokens[start + length :]


def _expand_compound_brand_tokens(tokens: list[str]):
    """Sépare les formes collées très sûres avant la compréhension.

    OnCloud, OnCloudmonster, OnCloudrunner, etc. sont couramment écrits sans
    espace. On reconnait dynamiquement toutes les familles On du catalogue, et
    on tolère une petite faute (Onclod, Oncloudmonstr...).
    """
    out = []
    fixes = []
    on_variants = []
    for model, aliases in MARQUES_MODELES.get("On", {}).items():
        for variant in (model, *aliases):
            vn = _norm(variant)
            vn = re.sub(r"^(?:on\s+running\s+|on\s+)", "", vn).strip()
            compact = "".join(_tokens(vn))
            if compact:
                on_variants.append((compact, vn))
    for tok in tokens:
        if len(tok) >= 6 and tok.startswith("on"):
            remainder = tok[2:]
            best = None
            for compact, pretty in on_variants:
                r = _ratio(remainder, compact)
                if r >= 0.78 and (best is None or r > best[0] or (r == best[0] and len(compact) > len(best[1]))):
                    best = (r, compact, pretty)
            if best is not None:
                replacement = ["on", *_tokens(best[2])]
                out.extend(replacement)
                fixed_text = " ".join(replacement)
                fixes.append(f"{tok} → {fixed_text}")
                continue
        out.append(tok)
    return out, fixes


def _model_context_score(tokens: list[str], brand: str, skip_start: int, skip_len: int) -> float:
    """Score de contexte modèle utilisé pour tolérer une faute sur la marque.

    Une correction agressive comme « Nikz → Nike » n'est acceptée que si la
    suite ressemble fortement à un modèle Nike (P6000, Vomero 5, etc.).
    """
    remaining = tokens[:skip_start] + tokens[skip_start + skip_len :]
    if not remaining:
        return 0.0
    text = " ".join(remaining)
    best = 0.0
    strip_aliases = MODEL_STRIP_ALIASES.get(brand, BRAND_ALIASES.get(brand, (brand,)))
    for model, aliases in MARQUES_MODELES.get(brand, {}).items():
        for variant in (model, *aliases):
            vn = _norm(variant)
            for ba in sorted((_norm(x) for x in strip_aliases), key=len, reverse=True):
                if ba:
                    vn = re.sub(rf"(?<![a-z0-9]){re.escape(ba)}(?![a-z0-9])", " ", vn)
            vn = re.sub(r"\s+", " ", vn).strip()
            vt = _tokens(vn)
            if not vt:
                continue
            target = " ".join(vt)
            if re.search(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])", text):
                return 1.0
            # Compare des fenêtres proches de la longueur du modèle.
            for size in {max(1, len(vt)-1), len(vt), len(vt)+1}:
                if size > len(remaining):
                    continue
                for i in range(len(remaining)-size+1):
                    chunk = " ".join(remaining[i:i+size])
                    best = max(best, _ratio(chunk, target))
    return best


def _best_brand(tokens: list[str]):
    best = None
    for brand, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            at = _tokens(alias)
            if not at or (len(at) == 1 and len(at[0]) <= 2):
                continue
            # Une marque multi-mots doit garder le même nombre de mots : cela
            # évite de "corriger" de longs morceaux de requête sans rapport.
            win_len = len(at)
            if win_len > len(tokens):
                continue
            for i in range(len(tokens) - win_len + 1):
                chunk = " ".join(tokens[i : i + win_len])
                target = " ".join(at)
                r = _ratio(chunk, target)
                threshold = 0.87 if win_len > 1 else (0.78 if len(target) >= 5 else 0.86)
                if chunk == target:
                    r = 1.0
                contextual = False
                if r < threshold:
                    relaxed = 0.78 if win_len > 1 else (0.70 if len(target) >= 4 else 0.80)
                    if r >= relaxed and _model_context_score(tokens, brand, i, win_len) >= 0.82:
                        contextual = True
                if (r >= threshold or contextual) and (best is None or r > best[0]):
                    best = (r, brand, i, win_len, target)
    return best


def _detect_exact_brand(tokens: list[str]):
    text = " ".join(tokens)
    matches = []
    for brand, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            an = _norm(alias)
            if not an or len(an) <= 2:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(an)}(?![a-z0-9])", text):
                matches.append((len(an), brand))
    return max(matches, default=(0, None))[1]


def _model_candidates(brand: str | None):
    if not brand:
        return []
    out = []
    for model, aliases in MARQUES_MODELES.get(brand, {}).items():
        # Les modèles qui ne sont que des types génériques restent utiles pour
        # les suggestions, mais pas pour une correction fuzzy agressive.
        model_n = _norm(model)
        variants = [model, *aliases]
        stripped_variants = []
        for variant in variants:
            vn = _norm(variant)
            for ba in MODEL_STRIP_ALIASES.get(brand, BRAND_ALIASES.get(brand, (brand,))):
                ban = _norm(ba)
                vn = re.sub(rf"(?<![a-z0-9]){re.escape(ban)}(?![a-z0-9])", " ", vn)
            vn = re.sub(r"\s+", " ", vn).strip()
            if vn:
                stripped_variants.append(vn)
        out.append((model, model_n, tuple(dict.fromkeys(stripped_variants))))
    return out


def _best_model(tokens: list[str], brand: str | None):
    if not brand:
        return None
    best = None
    # Retire les mots de marque afin de comparer la partie réellement modèle.
    brand_words = set()
    for alias in MODEL_STRIP_ALIASES.get(brand, BRAND_ALIASES.get(brand, (brand,))):
        brand_words.update(_tokens(alias))
    remaining = [tok for tok in tokens if tok not in brand_words]
    if not remaining:
        return None

    generic = {"sweat", "pantalon", "short", "veste", "pull", "polo", "cargo", "ensemble", "hoodie", "t", "shirt"}
    for model, model_n, variants in _model_candidates(brand):
        if model_n in generic:
            continue
        for variant in variants:
            vt = _tokens(variant)
            if not vt or len(vt) > len(remaining):
                continue
            for i in range(len(remaining) - len(vt) + 1):
                chunk = " ".join(remaining[i : i + len(vt)])
                target = " ".join(vt)
                r = _ratio(chunk, target)
                # Une fois la marque connue, on peut être un peu plus tolérant
                # sur le modèle (hybryd -> hybrid, phenon -> phenom).
                threshold = 0.82 if len(target) >= 5 else 0.90
                if chunk == target:
                    r = 1.0
                if r >= threshold and (best is None or r > best[0] or (r == best[0] and len(target) > len(best[3]))):
                    best = (r, model, chunk, target)
    return best


def _detect_type(tokens: list[str]):
    text = " ".join(tokens)
    for canonical, aliases in TYPE_ALIASES.items():
        for alias in aliases:
            an = _norm(alias)
            if re.search(rf"(?<![a-z0-9]){re.escape(an)}(?![a-z0-9])", text):
                return canonical
    return None


def _pretty(tokens: list[str], brand: str | None, model: str | None) -> str:
    text = " ".join(tokens)
    if brand:
        brand_aliases = sorted((_norm(a) for a in PRETTY_BRAND_ALIASES.get(brand, BRAND_ALIASES.get(brand, (brand,)))), key=len, reverse=True)
        for alias in brand_aliases:
            if alias and re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
                text = re.sub(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", brand, text, count=1)
                break
    if model:
        # Remplace uniquement une occurrence textuelle du modèle normalisé ; si
        # la requête utilisait un alias long, le contenu reste lisible sans
        # ajouter des mots non demandés.
        mn = _norm(model)
        text = re.sub(rf"(?<![a-z0-9]){re.escape(mn)}(?![a-z0-9])", model, text, count=1, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def understand_query(query: str) -> SearchUnderstanding:
    original = " ".join(str(query or "").split())[:120]
    if not original:
        return SearchUnderstanding(original="", canonical="", corrected=False, confidence=1.0, corrections=())

    # Les références produit (DM4652-040, etc.) ne doivent jamais être passées
    # dans un correcteur fuzzy.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,}", original) and any(ch.isdigit() for ch in original):
        return SearchUnderstanding(original, original, False, 1.0, ())

    tokens = _tokens(original)
    corrections = []
    confidence = 1.0

    tokens, compound_fixes = _expand_compound_brand_tokens(tokens)
    if compound_fixes:
        corrections.extend(compound_fixes)
        confidence = min(confidence, 0.93)

    # Corrections mot à mot sûres.
    for i, tok in enumerate(list(tokens)):
        fixed = SAFE_WORD_FIXES.get(tok)
        if fixed and fixed != tok:
            tokens[i] = fixed
            corrections.append(f"{tok} → {fixed}")
            confidence = min(confidence, 0.96)

    # Marque : fuzzy uniquement sur des fenêtres qui ressemblent déjà très
    # fortement à une marque connue.
    brand_match = _best_brand(tokens)
    brand = None
    if brand_match:
        r, brand, start, length, target = brand_match
        chunk = " ".join(tokens[start : start + length])
        if chunk != target:
            tokens = _replace_token_sequence(tokens, start, length, target)
            corrections.append(f"{chunk} → {brand}")
            confidence = min(confidence, r)
    brand = _detect_exact_brand(tokens) or brand
    # « On » est une marque très courte et donc volontairement exclue du fuzzy
    # générique. On la valide si elle est seule, ou si un modèle On connu suit.
    if not brand and tokens == ["on"]:
        brand = "On"
    elif not brand and "on" in tokens and _best_model(tokens, "On"):
        brand = "On"

    # Type : les typos ambiguës (sweet) ne sont corrigées que si une marque
    # mode reconnue apporte le contexte nécessaire.
    if brand:
        for i, tok in enumerate(list(tokens)):
            fixed = CONTEXTUAL_TYPE_TYPOS.get(tok)
            if fixed and tok != fixed:
                tokens[i] = fixed
                corrections.append(f"{tok} → {fixed}")
                confidence = min(confidence, 0.91)

    # Modèle : correction contrainte à la marque reconnue.
    model = None
    model_match = _best_model(tokens, brand)
    if model_match:
        r, model, chunk, target = model_match
        if chunk != target:
            # Remplacement sur la première séquence correspondante.
            ct = _tokens(chunk)
            for i in range(len(tokens) - len(ct) + 1):
                if tokens[i : i + len(ct)] == ct:
                    tokens = _replace_token_sequence(tokens, i, len(ct), target)
                    corrections.append(f"{chunk} → {model}")
                    confidence = min(confidence, r)
                    break

    product_type = _detect_type(tokens)
    canonical = _pretty(tokens, brand, model)
    corrected = _norm(canonical) != _norm(original)
    return SearchUnderstanding(
        original=original,
        canonical=canonical or original,
        corrected=corrected,
        confidence=round(float(confidence), 3),
        corrections=tuple(corrections),
        brand=brand,
        model=model,
        product_type=product_type,
    )


def _suggestion_catalog():
    items = []
    seen = set()
    for brand, models in MARQUES_MODELES.items():
        if brand not in seen:
            seen.add(brand)
            items.append((brand, "marque", brand, None))
        for model in models:
            label = f"{brand} {model}"
            if label not in seen:
                seen.add(label)
                items.append((label, "référence / modèle", brand, model))
    return items


_SUGGESTIONS = _suggestion_catalog()


def suggest_queries(query: str, limit: int = 8):
    limit = max(1, min(int(limit or 8), 12))
    info = understand_query(query)
    qn = _norm(info.canonical or query)
    if len(qn) < 2:
        return []

    q_tokens = _tokens(qn)
    ranked = []
    for label, kind, brand, model in _SUGGESTIONS:
        ln = _norm(label)
        score = 0.0
        if ln.startswith(qn):
            score = 120 - (len(ln) - len(qn)) * 0.08
        elif qn in ln:
            score = 105 - ln.index(qn) * 0.1
        else:
            # Important pour "nike phe" : tous les mots déjà tapés doivent
            # avoir une bonne correspondance dans le label.
            l_tokens = _tokens(ln)
            token_scores = []
            for qt in q_tokens:
                best = max((_ratio(qt, lt) for lt in l_tokens), default=0.0)
                token_scores.append(best)
            if token_scores and min(token_scores) >= 0.72:
                score = 72 + 25 * sum(token_scores) / len(token_scores)
            else:
                whole = _ratio(qn, ln)
                if whole >= 0.60:
                    score = 50 + 30 * whole
        if info.brand and brand == info.brand:
            score += 18
        if info.model and model == info.model:
            score += 25
        if score > 0:
            ranked.append((score, len(label), label, kind))

    ranked.sort(key=lambda row: (-row[0], row[1], row[2].casefold()))
    out = []
    seen = set()
    if info.corrected and info.canonical:
        seen.add(info.canonical.casefold())
        out.append({"value": info.canonical, "label": info.canonical, "kind": "correction"})
    for _score, _length, label, kind in ranked:
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"value": label, "label": label, "kind": kind})
        if len(out) >= limit:
            break
    return out[:limit]


def canonicalize_search_query(query: str) -> str:
    return understand_query(query).canonical
