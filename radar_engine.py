from __future__ import annotations

from urllib.parse import quote
import os
import re
import unicodedata
from time import perf_counter

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from modeles import MARQUES_MODELES
from marketplaces.connectors.authenticity import annotate_authenticity
from product_recognition import recognize as recognize_product
from search_understanding import canonicalize_search_query


def _is_render_runtime():
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_SERVICE_ID")
        or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        or os.environ.get("LUXE_RADAR_ENV", "").lower() == "production"
    )


def _env_int(name, default, minimum=1, maximum=300):
    try:
        value = int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(value, maximum))


_SOURCE_MAX_CAP = _env_int("LUXE_RADAR_SOURCE_MAX_CAP", 160, minimum=30, maximum=1000)


def _recall_mode_enabled():
    """V2.8.11 : couverture maximale sans fabriquer d'annonces.

    Une annonce réelle fournie par une marketplace peut rester visible même si
    l'identité produit n'est pas confirmée. Elle est alors marquée « à vérifier »
    et peut être filtrée dans l'interface.
    """
    return str(os.environ.get("LUXE_RADAR_RECALL_MODE", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _verbose_log(message):
    if os.environ.get("LUXE_RADAR_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}:
        print(message)


# ============================================================
# CONFIGURATION GENERALE
# ============================================================

# Accessoires / annonces qui ne correspondent généralement pas
# à un vêtement ou une paire recherchée.
MOTS_EXCLUS = {
    "badge",
    "patch",
    "sticker",
    "autocollant",
    "coque",
    "case",
    "accessoire",
    "ecusson",
    "écusson",
    "porte cle",
    "porte-clé",
    "porte clef",
    "lacets",
    "shoelace",
    "shoelaces",
    "boite vide",
    "boîte vide",
    "box only",
    "dust bag",
    "housse seule",
    "emballage seul",
}

# Annonces à ignorer complètement.
MOTS_ANNONCE_A_IGNORER = {
    "ne pas acheter",
    "ne pas achete",
    "n achetez pas",
    "n achete pas",
    "don't buy",
    "dont buy",
    "do not buy",
    "annonce test",
    "test annonce",
    "fake listing",
    "scam listing",
    "arnaque",
    "scam",
    "vendu",
    "sold",
}

# Signaux explicites de contrefaçon / réplique.
MOTS_VETO = {
    "replica",
    "replique",
    "réplique",
    "fake",
    "faux",
    "fausse",
    "counterfeit",
    "contrefacon",
    "contrefaçon",
    "1:1",
    "super fake",
    "aaa quality",
    "mirror quality",
    "unauthorized authentic",
}

MOTS_DOUTEUX_FORTS = set(MOTS_VETO) | {
    "ua",
}

MOTS_DOUTEUX_MOYENS = {
    "style",
    "inspire",
    "inspired",
    "inspiré",
    "dupe",
    "similaire",
    "similar",
    "inspired by",
    "comme",
}

MOTS_VETEMENT = {
    "t shirt",
    "tee shirt",
    "teeshirt",
    "tshirt",
    "tee",
    "polo",
    "pull",
    "sweat",
    "hoodie",
    "veste",
    "blouson",
    "manteau",
    "chemise",
    "pantalon",
    "jean",
    "short",
    "jogging",
    "survetement",
    "survêtement",
    "doudoune",
    "gilet",
    "knit",
    "cardigan",
    "maillot",
    "cargo",
    "chaussure",
    "chaussures",
    "basket",
    "baskets",
    "sneaker",
    "sneakers",
}

MOTS_ETAT = {
    "neuf",
    "new",
    "jamais porte",
    "jamais porté",
    "excellent etat",
    "excellent état",
    "tres bon etat",
    "très bon état",
    "bon etat",
    "bon état",
    "comme neuf",
    "very good condition",
    "new with tags",
    "nwt",
}

MOTS_TAILLE = {
    "xxs",
    "xs",
    "s",
    "m",
    "l",
    "xl",
    "xxl",
    "xxxl",
    "35",
    "36",
    "37",
    "38",
    "39",
    "40",
    "41",
    "42",
    "43",
    "44",
    "45",
    "46",
    "47",
    "48",
}


# ============================================================
# TYPES DE PRODUITS — MULTILINGUE
# ============================================================

_TYPES_RECHERCHE_MULTI = {
    "tshirt": {
        "aliases": {
            "t shirt",
            "tee shirt",
            "teeshirt",
            "tshirt",
            "tee",
            "camiseta",
            "camiseta manga corta",
        }
    },
    "pantalon": {
        "aliases": {
            "pantalon",
            "pants",
            "trousers",
            "trouser",
            "jogging",
            "jogger",
            "joggers",
            "sweatpants",
            "hose",
            "pantalones",
            "calca",
            "calcas",
            "calças",
        }
    },
    "short": {
        "aliases": {
            "short",
            "shorts",
            "bermuda",
            "pantalon corto",
            "pantalones cortos",
        }
    },
    "veste": {
        "aliases": {
            "veste",
            "jacket",
            "coat",
            "blouson",
            "manteau",
            "coupe vent",
            "windbreaker",
            "anorak",
            "chaqueta",
            "jacke",
        }
    },
    "sweat": {
        "aliases": {
            "sweat",
            "sweatshirt",
            "hoodie",
            "sweat a capuche",
            "sweat à capuche",
            "hooded",
        }
    },
    "pull": {
        "aliases": {
            "pull",
            "sweater",
            "pullover",
            "knit",
            "fleece",
            "polaire",
        }
    },
    "chaussures": {
        "aliases": {
            "chaussure",
            "chaussures",
            "basket",
            "baskets",
            "sneaker",
            "sneakers",
            "shoe",
            "shoes",
            "trainer",
            "trainers",
            "runner",
            "runners",
            "schuhe",
            "sapatilhas",
            "zapatillas",
        }
    },
    "chaussettes": {
        "aliases": {
            "chaussette",
            "chaussettes",
            "sock",
            "socks",
            "calcetines",
            "socken",
        }
    },
    "polo": {
        "aliases": {
            "polo",
        }
    },
    "chemise": {
        "aliases": {
            "chemise",
            "button shirt",
            "button down",
        }
    },
    "maillot": {
        "aliases": {
            "maillot",
            "jersey",
        }
    },
    "ensemble": {
        "aliases": {
            "ensemble",
            "ensemble complet",
            "set",
            "set complet",
            "tracksuit",
            "track suit",
            "survetement",
            "survêtement",
            "co ord",
            "co-ord",
            "coord",
            "two piece",
            "two-piece",
            "2 piece",
            "2-piece",
            "matching set",
            "jogging set",
            "sweat set",
            "hoodie set",
            "lot de deux",
            "haut et bas",
            "two piece set", "2 piece set", "2 pcs", "2pcs", "2 pieces", "2 pièces",
            "ensemble 2 pieces", "ensemble 2 pièces", "set 2 pieces", "set 2 pièces",
            "hoodie and joggers", "hoodie joggers", "hoodie and pants", "hoodie pants",
            "hoodie sweatpants", "sweatshirt and joggers", "sweatshirt joggers",
            "sweat et pantalon", "sweat pantalon", "top and bottom", "top bottom set",
        }
    },
    "gilet": {
        "aliases": {
            "gilet",
            "vest",
        }
    },
}


_PRIORITE_IDENTITE_MULTI = {
    "fort": 0,
    "possible": 1,
    "rejet": 2,
}


_PRIORITE_CATEGORIE_MULTI = {
    "EXCELLENTE AFFAIRE": 0,
    "BONNE AFFAIRE": 1,
    "INTERESSANTE": 2,
    "A VERIFIER": 3,
    "DOUTEUSE": 4,
    "A IGNORER": 5,
}


# ============================================================
# OUTILS GENERAUX
# ============================================================

def nettoyer_texte(texte):
    """Normalise un texte pour les comparaisons."""
    if texte is None:
        return ""

    texte = str(texte).lower().strip()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        c
        for c in texte
        if not unicodedata.combining(c)
    )

    texte = texte.replace("-", " ")
    texte = texte.replace("_", " ")
    texte = texte.replace("’", "'")
    texte = re.sub(r"[^\w€\s./']", " ", texte)
    texte = re.sub(r"\s+", " ", texte)

    return texte.strip()


def contient_mot(texte, mot):
    """
    Recherche un mot ou une expression avec des limites propres.
    Evite par exemple de détecter "ua" dans "chaussure".
    """
    texte_n = nettoyer_texte(texte)
    mot_n = nettoyer_texte(mot)

    if not texte_n or not mot_n:
        return False

    pattern = r"(?<!\w)" + re.escape(mot_n) + r"(?!\w)"
    return re.search(
        pattern,
        texte_n,
        flags=re.IGNORECASE,
    ) is not None


def trouver_termes(texte, dictionnaire):
    return [
        mot
        for mot in dictionnaire
        if contient_mot(texte, mot)
    ]


def _dedupe(liste):
    return list(
        dict.fromkeys(
            x
            for x in liste
            if x
        )
    )


def _safe_float(valeur, default=None):
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return default


def _safe_int(valeur, default=0):
    try:
        return int(float(valeur))
    except (TypeError, ValueError):
        return default


def _tokens(texte):
    return re.findall(
        r"[a-z0-9]+",
        nettoyer_texte(texte),
    )


# ============================================================
# PRIX
# ============================================================

def extraire_prix(texte):
    if not texte:
        return None

    texte = str(texte)

    motifs = [
        r"(\d{1,5}(?:[,.]\d{1,2})?)\s*€",
        r"€\s*(\d{1,5}(?:[,.]\d{1,2})?)",
    ]

    prix_trouves = []

    for motif in motifs:
        prix_trouves.extend(
            re.findall(
                motif,
                texte,
            )
        )

    for valeur in prix_trouves:
        try:
            prix = float(
                valeur.replace(
                    ",",
                    ".",
                )
            )
            if 0 < prix < 100000:
                return prix
        except (TypeError, ValueError):
            continue

    return None


# ============================================================
# MODELE / MARQUE
# ============================================================

def _trouver_cle_marque_catalogue(requete):
    requete_n = nettoyer_texte(requete)
    # Tolère les fautes fréquentes autour de Fear of God ESSENTIALS sans
    # rendre la recherche floue pour les autres marques.
    requete_n = re.sub(
        r"\b(?:essantials|essencials|essensials|essentails)\b",
        "essentials",
        requete_n,
    )

    # Priorité aux noms de marque les plus longs.
    cles = sorted(
        MARQUES_MODELES.keys(),
        key=lambda x: len(
            nettoyer_texte(x)
        ),
        reverse=True,
    )

    for cle in cles:
        if contient_mot(
            requete_n,
            cle,
        ):
            return cle

    # Compatibilité avec l'ancien comportement.
    if requete in MARQUES_MODELES:
        return requete

    return None


def trouver_modele(marque, texte):
    texte_n = nettoyer_texte(texte)

    cle_marque = _trouver_cle_marque_catalogue(
        marque
    )

    if cle_marque is None:
        return None

    modeles = MARQUES_MODELES.get(
        cle_marque,
        {},
    )

    candidats = []

    for modele, variantes in modeles.items():
        for variante in variantes:
            variante_n = nettoyer_texte(
                variante
            )

            if variante_n:
                candidats.append(
                    (
                        len(variante_n),
                        modele,
                        variante_n,
                    )
                )

    candidats.sort(
        reverse=True
    )

    for _, modele, variante_n in candidats:
        if contient_mot(
            texte_n,
            variante_n,
        ):
            return modele

    return None


def marque_presente(marque, texte):
    """
    Compatibilité historique : vérifie d'abord la requête complète,
    puis la marque connue dans MARQUES_MODELES si elle peut être déduite.
    """
    if contient_mot(
        texte,
        marque,
    ):
        return True

    cle = _trouver_cle_marque_catalogue(
        marque
    )

    if cle:
        return contient_mot(
            texte,
            cle,
        )

    return False


# ============================================================
# REQUETE / PERTINENCE
# ============================================================

def _normaliser_multi(texte):
    texte_n = nettoyer_texte(
        texte
    )
    # Correction volontairement limitée à la marque ESSENTIALS. Elle sert
    # aussi pour les titres de revente qui contiennent parfois la même faute.
    texte_n = re.sub(
        r"\b(?:essantials|essencials|essensials|essentails)\b",
        "essentials",
        texte_n,
    )
    return texte_n


def _contient_expression_multi(texte, expression):
    return contient_mot(
        texte,
        expression,
    )


def _detecter_type_multi(query):
    query_n = _normaliser_multi(
        query
    )

    # Les expressions longues d'abord pour éviter les ambiguïtés.
    candidats = []

    for type_nom, config in _TYPES_RECHERCHE_MULTI.items():
        for alias in config["aliases"]:
            alias_n = _normaliser_multi(
                alias
            )
            candidats.append(
                (
                    len(alias_n),
                    type_nom,
                    alias_n,
                )
            )

    candidats.sort(
        reverse=True
    )

    for _, type_nom, alias_n in candidats:
        if _contient_expression_multi(
            query_n,
            alias_n,
        ):
            return type_nom

    return None


def _alias_type_tokens():
    tokens = set()

    for config in _TYPES_RECHERCHE_MULTI.values():
        for alias in config["aliases"]:
            tokens.update(
                _tokens(
                    alias
                )
            )

    return tokens


_TOKENS_TYPES_MULTI = _alias_type_tokens()

_MOTS_RECHERCHE_IGNORES_MULTI = {
    "de",
    "du",
    "des",
    "le",
    "la",
    "les",
    "un",
    "une",
    "pour",
    "avec",
    "et",
    "the",
    "for",
    "with",
    "a",
    "an",
    "homme",
    "femme",
    "men",
    "mens",
    "man",
    "women",
    "womens",
    "woman",
    "taille",
    "size",
}


_ALIASES_MOTS_IMPORTANTS_MULTI = {
    # Les marketplaces raccourcissent souvent Fear of God ESSENTIALS en FOG.
    # On accepte ces alias uniquement comme équivalents de la marque demandée ;
    # le filtre de type continue de vérifier séparément le vêtement recherché.
    "essentials": (
        "essentials",
        "fear of god",
        "fog",
        "fog essentials",
        "essentials fear of god",
    ),
}


def _mot_important_present_multi(titre, mot, marketplace=None):
    titre_n = _normaliser_multi(titre)
    mot_n = _normaliser_multi(mot)

    aliases = list(_ALIASES_MOTS_IMPORTANTS_MULTI.get(mot_n, (mot_n,)))

    # Les titres des marketplaces à très gros catalogue raccourcissent parfois
    # ESSENTIALS. On tolère quelques variantes uniquement sur ces sources ;
    # ASOS/Vinted gardent le contrôle strict pour éviter les homonymes.
    if mot_n == "essentials" and marketplace in {"AliExpress", "DHgate"}:
        aliases.extend((
            "essential",
            "fg essentials",
            "fear god essentials",
        ))

    return any(
        _contient_expression_multi(titre_n, alias)
        for alias in _dedupe(aliases)
    )


def _mots_importants_multi(query, type_recherche=None):
    mots = []

    for token in _tokens(
        _normaliser_multi(query)
    ):
        if token in _MOTS_RECHERCHE_IGNORES_MULTI:
            continue

        # Si un type est déjà imposé séparément, on retire ses alias
        # du contrôle des mots importants.
        if type_recherche and token in _TOKENS_TYPES_MULTI:
            continue

        mots.append(
            token
        )

    return _dedupe(
        mots
    )


def _titre_contient_type(titre, type_recherche):
    if not type_recherche:
        return True

    config = _TYPES_RECHERCHE_MULTI.get(
        type_recherche
    )

    if not config:
        return True

    if type_recherche == "ensemble":
        # ``set`` est très ambigu : ASOS (et d'autres marketplaces) l'utilisent
        # aussi pour des coffrets beauté, brosses, soins, parfums, etc. Une
        # requête « ensemble Essentials » ne doit donc jamais être validée par
        # le seul mot ``set``. Les formulations explicitement vestimentaires
        # restent fortes, et un ``set`` générique doit avoir un contexte mode.
        titre_n = _normaliser_multi(titre)

        marqueurs_non_mode = (
            "skincare", "skin care", "beauty", "brush", "brushes",
            "makeup", "cosmetic", "cosmetics", "shampoo", "conditioner",
            "hair care", "haircare", "body wash", "shower", "fragrance",
            "perfume", "parfum", "cologne", "nail", "manicure", "candle",
            "gift set", "giftset", "toiletry", "toiletries", "serum",
            "cleanser", "moisturiser", "moisturizer", "cream", "lotion",
        )

        marqueurs_mode = (
            "hoodie", "sweat", "sweatshirt", "crewneck", "veste", "jacket",
            "coat", "top", "t shirt", "tshirt", "tee", "shirt", "chemise",
            "pantalon", "pants", "trouser", "trousers", "sweatpant",
            "sweatpants", "jogger", "joggers", "short", "shorts", "legging",
            "leggings", "skirt", "jupe", "dress", "robe", "pyjama", "pajama",
            "loungewear", "tracksuit", "track suit", "survetement", "survêtement",
            "co ord", "coord", "co-ord", "activewear", "sportswear",
        )

        fortes = (
            "tracksuit", "track suit", "survetement", "survêtement",
            "co ord", "coord", "co-ord", "matching set", "jogging set",
            "sweat set", "hoodie set", "hoodie and joggers", "hoodie joggers",
            "hoodie and pants", "hoodie pants", "hoodie sweatpants",
            "sweatshirt and joggers", "sweatshirt joggers", "sweat et pantalon",
            "sweat pantalon", "top and bottom", "top bottom set",
        )

        if any(_contient_expression_multi(titre_n, mot) for mot in fortes):
            return True

        hauts = (
            "hoodie", "sweat", "sweatshirt", "crewneck", "veste", "jacket",
            "top", "t shirt", "tshirt", "tee", "shirt", "chemise",
        )
        bas = (
            "pantalon", "pants", "sweatpant", "sweatpants", "jogger",
            "joggers", "short", "shorts", "legging", "leggings", "skirt", "jupe",
        )
        deux_pieces = (
            any(_contient_expression_multi(titre_n, mot) for mot in hauts)
            and any(_contient_expression_multi(titre_n, mot) for mot in bas)
        )

        if deux_pieces:
            return True

        # ``2pcs/two piece`` peut aussi décrire un coffret cosmétique. On le
        # considère comme un ensemble seulement avec un marqueur vestimentaire.
        aliases_ambigus = (
            "ensemble", "ensemble complet", "set", "set complet", "two piece",
            "two-piece", "2 piece", "2-piece", "two piece set", "2 piece set",
            "2 pcs", "2pcs", "2 pieces", "2 pièces", "lot de deux",
        )
        a_alias_ambigu = any(
            _contient_expression_multi(titre_n, alias)
            for alias in aliases_ambigus
        )
        a_contexte_mode = any(
            _contient_expression_multi(titre_n, mot)
            for mot in marqueurs_mode
        )
        a_contexte_non_mode = any(
            _contient_expression_multi(titre_n, mot)
            for mot in marqueurs_non_mode
        )

        return bool(a_alias_ambigu and a_contexte_mode and not a_contexte_non_mode)

    if any(
        _contient_expression_multi(
            titre,
            alias,
        )
        for alias in config["aliases"]
    ):
        return True

    return False


def _faux_positif_connu(titre, query):
    """
    Bloque des homonymes connus qui contiennent les bons mots mais
    ne correspondent pas réellement à l'intention de recherche.

    Exemple principal :
    "Nike Trail" ne doit pas renvoyer des produits NBA des
    Portland Trail Blazers.
    """
    titre_n = _normaliser_multi(titre)
    query_n = _normaliser_multi(query)

    if not titre_n or not query_n:
        return False

    query_tokens = set(re.findall(r"[a-z0-9]+", query_n))
    titre_tokens = re.findall(r"[a-z0-9]+", titre_n)
    titre_set = set(titre_tokens)

    cherche_trail = "trail" in query_tokens
    cherche_blazers = "blazers" in query_tokens
    cherche_portland = "portland" in query_tokens

    # Cas explicite et robuste : la séquence "trail blazers".
    # On utilise aussi une comparaison directe de chaîne pour éviter
    # qu'une variation de la fonction de recherche par mot la laisse passer.
    if (
        cherche_trail
        and not cherche_blazers
        and "trail blazers" in titre_n
    ):
        return True

    # Contexte NBA/Portland : utile pour les titres mal ponctués ou traduits.
    contexte_nba = bool(
        titre_set.intersection(
            {
                "nba",
                "portland",
                "blazers",
                "lillard",
                "basketball",
            }
        )
    )

    if (
        cherche_trail
        and not cherche_portland
        and not cherche_blazers
        and "trail" in titre_set
        and contexte_nba
        and (
            "blazers" in titre_set
            or "portland" in titre_set
            or "nba" in titre_set
        )
    ):
        return True

    # « Essentials » est une vraie ligne Fear of God, mais le mot est aussi
    # utilisé comme nom de gamme par Nike, adidas, ASOS DESIGN, Reebok, etc.
    # Quand l'utilisateur cherche la marque Essentials, on garde les titres
    # génériques « Essentials hoodie » (fréquents en seconde main) et ceux qui
    # mentionnent Fear of God, mais on écarte les autres marques explicites.
    cherche_essentials = "essentials" in query_tokens
    if cherche_essentials and "essentials" in titre_set:
        indique_fog = (
            "fear of god" in titre_n
            or "fog essentials" in titre_n
            or "essentials fear of god" in titre_n
        )
        marques_concurrentes = (
            "adidas", "nike", "reebok", "puma", "under armour",
            "asos design", "new balance", "lacoste", "fila", "champion",
            "tommy hilfiger", "calvin klein", "jack jones", "jack & jones",
            "hugo boss", "boss", "ralph lauren", "river island", "weekday",
            "abercrombie", "hollister", "ellesse", "levis", "levi's",
        )
        if not indique_fog and any(
            _contient_expression_multi(titre_n, marque)
            for marque in marques_concurrentes
        ):
            return True

    return False


def _titre_correspond_multi(
    titre,
    query,
    type_recherche=None,
    marketplace=None,
):
    """
    V2.8.4 — reconnaissance produit centrale.

    L'ancien filtre exigeait des mots exacts et se comportait très différemment
    selon les marketplaces. La décision est maintenant déléguée au moteur
    ``product_recognition`` qui pondère marque, modèle/ligne, type, descripteurs
    et conflits explicites. ``type_recherche`` reste dans la signature pour la
    compatibilité avec les anciens appels/tests.
    """
    analyse = recognize_product(
        title=titre,
        query=query,
        marketplace=marketplace,
    )
    return bool(analyse.accepted)


# ============================================================
# ANALYSE DE CONFIANCE VINTED (LEGACY + AMELIORATIONS)
# ============================================================

def calculer_confiance(
    marque,
    titre,
    texte,
    prix,
    modele,
    image,
):
    titre_n = nettoyer_texte(
        titre
    )
    texte_n = nettoyer_texte(
        texte
    )
    tout = f"{titre_n} {texte_n}".strip()

    confiance = 55
    alertes = []
    raisons = []

    if marque_presente(
        marque,
        titre_n,
    ):
        confiance += 15
        raisons.append(
            "Marque clairement présente dans le titre"
        )
    elif marque_presente(
        marque,
        texte_n,
    ):
        confiance += 7
        raisons.append(
            "Marque détectée dans l'annonce"
        )
    else:
        confiance -= 20
        alertes.append(
            "Marque non clairement identifiable"
        )

    if modele:
        confiance += 12
        raisons.append(
            f"Modèle détecté : {modele}"
        )

    mots_description = len(
        texte_n.split()
    )

    if mots_description >= 25:
        confiance += 7
        raisons.append(
            "Description suffisamment détaillée"
        )
    elif mots_description >= 10:
        confiance += 3
    elif mots_description <= 3:
        confiance -= 8
        alertes.append(
            "Description très pauvre"
        )

    if prix is not None and prix <= 8:
        confiance -= 4
        alertes.append(
            "Prix très bas : vérification recommandée"
        )

    if image:
        confiance += 5
        raisons.append(
            "Photo disponible"
        )
    else:
        confiance -= 8
        alertes.append(
            "Aucune photo détectée"
        )

    if trouver_termes(
        tout,
        MOTS_ETAT,
    ):
        confiance += 4
        raisons.append(
            "État renseigné"
        )

    forts = trouver_termes(
        tout,
        MOTS_DOUTEUX_FORTS,
    )

    moyens = trouver_termes(
        tout,
        MOTS_DOUTEUX_MOYENS,
    )

    if forts:
        malus = min(
            65,
            32 + (
                len(forts) - 1
            ) * 10,
        )
        confiance -= malus

        for mot in forts:
            alertes.append(
                f"Signal suspect fort : {mot}"
            )

    if moyens:
        confiance -= min(
            25,
            len(moyens) * 8,
        )

        for mot in moyens:
            alertes.append(
                f"Signal ambigu : {mot}"
            )

    forts_titre = trouver_termes(
        titre_n,
        MOTS_DOUTEUX_FORTS,
    )

    if forts_titre:
        confiance -= 10
        alertes.append(
            "Terme suspect présent directement dans le titre"
        )

    return (
        max(
            0,
            min(
                int(confiance),
                100,
            ),
        ),
        _dedupe(
            raisons
        ),
        _dedupe(
            alertes
        ),
    )


# ============================================================
# SCORE D'AFFAIRE
# ============================================================

def calculer_score_affaire(
    prix,
    prix_max,
):
    """
    Mesure seulement l'intérêt du prix par rapport au budget.
    Ne constitue jamais une preuve d'authenticité.
    """
    prix_float = _safe_float(
        prix
    )
    prix_max_float = _safe_float(
        prix_max
    )

    if prix_float is None:
        return 0, "Prix non disponible"

    if (
        prix_max_float is None
        or prix_max_float <= 0
    ):
        return 50, "Prix disponible"

    ratio = (
        prix_float
        / prix_max_float
    )

    if ratio <= 0.35:
        return 95, "Prix très inférieur au budget"
    if ratio <= 0.50:
        return 88, "Prix très intéressant"
    if ratio <= 0.65:
        return 80, "Très bon prix"
    if ratio <= 0.80:
        return 70, "Bon prix"
    if ratio <= 0.95:
        return 58, "Prix intéressant"

    return 45, "Proche du plafond"


# ============================================================
# ANALYSE VINTED
# ============================================================

def analyser_annonce(
    marque,
    titre,
    texte,
    prix,
    modele,
    image,
    prix_max=None,
):
    titre_n = nettoyer_texte(
        titre
    )
    texte_n = nettoyer_texte(
        texte
    )
    tout = f"{titre_n} {texte_n}".strip()

    raisons = []
    alertes = []

    ignore = trouver_termes(
        tout,
        MOTS_ANNONCE_A_IGNORER,
    )

    exclus = trouver_termes(
        titre_n,
        MOTS_EXCLUS,
    )

    veto = trouver_termes(
        tout,
        MOTS_VETO,
    )

    match = 45

    if marque_presente(
        marque,
        titre_n,
    ):
        match += 25
        raisons.append(
            "Marque présente dans le titre"
        )
    elif marque_presente(
        marque,
        texte_n,
    ):
        match += 12
        raisons.append(
            "Marque détectée dans la description"
        )
    else:
        match -= 20
        alertes.append(
            "Marque absente ou non confirmée"
        )

    if modele:
        match += 20
        raisons.append(
            f"Modèle correspondant : {modele}"
        )

    if trouver_termes(
        titre_n,
        MOTS_VETEMENT,
    ):
        match += 5
        raisons.append(
            "Type de produit identifiable"
        )

    match = max(
        0,
        min(
            match,
            100,
        ),
    )

    confiance, raisons_confiance, alertes_confiance = (
        calculer_confiance(
            marque=marque,
            titre=titre,
            texte=texte,
            prix=prix,
            modele=modele,
            image=image,
        )
    )

    raisons.extend(
        raisons_confiance
    )
    alertes.extend(
        alertes_confiance
    )

    score_affaire, raison_prix = (
        calculer_score_affaire(
            prix,
            prix_max,
        )
    )
    raisons.append(
        raison_prix
    )

    score = (
        match * 0.45
        + confiance * 0.35
        + score_affaire * 0.20
    )

    prix_float = _safe_float(
        prix
    )

    prix_extremement_bas = (
        prix_float is not None
        and prix_float <= 3
    )

    if ignore:
        score = min(
            score,
            10,
        )
        confiance = min(
            confiance,
            10,
        )
        alertes.append(
            "Annonce à ignorer"
        )

    if veto:
        score = min(
            score,
            25,
        )
        confiance = min(
            confiance,
            20,
        )
        alertes.append(
            "VETO : signal explicite de contrefaçon/réplique"
        )

    if exclus:
        score = min(
            score,
            20,
        )
        alertes.extend(
            f"Article/accessoire exclu : {mot}"
            for mot in exclus
        )

    if prix_extremement_bas and not ignore and not veto:
        score = min(
            score,
            59,
        )
        alertes.append(
            "Prix extrêmement bas : contrôle manuel nécessaire"
        )

    if confiance < 45:
        score = min(
            score,
            49,
        )

    score = max(
        0,
        min(
            round(score),
            100,
        ),
    )

    if ignore:
        categorie = "A IGNORER"
    elif veto:
        categorie = "DOUTEUSE"
    elif exclus:
        categorie = "A IGNORER"
    elif prix_extremement_bas:
        categorie = "A VERIFIER"
    elif confiance < 45:
        categorie = "DOUTEUSE"
    elif confiance < 60:
        categorie = "A VERIFIER"
    elif score >= 88 and confiance >= 85:
        categorie = "EXCELLENTE AFFAIRE"
    elif score >= 72:
        categorie = "BONNE AFFAIRE"
    elif score >= 55:
        categorie = "INTERESSANTE"
    else:
        categorie = "A VERIFIER"

    return {
        "score": score,
        "categorie": categorie,
        "raisons": _dedupe(
            raisons
        ),
        "alertes": _dedupe(
            alertes
        ),
        "score_match": match,
        "score_confiance": max(
            0,
            min(
                round(confiance),
                100,
            ),
        ),
        "score_affaire": score_affaire,
    }


# ============================================================
# RECHERCHE VINTED
# ============================================================

def rechercher_vinted(
    marque,
    prix_max,
    limite=10,
    headless=False,
    page=1,
):
    query = str(
        marque or ""
    ).strip()

    prix_max_float = _safe_float(
        prix_max
    )

    limite_int = max(
        1,
        _safe_int(
            limite,
            10,
        ),
    )

    page_int = max(1, min(_safe_int(page, 1), 100))

    if (
        not query
        or prix_max_float is None
        or prix_max_float <= 0
    ):
        return []

    url = (
        "https://www.vinted.fr/catalog"
        f"?search_text={quote(query)}"
        f"&price_to={quote(str(prix_max_float))}"
        "&currency=EUR"
        f"&page={page_int}"
    )

    annonces = []
    vus = set()

    type_recherche = _detecter_type_multi(
        query
    )

    # V2.8.10 : Vinted est une source d'enrichissement, jamais une raison de
    # bloquer le radar. Le budget est coopératif : on garde les annonces déjà
    # extraites et on sort proprement dès que le temps maximal est atteint.
    vinted_total_budget_s = _env_int(
        "LUXE_RADAR_VINTED_TOTAL_SECONDS",
        8 if _is_render_runtime() else 10,
        minimum=6,
        maximum=25,
    )
    vinted_deadline = perf_counter() + vinted_total_budget_s

    with sync_playwright() as p:
        # V2.9.x : sur le petit service Render, le démarrage de Chromium peut
        # coûter ~30 s pour 0 résultat quand Vinted refuse l'egress datacenter.
        # On borne le lancement lui-même : s'il ne démarre pas à temps, on sort
        # proprement (source vide -> cooldown vide-lent) au lieu de monopoliser
        # un worker progressif pendant toute la durée d'un échec prévisible.
        vinted_launch_ms = _env_int(
            "LUXE_RADAR_VINTED_LAUNCH_MS",
            15000 if _is_render_runtime() else 25000,
            minimum=5000,
            maximum=60000,
        )
        try:
            browser = p.chromium.launch(
                headless=headless,
                timeout=vinted_launch_ms,
            )
        except PlaywrightTimeoutError:
            print(
                f"[Vinted] Démarrage Chromium > {vinted_launch_ms}ms -> "
                "source ignorée proprement"
            )
            return []

        page = browser.new_page(
            viewport={
                "width": 1400,
                "height": 900,
            },
            locale="fr-FR",
        )

        try:
            _verbose_log(f"Recherche {query} <= {prix_max_float} EUR...")

            vinted_navigation_timeout_ms = _env_int(
                "LUXE_RADAR_VINTED_TIMEOUT_MS",
                6000 if _is_render_runtime() else 7000,
                minimum=2500,
                maximum=12000,
            )
            vinted_settle_ms = _env_int(
                "LUXE_RADAR_VINTED_SETTLE_MS",
                200 if _is_render_runtime() else 450,
                minimum=0,
                maximum=1500,
            )

            vinted_navigation_partial = False
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=vinted_navigation_timeout_ms,
                )
            except PlaywrightTimeoutError:
                # Sur le petit service Render, Vinted peut dépasser le délai
                # alors que le HTML utile a déjà commencé à arriver. On garde
                # la page partielle au lieu d'abandonner toute la source.
                vinted_navigation_partial = True
                print(
                    f"[Vinted] Navigation > {vinted_navigation_timeout_ms}ms ; "
                    "tentative de lecture partielle"
                )

            if vinted_settle_ms:
                page.wait_for_timeout(
                    min(vinted_settle_ms, 450)
                    if vinted_navigation_partial and _is_render_runtime()
                    else vinted_settle_ms
                )

            # Vinted charge une partie du catalogue au fur et à mesure du scroll.
            # Quand on demande un gros volume, on laisse le feed charger quelques
            # écrans supplémentaires avant d'analyser les cartes. Cela augmente la
            # couverture sans changer les filtres de pertinence.
            if limite_int > 60 and not _is_render_runtime():
                try:
                    max_scrolls = max(2, min(
                        _safe_int(os.environ.get("LUXE_RADAR_VINTED_SCROLLS", "2"), 2),
                        3,
                    ))
                    precedent = -1
                    stable = 0
                    for _ in range(max_scrolls):
                        if perf_counter() >= vinted_deadline:
                            break
                        courant = page.locator('a[href*="/items/"]').count()
                        if courant == precedent:
                            stable += 1
                        else:
                            stable = 0
                        if stable >= 2:
                            break
                        precedent = courant
                        page.mouse.wheel(0, 2600)
                        page.wait_for_timeout(350)
                except Exception:
                    pass

            liens = page.locator(
                'a[href*="/items/"]'
            )

            nombre_liens = liens.count()
            # Vinted est progressif : mieux vaut 40–60 annonces rapidement que
            # 100 annonces après une minute. Les filtres centraux restent les
            # mêmes, seule la quantité de DOM parcourue est bornée.
            nombre_liens = min(nombre_liens, 40 if _is_render_runtime() else 60)

            if vinted_navigation_partial and nombre_liens == 0:
                print("[Vinted] Page partielle sans carte exploitable ; source ignorée proprement")
                return []

            _verbose_log(f"{nombre_liens} liens analyses")

            for i in range(
                nombre_liens
            ):
                if perf_counter() >= vinted_deadline:
                    print(
                        f"[Vinted] Budget {vinted_total_budget_s}s atteint ; "
                        f"{len(annonces)} résultat(s) conservé(s)"
                    )
                    break

                lien = liens.nth(
                    i
                )

                try:
                    href = lien.get_attribute(
                        "href"
                    )
                except Exception:
                    continue

                if (
                    not href
                    or "/items/" not in href
                ):
                    continue

                if href.startswith(
                    "/"
                ):
                    href = (
                        "https://www.vinted.fr"
                        + href
                    )

                href = href.split(
                    "?",
                    1,
                )[0]

                if href in vus:
                    continue

                vus.add(
                    href
                )

                try:
                    bloc = lien.locator(
                        "xpath=../../.."
                    )
                    texte_annonce = (
                        bloc.inner_text(
                            timeout=350 if _is_render_runtime() else 500
                        )
                    )
                except Exception:
                    bloc = lien
                    texte_annonce = ""

                texte_annonce = " ".join(
                    str(
                        texte_annonce
                    ).split()
                )

                partie = href.split(
                    "/items/",
                    1,
                )[-1]

                titre = partie.split(
                    "?",
                    1,
                )[0]

                titre = re.sub(
                    r"^\d+-",
                    "",
                    titre,
                )

                titre = titre.replace(
                    "-",
                    " ",
                )

                titre = " ".join(
                    titre.split()
                )

                titre_n = nettoyer_texte(
                    titre
                )
                texte_n = nettoyer_texte(
                    texte_annonce
                )
                tout_n = f"{titre_n} {texte_n}"

                # Préfiltre cohérent avec le moteur central : Vinted peut
                # mettre la marque ou le modèle dans le texte de la carte plutôt
                # que dans le slug. On utilise donc titre + texte disponible.
                reconnaissance_vinted = recognize_product(
                    title=titre,
                    query=query,
                    marketplace="Vinted",
                    extra_text=texte_annonce,
                )
                # V3.7.x : une carte Vinted classée « rejet » (conflit dur ou
                # score sous le seuil) ne doit jamais entrer dans le catalogue,
                # même en mode couverture maximale. Le mode recall conserve
                # uniquement les cartes « possible ».
                if reconnaissance_vinted.level == "rejet":
                    continue

                # On conserve le garde-fou des annonces explicitement
                # inutilisables. Les accessoires/homonymes restent filtrables
                # côté interface en mode couverture maximale.
                if trouver_termes(
                    tout_n,
                    MOTS_ANNONCE_A_IGNORER,
                ):
                    continue

                if (
                    not _recall_mode_enabled()
                    and trouver_termes(
                        titre_n,
                        MOTS_EXCLUS,
                    )
                ):
                    continue

                prix = extraire_prix(
                    texte_annonce
                )

                if prix is None:
                    continue

                if prix > prix_max_float:
                    continue

                modele = trouver_modele(
                    query,
                    f"{titre_n} {texte_n}",
                )

                image = None

                try:
                    img = bloc.locator(
                        "img"
                    ).first

                    if img.count() > 0:
                        image = (
                            img.get_attribute(
                                "src"
                            )
                            or img.get_attribute(
                                "data-src"
                            )
                            or img.get_attribute(
                                "srcset"
                            )
                        )

                        if (
                            image
                            and "," in image
                            and " " in image
                        ):
                            # srcset : garde la première URL.
                            image = image.split(
                                ",",
                                1,
                            )[0].strip().split(
                                " ",
                                1,
                            )[0]
                except Exception:
                    image = None

                analyse = analyser_annonce(
                    marque=query,
                    titre=titre,
                    texte=texte_annonce,
                    prix=prix,
                    modele=modele,
                    image=image,
                    prix_max=prix_max_float,
                )

                titre_affiche = titre

                if (
                    modele
                    and nettoyer_texte(
                        modele
                    ) not in titre_n
                ):
                    titre_affiche = (
                        f"{titre} - {modele}"
                    )

                annonce = {
                    "marketplace": "Vinted",
                    "titre": titre_affiche,
                    "prix": prix,
                    "score": analyse["score"],
                    "categorie": analyse["categorie"],
                    "raisons": analyse["raisons"],
                    "alertes": analyse["alertes"],
                    "score_match": analyse["score_match"],
                    "score_confiance": analyse["score_confiance"],
                    "score_affaire": analyse["score_affaire"],
                    "lien": href,
                    "image": image,
                    "modele": modele,
                }

                annonces.append(
                    annonce
                )

                _verbose_log(
                    f"{titre} | {prix:.2f} EUR | {analyse['categorie']} | "
                    f"{analyse['score']}/100 | confiance {analyse['score_confiance']}/100"
                )

                if len(annonces) >= max(
                    limite_int * 5,
                    40,
                ):
                    break

        except Exception as e:
            print(
                f"[Vinted] Erreur : {e}"
            )

        finally:
            browser.close()

    annonces.sort(
        key=lambda x: (
            _PRIORITE_CATEGORIE_MULTI.get(
                x.get(
                    "categorie"
                ),
                99,
            ),
            -_safe_float(
                x.get(
                    "score"
                ),
                0,
            ),
            -_safe_float(
                x.get(
                    "score_confiance"
                ),
                0,
            ),
            _safe_float(
                x.get(
                    "prix"
                ),
                999999,
            ),
        )
    )

    return annonces[
        :limite_int
    ]


# ============================================================
# SCORE PRIX COMMUN MULTI-MARKETPLACES
# ============================================================

def _calculer_score_affaire_multi(
    prix_reference,
    prix_max,
):
    prix = _safe_float(
        prix_reference
    )
    plafond = _safe_float(
        prix_max
    )

    if prix is None:
        return 0

    if (
        plafond is None
        or plafond <= 0
    ):
        return 50

    ratio = (
        prix
        / plafond
    )

    if ratio <= 0.35:
        return 95
    if ratio <= 0.50:
        return 88
    if ratio <= 0.65:
        return 80
    if ratio <= 0.80:
        return 70
    if ratio <= 0.95:
        return 58

    return 45


def _score_feedback_ebay(resultat):
    """
    Retourne un petit ajustement de confiance si le connecteur eBay
    expose un pourcentage de feedback vendeur.
    """
    candidats = [
        resultat.get(
            "seller_feedback_percentage"
        ),
        resultat.get(
            "feedback_percentage"
        ),
        resultat.get(
            "vendeur_feedback"
        ),
    ]

    pourcentage = None

    for valeur in candidats:
        if valeur is None:
            continue

        if isinstance(
            valeur,
            dict,
        ):
            valeur = (
                valeur.get(
                    "percentage"
                )
                or valeur.get(
                    "feedbackPercentage"
                )
            )

        if valeur is None:
            continue

        texte = str(
            valeur
        ).replace(
            "%",
            "",
        ).replace(
            ",",
            ".",
        )

        match = re.search(
            r"\d+(?:\.\d+)?",
            texte,
        )

        if match:
            pourcentage = _safe_float(
                match.group(0)
            )
            break

    if pourcentage is None:
        return 0, None

    if pourcentage >= 99:
        return 5, "Très bon historique vendeur eBay"
    if pourcentage >= 97:
        return 2, "Bon historique vendeur eBay"
    if pourcentage < 90:
        return -15, "Historique vendeur eBay faible"
    if pourcentage < 95:
        return -8, "Historique vendeur eBay à vérifier"

    return 0, None


def _confiance_base_multi(
    resultat,
    marketplace,
):
    existante = _safe_float(
        resultat.get(
            "score_confiance"
        )
    )

    if existante is not None:
        confiance = existante
    elif marketplace == "eBay":
        confiance = 75
    elif marketplace == "Vinted":
        confiance = 70
    elif marketplace == "67behaviour":
        confiance = 60
    else:
        confiance = 65

    # Plafonds volontaires : ils évitent de transformer le score
    # en "certificat d'authenticité".
    plafonds = {
        "eBay": 95,
        "Vinted": 85,
        "67behaviour": 65,
    }

    plafond = plafonds.get(
        marketplace,
        80,
    )

    confiance = min(
        confiance,
        plafond,
    )

    return max(
        0,
        confiance,
    )


# ============================================================
# ANALYSE UNIVERSELLE
# ============================================================

def _analyser_resultat_multi(
    resultat,
    query,
    prix_max,
):
    resultat = dict(
        resultat
    )

    marketplace = str(
        resultat.get(
            "marketplace"
        )
        or "Inconnu"
    ).strip()

    titre = str(
        resultat.get(
            "titre"
        )
        or ""
    ).strip()

    if not titre:
        return None

    type_recherche = _detecter_type_multi(
        query
    )

    # V2.8.4 : on construit d'abord le texte disponible puis on fait UNE
    # reconnaissance centrale. Le score et les raisons seront réutilisés plus
    # bas au lieu de recalculer une pertinence différente selon la source.
    texte_annonce = " ".join(
        str(resultat.get(cle) or "")
        for cle in (
            "description", "texte", "condition", "etat",
            "marque", "brand", "type_produit_site", "product_type",
            "category", "categorie_site", "reference",
        )
    )
    reconnaissance = recognize_product(
        title=titre,
        query=query,
        marketplace=marketplace,
        extra_text=texte_annonce,
    )

    identite_non_confirmee = not reconnaissance.accepted
    # V3.7.x : une correspondance « rejet » ne doit JAMAIS entrer dans le
    # catalogue, même en mode couverture maximale. Le mode recall conserve
    # uniquement les cartes « possible », jamais les rejets fermes (conflit
    # dur ou score sous le seuil). Une offre faible n'a pas de place dans le
    # catalogue confirmé.
    if reconnaissance.level == "rejet":
        return None

    prix = _safe_float(
        resultat.get(
            "prix"
        )
    )

    if prix is None:
        return None

    plafond = _safe_float(
        prix_max
    )

    if (
        plafond is not None
        and plafond > 0
        and prix > plafond
    ):
        return None

    # eBay peut fournir un prix total incluant la livraison.
    prix_total = _safe_float(
        resultat.get(
            "prix_total"
        )
    )

    if (
        marketplace == "eBay"
        and prix_total is not None
        and prix_total > 0
    ):
        prix_reference = prix_total
    else:
        prix_reference = prix

    titre_n = _normaliser_multi(
        titre
    )

    tout = (
        f"{titre_n} "
        f"{_normaliser_multi(texte_annonce)}"
    ).strip()

    raisons = list(
        resultat.get(
            "raisons"
        )
        or []
    )

    alertes = list(
        resultat.get(
            "alertes"
        )
        or []
    )

    # --------------------------------------------------------
    # IDENTITE / MATCH PRODUIT
    # --------------------------------------------------------

    score_match = reconnaissance.score
    raisons.extend(reconnaissance.reasons)
    for conflit in reconnaissance.conflicts:
        alertes.append(f"Conflit identité : {conflit}")

    resultat["score_identite"] = reconnaissance.score
    resultat["niveau_identite"] = reconnaissance.level
    resultat["correspondance_verifiee"] = True
    resultat["identite_marque"] = reconnaissance.profile.brand
    resultat["identite_modele"] = reconnaissance.profile.model
    resultat["identite_type"] = reconnaissance.profile.type_name
    resultat["identite_descripteurs"] = list(reconnaissance.profile.descriptors)
    # V3.2.0 : explication courte exposable à l'interface sans publier tout le
    # diagnostic interne. Utile pour comprendre pourquoi une carte est gardée.
    resultat["explication_pertinence"] = " · ".join(list(reconnaissance.reasons)[:3])[:200]
    resultat["conflit_pertinence"] = " · ".join(list(reconnaissance.conflicts)[:2])[:200]

    modele = resultat.get("modele") or reconnaissance.profile.model or trouver_modele(query, titre_n)
    if modele:
        resultat["modele"] = modele

    # --------------------------------------------------------
    # CONFIANCE
    # --------------------------------------------------------

    score_confiance = _confiance_base_multi(
        resultat,
        marketplace,
    )

    image = resultat.get(
        "image"
    )

    if image:
        score_confiance += 2
        raisons.append(
            "Image disponible"
        )

    if trouver_termes(
        tout,
        MOTS_ETAT,
    ):
        score_confiance += 2
        raisons.append(
            "État renseigné"
        )

    if marketplace == "eBay":
        ajustement, raison_feedback = _score_feedback_ebay(
            resultat
        )

        score_confiance += ajustement

        if raison_feedback:
            if ajustement >= 0:
                raisons.append(
                    raison_feedback
                )
            else:
                alertes.append(
                    raison_feedback
                )

    # On réapplique les plafonds après les bonus.
    plafonds = {
        "eBay": 95,
        "Vinted": 85,
        "67behaviour": 65,
    }

    score_confiance = min(
        score_confiance,
        plafonds.get(
            marketplace,
            80,
        ),
    )

    if identite_non_confirmee:
        # Une correspondance rejetée par le moteur ne doit jamais être vendue
        # comme une forte recommandation juste parce que son prix est bas.
        score_confiance = min(score_confiance, 45)

    # --------------------------------------------------------
    # SIGNAUX NEGATIFS
    # --------------------------------------------------------

    termes_ignore = trouver_termes(
        tout,
        MOTS_ANNONCE_A_IGNORER,
    )

    veto = trouver_termes(
        tout,
        MOTS_VETO,
    )

    moyens = trouver_termes(
        tout,
        MOTS_DOUTEUX_MOYENS,
    )

    if moyens:
        score_confiance -= min(
            15,
            len(
                moyens
            ) * 5,
        )

        for mot in moyens:
            alertes.append(
                f"Signal ambigu : {mot}"
            )

    # Prix très bas : prudence, jamais un bonus de confiance.
    prix_extremement_bas = (
        marketplace in {
            "Vinted",
            "eBay",
        }
        and prix <= 3
    )

    prix_tres_bas = (
        marketplace in {
            "Vinted",
            "eBay",
        }
        and prix <= 8
    )

    if prix_tres_bas:
        score_confiance -= 5
        alertes.append(
            "Prix très bas : vérification recommandée"
        )

    if prix_extremement_bas:
        score_confiance -= 10
        alertes.append(
            "Prix extrêmement bas : contrôle manuel nécessaire"
        )

    if marketplace == "67behaviour":
        alertes.append(
            "Prix affiché hors éventuels frais de livraison, taxes ou import"
        )

    # --------------------------------------------------------
    # SCORE AFFAIRE
    # --------------------------------------------------------

    score_affaire = _calculer_score_affaire_multi(
        prix_reference,
        prix_max,
    )

    if prix_extremement_bas:
        # Un prix de 1-3 € ne doit pas fabriquer une "super affaire".
        score_affaire = min(
            score_affaire,
            45,
        )

    score_confiance = max(
        0,
        min(
            round(
                score_confiance
            ),
            100,
        ),
    )

    # --------------------------------------------------------
    # SCORE FINAL
    # --------------------------------------------------------

    score = (
        score_match * 0.50
        + score_confiance * 0.35
        + score_affaire * 0.15
    )

    categorie_forcee = "A VERIFIER" if identite_non_confirmee else None

    if identite_non_confirmee:
        score = min(score, 49)

    if termes_ignore:
        score = min(
            score,
            10,
        )
        score_confiance = min(
            score_confiance,
            10,
        )
        categorie_forcee = "A IGNORER"
        alertes.append(
            "Annonce à ignorer"
        )

    elif veto:
        score = min(
            score,
            25,
        )
        score_confiance = min(
            score_confiance,
            20,
        )
        categorie_forcee = "DOUTEUSE"
        alertes.append(
            "Signal explicite de contrefaçon/réplique"
        )

    elif prix_extremement_bas:
        score = min(
            score,
            59,
        )
        categorie_forcee = "A VERIFIER"

    elif score_confiance < 45:
        score = min(
            score,
            49,
        )
        categorie_forcee = "DOUTEUSE"

    score = max(
        0,
        min(
            round(
                score
            ),
            100,
        ),
    )

    # V3.7.x : couleur/sexe demandés dans la requête = léger malus de rang si
    # absents du titre. Jamais un rejet (on évite les faux négatifs) : c'est une
    # préférence d'ordre, le gate strict vit côté index/pertinence.
    try:
        from search_intent import (
            COLOR_ALIASES,
            GENDER_ALIASES,
            parse_search_intent,
        )
        _intent = parse_search_intent(query)
        if _intent.color and not any(
            contient_mot(titre, alias)
            for alias in COLOR_ALIASES.get(_intent.color, ())
        ):
            score = max(0, score - 6)
        if _intent.gender and not any(
            contient_mot(titre, alias)
            for alias in GENDER_ALIASES.get(_intent.gender, ())
        ):
            score = max(0, score - 4)
    except Exception:
        pass

    if categorie_forcee:
        categorie = categorie_forcee
    elif score_confiance < 60:
        categorie = "A VERIFIER"
    elif (
        score >= 88
        and score_confiance >= 85
    ):
        categorie = "EXCELLENTE AFFAIRE"
    elif (
        score >= 72
        and score_confiance >= 60
    ):
        categorie = "BONNE AFFAIRE"
    elif score >= 55:
        categorie = "INTERESSANTE"
    else:
        categorie = "A VERIFIER"

    # Niveau de prudence utile pour une future UI.
    if categorie in {
        "DOUTEUSE",
        "A IGNORER",
    }:
        niveau_risque = "eleve"
    elif categorie == "A VERIFIER":
        niveau_risque = "modere"
    else:
        niveau_risque = "standard"

    resultat[
        "marketplace"
    ] = marketplace

    resultat[
        "prix"
    ] = prix

    resultat[
        "score"
    ] = score

    resultat[
        "categorie"
    ] = categorie

    resultat[
        "niveau_risque"
    ] = niveau_risque

    resultat[
        "score_match"
    ] = score_match

    resultat[
        "score_confiance"
    ] = score_confiance

    resultat[
        "score_affaire"
    ] = max(
        0,
        min(
            round(
                score_affaire
            ),
            100,
        ),
    )

    resultat[
        "raisons"
    ] = _dedupe(
        raisons
    )

    resultat[
        "alertes"
    ] = _dedupe(
        alertes
    )

    # V2.8 : les signaux de contrefaçon restent visibles. On informe et on
    # laisse l'utilisateur décider via le filtre d'authenticité de l'UI.
    annotate_authenticity(resultat, marketplace=marketplace)

    return resultat


# ============================================================
# DEDUPLICATION
# ============================================================

def _cle_unique_multi(
    annonce,
):
    marketplace = str(
        annonce.get(
            "marketplace"
        )
        or ""
    ).lower()

    lien = str(
        annonce.get(
            "lien"
        )
        or ""
    ).strip()

    # Supprime les paramètres de tracking.
    if lien:
        lien = lien.split(
            "?",
            1,
        )[0]

    if lien:
        return (
            marketplace,
            "url",
            lien,
        )

    titre_n = _normaliser_multi(
        annonce.get(
            "titre"
        )
    )

    prix = _safe_float(
        annonce.get(
            "prix"
        )
    )

    if prix is not None:
        prix = round(
            prix,
            2,
        )

    return (
        marketplace,
        "fallback",
        titre_n,
        prix,
    )


# ============================================================
# CLASSEMENT DIVERSIFIE
# ============================================================

def _prix_pour_tri_multi(
    annonce,
):
    valeur = annonce.get(
        "prix_total",
        annonce.get(
            "prix",
            999999,
        ),
    )

    return _safe_float(
        valeur,
        999999,
    )


def _selection_diversifiee(
    annonces,
    limite,
    diversifie=True,
):
    selectionnes = []
    restants = list(
        annonces
    )
    compteur_plateformes = {}

    while (
        restants
        and len(
            selectionnes
        ) < limite
    ):

        def cle_diversifiee(
            annonce
        ):
            marketplace = str(
                annonce.get(
                    "marketplace"
                )
                or "Inconnu"
            )

            nombre_deja_present = (
                compteur_plateformes.get(
                    marketplace,
                    0,
                )
            )

            # Départage très léger dans le premier lot uniquement.
            # Aucun résultat n'est supprimé et le score original reste
            # dominant. Quand `diversifie` est False, le classement est
            # purement par score : les meilleurs résultats arrivent
            # d'abord, quelle que soit leur marketplace (mode recommandé
            # pour les grands volumes de résultats).
            penalite_diversite = 0
            if diversifie and len(selectionnes) < 50:
                penalite_diversite = min(
                    max(nombre_deja_present - 7, 0) * 0.05,
                    0.25,
                )

            score_original = _safe_float(
                annonce.get(
                    "score"
                ),
                0,
            )

            score_diversifie = (
                score_original
                - penalite_diversite
            )

            confiance = _safe_float(
                annonce.get(
                    "score_confiance"
                ),
                0,
            )

            return (
                # L'identité produit domine désormais réellement le classement.
                # Le pré-tri seul ne suffisait pas car min() recalculait sa
                # propre clé et pouvait remettre un match "possible" devant
                # un match "fort".
                _PRIORITE_IDENTITE_MULTI.get(
                    annonce.get("niveau_identite"),
                    1,
                ),
                -_safe_float(annonce.get("score_identite"), 0),
                _PRIORITE_CATEGORIE_MULTI.get(
                    annonce.get(
                        "categorie"
                    ),
                    99,
                ),
                -score_diversifie,
                -score_original,
                -confiance,
                _prix_pour_tri_multi(
                    annonce
                ),
            )

        meilleur = min(
            restants,
            key=cle_diversifiee,
        )

        selectionnes.append(
            meilleur
        )

        marketplace = str(
            meilleur.get(
                "marketplace"
            )
            or "Inconnu"
        )

        compteur_plateformes[
            marketplace
        ] = (
            compteur_plateformes.get(
                marketplace,
                0,
            )
            + 1
        )

        restants.remove(
            meilleur
        )

    return (
        selectionnes,
        compteur_plateformes,
    )


# ============================================================
# RECHERCHE MULTI-MARKETPLACES
# ============================================================

def rechercher_multi_marketplaces(
    marque,
    prix_max,
    plateformes=None,
    limite=160,
    delai_total_secondes=None,
    max_workers=None,
    page=1,
):
    """Recherche et classe plusieurs marketplaces.

    V2.8.6 :
    - une recherche mono-source est exécutée directement (pas de sous-executor
      qui continuerait en tâche orpheline après un timeout) ;
    - la recherche multi-source garde un budget configurable ;
    - sur Render, la concurrence reste volontairement faible pour protéger les
      512 Mo du plan gratuit ;
    - l'analyse d'identité reste commune à toutes les sources.
    """
    from connector_registry import (
        get_available_connectors,
        get_connector,
    )
    from concurrent.futures import ThreadPoolExecutor, as_completed

    query_originale = str(marque or "").strip()
    query = canonicalize_search_query(query_originale)
    if not query:
        return []
    if query != query_originale:
        print(f"[RECHERCHE] compris comme : {query}")

    limite_int = max(1, _safe_int(limite, 10))
    page_int = max(1, min(_safe_int(page, 1), 100))
    prix_max_float = _safe_float(prix_max)
    if prix_max_float is None or prix_max_float <= 0:
        return []

    if plateformes is None:
        plateformes = list(get_available_connectors().keys())
    elif isinstance(plateformes, str):
        plateformes = [plateformes]
    else:
        plateformes = list(plateformes)

    plateformes = _dedupe(
        str(p).strip() for p in plateformes if str(p).strip()
    )
    if not plateformes:
        return []

    # Les sources HTTP rapides passent d'abord si cette fonction est appelée
    # directement sur plusieurs plateformes en production. L'interface web
    # utilise en plus son propre pipeline progressif.
    if _is_render_runtime() and len(plateformes) > 1:
        priorite_prod = {
            "eBay": 0,
            "i-Run": 1,
            "Direct Running": 2,
            "Alltricks": 3,
            "Deporvillage": 4,
            "Zalando": 5,
            "ASOS": 6,
            "21RUN": 7,
            "Running Point": 8,
            "MisterRunning": 9,
            "Hardloop": 10,
            "Ekosport": 11,
            "Courir": 12,
            "Vinted": 13,
            "SSENSE": 14,
            "Cdiscount": 15,
            "Spartoo": 16,
            "Footshop": 17,
            "JD Sports": 18,
            "AliExpress": 19,
            "DHgate": 20,
            "67behaviour": 21,
            "1688": 22,
            "Grailed": 23,
        }
        plateformes = sorted(
            plateformes,
            key=lambda nom: priorite_prod.get(nom, 50),
        )

    resultats_bruts = []
    nombre_plateformes = max(len(plateformes), 1)
    if nombre_plateformes == 1:
        # V2.9.2 : le web appelle déjà chaque source séparément et sait demander
        # la page suivante. Doubler systématiquement le lot faisait analyser
        # jusqu'à 100 cartes pour n'en afficher que 25/50.
        # V3.7 : le plafond est paramétrable (LUXE_RADAR_SOURCE_MAX_CAP, défaut 160)
        # pour laisser SSENSE et ASOS remonter bien plus de résultats.
        limite_par_plateforme = min(_SOURCE_MAX_CAP, max(30, limite_int))
    else:
        limite_par_plateforme = min(
            _SOURCE_MAX_CAP,
            max(50, (limite_int * 2 + nombre_plateformes - 1) // nombre_plateformes),
        )

    def _chercher_une_plateforme(plateforme):
        if plateforme == "Vinted":
            return (
                plateforme,
                rechercher_vinted(
                    marque=query,
                    prix_max=prix_max_float,
                    limite=limite_par_plateforme,
                    headless=True,
                    page=page_int,
                ),
            )

        connector = get_connector(plateforme)
        if connector is None:
            print(f"[MULTI] Connecteur inconnu : {plateforme}")
            return plateforme, []
        if not getattr(connector, "enabled", True):
            print(f"[MULTI] {plateforme} désactivé")
            return plateforme, []

        search_method = connector.search_page if page_int > 1 and hasattr(connector, "search_page") else connector.search
        kwargs = {
            "query": query,
            "price_max": prix_max_float,
            "limit": limite_par_plateforme,
        }
        if page_int > 1 and hasattr(connector, "search_page"):
            kwargs["page"] = page_int
        return (
            plateforme,
            search_method(**kwargs),
        )

    def _ajouter_annonces(plateforme, annonces):
        for annonce in annonces or []:
            if not isinstance(annonce, dict):
                continue
            copie = dict(annonce)
            copie.setdefault("marketplace", plateforme)
            resultats_bruts.append(copie)

    # Une source progressive est déjà exécutée dans un worker borné de
    # app_web.py. Créer encore un ThreadPoolExecutor ici produisait des threads
    # orphelins impossibles à annuler. En mono-source, on appelle directement.
    if len(plateformes) == 1:
        plateforme = plateformes[0]
        debut = perf_counter()
        try:
            _, annonces = _chercher_une_plateforme(plateforme)
            print(
                "[MULTI][TEMPS] "
                f"{plateforme}: {perf_counter()-debut:.2f}s | "
                f"{len(annonces or [])} candidat(s) | page={page_int}"
            )
            _ajouter_annonces(plateforme, annonces)
        except Exception as e:
            print(f"[MULTI] Erreur {plateforme} : {e}")
    else:
        if delai_total_secondes is None:
            delai_total_secondes = _env_int(
                "LUXE_RADAR_MULTI_TIMEOUT",
                8 if _is_render_runtime() else 110,
                minimum=4,
                maximum=110,
            )
        else:
            try:
                delai_total_secondes = max(2, min(float(delai_total_secondes), 110))
            except (TypeError, ValueError):
                delai_total_secondes = 8 if _is_render_runtime() else 110

        if max_workers is None:
            max_workers = min(
                3 if _is_render_runtime() else 7,
                max(len(plateformes), 1),
            )
        else:
            max_workers = max(1, min(_safe_int(max_workers, 1), len(plateformes), 7))

        if _is_render_runtime():
            print(
                "[MULTI][PROD] "
                f"budget global={delai_total_secondes:g}s | "
                f"workers={max_workers} | sources={len(plateformes)}"
            )

        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="luxe-radar",
        )
        futurs = {}
        try:
            for plateforme in plateformes:
                futur = executor.submit(_chercher_une_plateforme, plateforme)
                futurs[futur] = (plateforme, perf_counter())

            try:
                for futur in as_completed(futurs, timeout=delai_total_secondes):
                    plateforme, debut_plateforme = futurs[futur]
                    try:
                        _, annonces = futur.result()
                    except Exception as e:
                        print(f"[MULTI] Erreur {plateforme} : {e}")
                        continue

                    print(
                        "[MULTI][TEMPS] "
                        f"{plateforme}: {perf_counter()-debut_plateforme:.2f}s | "
                        f"{len(annonces or [])} candidat(s) | page={page_int}"
                    )
                    _ajouter_annonces(plateforme, annonces)
            except TimeoutError:
                restants = [
                    plateforme
                    for futur, (plateforme, _debut) in futurs.items()
                    if not futur.done()
                ]
                print(
                    "[MULTI] Délai global dépassé ; réponse conservée avec "
                    f"les sources terminées. Restantes: {', '.join(restants) or 'aucune'}"
                )
                for futur in futurs:
                    futur.cancel()
        finally:
            # cancel_futures évite de démarrer les tâches encore en file. Les
            # tâches déjà actives sont, elles, bornées par les timeouts internes
            # de leurs connecteurs.
            executor.shutdown(wait=False, cancel_futures=True)

    # --------------------------------------------------------
    # ANALYSE UNIVERSELLE
    # --------------------------------------------------------
    resultats = []
    stats_bruts = {}
    stats_retenus = {}
    stats_prix_rejet = {}
    exemples_rejetes = {}
    diagnostics_identite = {}

    for annonce in resultats_bruts:
        marketplace_stat = str(annonce.get("marketplace") or "Inconnu")
        stats_bruts[marketplace_stat] = stats_bruts.get(marketplace_stat, 0) + 1

        # V4.1 : observabilité du goulot — séparer les rejets de prix des
        # rejets qualité/pertinence dans la ligne [MULTI][FILTRE].
        prix_annonce = _safe_float(annonce.get("prix"))
        if prix_annonce is None or (
            prix_max_float is not None
            and prix_max_float > 0
            and prix_annonce > prix_max_float
        ):
            stats_prix_rejet[marketplace_stat] = stats_prix_rejet.get(marketplace_stat, 0) + 1

        analyse = _analyser_resultat_multi(
            annonce,
            query=query,
            prix_max=prix_max_float,
        )

        if analyse is None:
            exemples = exemples_rejetes.setdefault(marketplace_stat, [])
            titre_rejete = " ".join(str(annonce.get("titre") or "").split())
            if titre_rejete and len(exemples) < 4:
                diag = recognize_product(
                    title=titre_rejete,
                    query=query,
                    marketplace=marketplace_stat,
                    extra_text=" ".join(
                        str(annonce.get(cle) or "")
                        for cle in (
                            "description", "texte", "marque", "brand",
                            "type_produit_site", "category",
                        )
                    ),
                )
                resume = f"{diag.score}/100 {diag.level} :: {titre_rejete[:130]}"
                if diag.conflicts:
                    resume += " :: " + ", ".join(diag.conflicts[:2])
                if resume not in exemples:
                    exemples.append(resume[:260])
            continue

        marketplace_retenu = str(analyse.get("marketplace") or marketplace_stat)
        identite_stats = diagnostics_identite.setdefault(
            marketplace_retenu,
            {"fort": 0, "possible": 0, "scores": []},
        )
        niveau_identite = str(analyse.get("niveau_identite") or "fort")
        if niveau_identite in identite_stats:
            identite_stats[niveau_identite] += 1
        identite_stats["scores"].append(
            _safe_float(analyse.get("score_identite"), 0) or 0
        )
        stats_retenus[marketplace_retenu] = stats_retenus.get(marketplace_retenu, 0) + 1
        resultats.append(analyse)

    for marketplace_stat in sorted(set(stats_bruts) | set(stats_retenus)):
        bruts = stats_bruts.get(marketplace_stat, 0)
        retenus = stats_retenus.get(marketplace_stat, 0)
        prix_rejet = stats_prix_rejet.get(marketplace_stat, 0)
        qualite_rejet = max(0, bruts - retenus - prix_rejet)
        print(
            "[MULTI][FILTRE] "
            f"{marketplace_stat}: {bruts} bruts -> {retenus} pertinents "
            f"(prix={prix_rejet}, qualite={qualite_rejet})"
        )
        identite_stats = diagnostics_identite.get(marketplace_stat)
        if identite_stats and identite_stats.get("scores"):
            moyenne = sum(identite_stats["scores"]) / len(identite_stats["scores"])
            print(
                f"[MULTI][IDENTITE][{marketplace_stat}] "
                f"fort={identite_stats['fort']} | possible={identite_stats['possible']} | "
                f"moyenne={moyenne:.1f}/100"
            )

        taux = (retenus / bruts) if bruts else 0
        if bruts and taux < 0.30 and exemples_rejetes.get(marketplace_stat):
            print(
                f"[MULTI][REJETS][{marketplace_stat}] "
                + " | ".join(exemples_rejetes[marketplace_stat])
            )

    # --------------------------------------------------------
    # DEDUPLICATION / CLASSEMENT
    # --------------------------------------------------------
    uniques = []
    vus = set()
    for annonce in resultats:
        cle = _cle_unique_multi(annonce)
        if cle in vus:
            continue
        vus.add(cle)
        uniques.append(annonce)

    uniques.sort(
        key=lambda x: (
            _PRIORITE_IDENTITE_MULTI.get(x.get("niveau_identite"), 1),
            -_safe_float(x.get("score_identite"), 0),
            _PRIORITE_CATEGORIE_MULTI.get(x.get("categorie"), 99),
            -_safe_float(x.get("score"), 0),
            -_safe_float(x.get("score_confiance"), 0),
            _prix_pour_tri_multi(x),
        )
    )

    selectionnes, compteur_plateformes = _selection_diversifiee(
        uniques,
        limite_int,
        diversifie=False,
    )

    print(
        "[MULTI] "
        f"{len(resultats_bruts)} résultats bruts -> "
        f"{len(uniques)} résultats classés"
    )
    print(f"[MULTI] Répartition TOP : {compteur_plateformes}")
    return selectionnes
