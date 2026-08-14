from __future__ import annotations

from urllib.parse import quote
import os
import re
import unicodedata

from playwright.sync_api import sync_playwright

from modeles import MARQUES_MODELES


def _is_render_runtime():
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_SERVICE_ID")
        or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    )


def _env_int(name, default, minimum=1, maximum=300):
    try:
        value = int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(value, maximum))


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
            "set",
            "tracksuit",
        }
    },
    "gilet": {
        "aliases": {
            "gilet",
            "vest",
        }
    },
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
    return nettoyer_texte(
        texte
    )


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


def _mots_importants_multi(query, type_recherche=None):
    mots = []

    for token in _tokens(
        query
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

    return any(
        _contient_expression_multi(
            titre,
            alias,
        )
        for alias in config["aliases"]
    )


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

    return False


def _titre_correspond_multi(
    titre,
    query,
    type_recherche=None,
):
    titre_n = _normaliser_multi(
        titre
    )

    if not titre_n:
        return False

    if _faux_positif_connu(
        titre_n,
        query,
    ):
        return False

    if type_recherche and not _titre_contient_type(
        titre_n,
        type_recherche,
    ):
        return False

    mots_importants = _mots_importants_multi(
        query,
        type_recherche,
    )

    # Tous les mots importants doivent être dans le titre.
    # On n'impose pas leur ordre : "Nike Dri-FIT Trail" reste valide.
    if mots_importants and not all(
        _contient_expression_multi(
            titre_n,
            mot,
        )
        for mot in mots_importants
    ):
        return False

    return True


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
        "&page=1"
    )

    annonces = []
    vus = set()

    type_recherche = _detecter_type_multi(
        query
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless
        )

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
                8000 if _is_render_runtime() else 30000,
                minimum=2500,
                maximum=30000,
            )
            vinted_settle_ms = _env_int(
                "LUXE_RADAR_VINTED_SETTLE_MS",
                700 if _is_render_runtime() else 4500,
                minimum=0,
                maximum=5000,
            )

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=vinted_navigation_timeout_ms,
            )

            if vinted_settle_ms:
                page.wait_for_timeout(vinted_settle_ms)

            liens = page.locator(
                'a[href*="/items/"]'
            )

            nombre_liens = liens.count()

            _verbose_log(f"{nombre_liens} liens analyses")

            for i in range(
                nombre_liens
            ):
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
                            timeout=800 if _is_render_runtime() else 2000
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

                # Filtre de pertinence strict sur le titre.
                if not _titre_correspond_multi(
                    titre=titre,
                    query=query,
                    type_recherche=type_recherche,
                ):
                    continue

                # Évite les annonces explicitement inutilisables.
                if trouver_termes(
                    tout_n,
                    MOTS_ANNONCE_A_IGNORER,
                ):
                    continue

                if trouver_termes(
                    titre_n,
                    MOTS_EXCLUS,
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

    if not _titre_correspond_multi(
        titre=titre,
        query=query,
        type_recherche=type_recherche,
    ):
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

    texte_annonce = " ".join(
        str(
            resultat.get(
                cle
            )
            or ""
        )
        for cle in (
            "description",
            "texte",
            "condition",
            "etat",
        )
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
    # MATCH
    # --------------------------------------------------------

    score_match = 82

    query_n = _normaliser_multi(
        query
    )

    if contient_mot(
        titre_n,
        query_n,
    ):
        score_match += 8
        raisons.append(
            "Expression recherchée présente dans le titre"
        )
    else:
        raisons.append(
            "Tous les mots importants sont présents dans le titre"
        )

    if type_recherche:
        score_match += 6
        raisons.append(
            f"Type correspondant : {type_recherche}"
        )

    modele = (
        resultat.get(
            "modele"
        )
        or trouver_modele(
            query,
            titre_n,
        )
    )

    if modele:
        score_match += 4
        resultat[
            "modele"
        ] = modele
        raisons.append(
            f"Modèle détecté : {modele}"
        )

    score_match = max(
        0,
        min(
            round(
                score_match
            ),
            100,
        ),
    )

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

    categorie_forcee = None

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
):
    from marketplaces.connectors import (
        get_available_connectors,
        get_connector,
    )

    query = str(
        marque or ""
    ).strip()

    if not query:
        return []

    limite_int = max(
        1,
        _safe_int(
            limite,
            10,
        ),
    )

    prix_max_float = _safe_float(
        prix_max
    )

    if (
        prix_max_float is None
        or prix_max_float <= 0
    ):
        return []

    if plateformes is None:
        plateformes = list(
            get_available_connectors().keys()
        )
    elif isinstance(
        plateformes,
        str,
    ):
        plateformes = [
            plateformes
        ]
    else:
        plateformes = list(
            plateformes
        )

    # Nettoie les doublons tout en gardant l'ordre demandé.
    plateformes = _dedupe(
        str(
            p
        ).strip()
        for p in plateformes
        if str(
            p
        ).strip()
    )

    # Sur le petit service Render, démarre d'abord les sources qui
    # donnent le plus souvent des résultats rapidement. Cela évite
    # qu'une source lente occupe tous les workers avant eBay/Vinted.
    if _is_render_runtime() and len(plateformes) > 1:
        priorite_prod = {
            "eBay": 0,
            "67behaviour": 1,
            "AliExpress": 2,
            "Vinted": 3,
            "ASOS": 4,
            "Grailed": 5,
            "SSENSE": 6,
        }
        plateformes = sorted(
            plateformes,
            key=lambda nom: priorite_prod.get(nom, 50),
        )

    resultats_bruts = []

    # On récupère volontairement plus de candidats que le TOP final.
    nombre_plateformes = max(len(plateformes), 1)
    limite_par_plateforme = min(
        100,
        max(50, (limite_int * 2 + nombre_plateformes - 1) // nombre_plateformes),
    )

    # --------------------------------------------------------
    # COLLECTE CONCURRENTE
    # Une source lente (ou en panne) ne doit plus bloquer les
    # autres : on interroge toutes les marketplaces en parallèle
    # et on borne le temps total du balayage.
    # --------------------------------------------------------

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _chercher_une_plateforme(plateforme):
        if plateforme == "Vinted":
            return (
                plateforme,
                rechercher_vinted(
                    marque=query,
                    prix_max=prix_max_float,
                    limite=limite_par_plateforme,
                    headless=True,
                ),
            )

        connector = get_connector(
            plateforme
        )

        if connector is None:
            print(
                "[MULTI] "
                f"Connecteur inconnu : {plateforme}"
            )
            return (
                plateforme,
                [],
            )

        if not getattr(
            connector,
            "enabled",
            True,
        ):
            print(
                "[MULTI] "
                f"{plateforme} désactivé"
            )
            return (
                plateforme,
                [],
            )

        return (
            plateforme,
            connector.search(
                query=query,
                price_max=prix_max_float,
                limit=limite_par_plateforme,
            ),
        )

    # En production Render, une source lente ne doit jamais retenir
    # la requête HTTP pendant une minute. Les connecteurs ont en plus
    # leurs propres délais courts. La valeur reste configurable.
    delai_total_secondes = _env_int(
        "LUXE_RADAR_MULTI_TIMEOUT",
        12 if _is_render_runtime() else 110,
        minimum=5,
        maximum=110,
    )
    max_workers = min(
        4,
        max(len(plateformes), 1),
    )
    if _is_render_runtime():
        print(
            "[MULTI][PROD] "
            f"budget global={delai_total_secondes}s | sources={len(plateformes)}"
        )

    executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="luxe-radar",
    )

    try:
        futurs = {
            executor.submit(
                _chercher_une_plateforme,
                plateforme,
            ): plateforme
            for plateforme in plateformes
        }

        try:
            for futur in as_completed(
                futurs,
                timeout=delai_total_secondes,
            ):
                plateforme = futurs[futur]

                try:
                    _, annonces = futur.result()
                except Exception as e:
                    # Une marketplace en panne ne doit pas casser tout le radar.
                    print(
                        "[MULTI] "
                        f"Erreur {plateforme} : {e}"
                    )
                    continue

                for annonce in (
                    annonces
                    or []
                ):
                    if not isinstance(
                        annonce,
                        dict,
                    ):
                        continue

                    copie = dict(
                        annonce
                    )

                    copie.setdefault(
                        "marketplace",
                        plateforme,
                    )

                    resultats_bruts.append(
                        copie
                    )

        except TimeoutError:
            # On abandonne les sources les plus lentes sans casser le radar.
            print(
                "[MULTI] "
                "Délai global dépassé, sources restantes annulées"
            )
            for futur in futurs:
                futur.cancel()

    finally:
        # On ne bloque pas la réponse : les threads restants se
        # terminent en arrière-plan grâce à leurs timeouts internes.
        executor.shutdown(
            wait=False
        )

    # --------------------------------------------------------
    # ANALYSE UNIVERSELLE
    # --------------------------------------------------------

    resultats = []

    for annonce in resultats_bruts:
        analyse = _analyser_resultat_multi(
            annonce,
            query=query,
            prix_max=prix_max_float,
        )

        if analyse is None:
            continue

        resultats.append(
            analyse
        )

    # --------------------------------------------------------
    # DEDUPLICATION GLOBALE
    # --------------------------------------------------------

    uniques = []
    vus = set()

    for annonce in resultats:
        cle = _cle_unique_multi(
            annonce
        )

        if cle in vus:
            continue

        vus.add(
            cle
        )

        uniques.append(
            annonce
        )

    # Pré-tri stable avant diversification.
    uniques.sort(
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
            _prix_pour_tri_multi(
                x
            ),
        )
    )

    selectionnes, compteur_plateformes = (
        _selection_diversifiee(
            uniques,
            limite_int,
            diversifie=False,
        )
    )

    print(
        "[MULTI] "
        f"{len(resultats_bruts)} résultats bruts -> "
        f"{len(uniques)} résultats classés"
    )

    print(
        "[MULTI] Répartition TOP : "
        f"{compteur_plateformes}"
    )

    return selectionnes
