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
    "louis vuitton": ("louis vuitton", "lv"),
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


_GENERIC_NUMBER_PREFIXES = {
    "size", "taille", "pointure", "eu", "uk", "us", "prix", "price",
    "moins", "under", "max", "maximum", "min", "minimum",
}

def _explicit_model_anchor_mismatch(query, title):
    """Rejette les variantes numériques évidentes d'un modèle demandé.

    Exemple: `On Cloud 5` doit accepter `Cloud 5 Waterproof` mais pas
    `Cloud 6` ni `Cloud X 5`. On applique uniquement ce garde-fou aux
    requêtes courtes contenant une marque connue + un nombre, afin de ne pas
    transformer une pointure ou un prix en faux modèle.
    """
    q = _norm(query)
    t = _norm(title)
    if not q or not t or not _detected_brands(q):
        return False
    q_tokens = re.findall(r"[a-z0-9]+", q)
    t_tokens = re.findall(r"[a-z0-9]+", t)
    if len(q_tokens) > 8 or not any(tok.isdigit() for tok in q_tokens):
        return False
    compact_t = "".join(t_tokens)
    anchors = []
    for idx, tok in enumerate(q_tokens):
        if not tok.isdigit() or idx == 0:
            continue
        prev = q_tokens[idx - 1]
        if prev in _GENERIC_NUMBER_PREFIXES:
            continue
        if len(prev) < 1:
            continue
        anchors.append((prev, tok))
    if not anchors:
        return False
    for left, number in anchors:
        adjacent = any(
            t_tokens[i] == left and i + 1 < len(t_tokens) and t_tokens[i + 1] == number
            for i in range(len(t_tokens))
        )
        compact = f"{left}{number}" in compact_t
        if adjacent or compact:
            continue
        return True
    return False


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

    # V3.7.0 : une marque explicite doit réellement être présente dans le
    # titre. Ce garde-fou est volontairement placé AVANT le mode recall : il
    # empêche `River Island ... stone` d'entrer dans une recherche Stone
    # Island tout en laissant les recherches génériques fonctionner.
    # V3.8.0 : certains marchés (DHgate, 1688, etc.) publient des titres
    # génériques sans nom de marque ; le contrôle est inutile pour eux.
    _BRAND_BLIND_MARKETPLACES = {"DHgate", "1688"}
    query_brands = _detected_brands(query)
    if len(query_brands) == 1:
        requested_brand = next(iter(query_brands))
        title_brands = _detected_brands(title)
        if requested_brand not in title_brands:
            if marketplace in _BRAND_BLIND_MARKETPLACES:
                pass  # titles on these platforms never carry brand names
            else:
                return False, f"marque demandée absente ({requested_brand})"

    # V3.5.0 : même en mode couverture maximale, on retire les variantes de
    # modèle manifestement incompatibles avec une requête explicite. Cela
    # évite par exemple Cloud 6 / Cloud X 5 pour une recherche Cloud 5.
    if _explicit_model_anchor_mismatch(query, title):
        return False, "variante de modèle incompatible"

    # Nike Trail != Portland Trail Blazers. Ce garde-fou est volontairement
    # placé AVANT le mode recall : même en couverture maximale, on ne réadmet
    # jamais un faux positif Portland Trail Blazers pour une requête Trail.
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

    # V2.8.11 : en couverture maximale, on conserve le reste des vraies cartes
    # et l'interface décide ensuite du niveau de pertinence à afficher.
    if _recall_mode_enabled():
        return True, None

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
