import re
import time
import unicodedata
from urllib.parse import quote, urljoin

import requests
from playwright.sync_api import sync_playwright

from .base import MarketplaceConnector


BASE_URL = "https://www.67behaviour.com"

# Taux de secours utilisé uniquement si l'API de change ne répond pas.
FALLBACK_INR_EUR = 0.00908

# Le taux récupéré est gardé en mémoire pendant 6 heures.
FX_CACHE_TTL = 6 * 60 * 60

_FX_CACHE = {
    "rate": None,
    "timestamp": 0.0,
}


# ============================================================
# TYPES DE PRODUITS
# ============================================================

TYPE_MOTS = {
    "pantalon": [
        "pantalon",
        "pants",
        "trousers",
        "jogger",
        "joggers",
        "track pants",
        "cargo",
    ],

    "short": [
        "short",
        "shorts",
    ],

    "tshirt": [
        "t shirt",
        "tshirt",
        "tee",
        "jersey",
    ],

    "polo": [
        "polo",
        "polo shirt",
    ],

    "veste": [
        "veste",
        "jacket",
        "windbreaker",
        "track jacket",
    ],

    "sweat": [
        "sweat",
        "sweatshirt",
        "hoodie",
        "hooded",
    ],

    "pull": [
        "pull",
        "pullover",
        "sweater",
        "knit",
        "jumper",
    ],

    "chemise": [
        "chemise",
        "shirt",
        "button shirt",
    ],

    "chaussures": [
        "shoe",
        "shoes",
        "sneaker",
        "sneakers",
        "trainer",
        "trainers",
        "running shoe",
        "running shoes",
        "slide",
        "slides",
    ],
}


TYPE_ALIASES = {
    "pantalon": [
        "pantalon",
        "pantalons",
        "pants",
        "trousers",
        "jogger",
        "joggers",
        "cargo",
    ],

    "short": [
        "short",
        "shorts",
    ],

    "tshirt": [
        "tshirt",
        "t shirt",
        "t-shirt",
        "tee",
    ],

    "polo": [
        "polo",
    ],

    "veste": [
        "veste",
        "vestes",
        "jacket",
        "windbreaker",
    ],

    "sweat": [
        "sweat",
        "sweatshirt",
        "hoodie",
    ],

    "pull": [
        "pull",
        "pullover",
        "sweater",
        "jumper",
    ],

    "chemise": [
        "chemise",
        "shirt",
    ],

    "chaussures": [
        "chaussure",
        "chaussures",
        "basket",
        "baskets",
        "shoe",
        "shoes",
        "sneaker",
        "sneakers",
        "trainer",
        "trainers",
    ],
}


MOTS_GENERIQUES = {
    "a",
    "an",
    "the",
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
    "homme",
    "hommes",
    "femme",
    "femmes",
    "men",
    "mens",
    "women",
    "womens",
    "unisex",
    "unisexe",
    "taille",
    "size",
}


# ============================================================
# TEXTE
# ============================================================

def normaliser_texte(texte):
    texte = "" if texte is None else str(texte)
    texte = texte.lower().strip()

    texte = unicodedata.normalize(
        "NFKD",
        texte,
    )

    texte = "".join(
        caractere
        for caractere in texte
        if not unicodedata.combining(
            caractere
        )
    )

    texte = texte.replace(
        "-",
        " ",
    )

    texte = texte.replace(
        "_",
        " ",
    )

    texte = re.sub(
        r"[^a-z0-9\s]",
        " ",
        texte,
    )

    texte = re.sub(
        r"\s+",
        " ",
        texte,
    )

    return texte.strip()


def contient_expression(
    texte,
    expression,
):
    texte_n = normaliser_texte(
        texte
    )

    expression_n = normaliser_texte(
        expression
    )

    if not texte_n or not expression_n:
        return False

    pattern = (
        r"(?<![a-z0-9])"
        + re.escape(expression_n)
        + r"(?![a-z0-9])"
    )

    return (
        re.search(
            pattern,
            texte_n,
        )
        is not None
    )


# ============================================================
# TYPE DE RECHERCHE
# ============================================================

def detecter_type_recherche(query):
    query_n = normaliser_texte(
        query
    )

    for (
        type_produit,
        aliases,
    ) in TYPE_ALIASES.items():

        for alias in aliases:

            if contient_expression(
                query_n,
                alias,
            ):
                return type_produit

    return None


def mots_importants_recherche(
    query,
):
    query_n = normaliser_texte(
        query
    )

    mots_ignores = set(
        MOTS_GENERIQUES
    )

    # On enlève les mots décrivant seulement
    # le type de produit.
    for aliases in TYPE_ALIASES.values():

        for alias in aliases:

            mots_ignores.update(
                normaliser_texte(
                    alias
                ).split()
            )

    tokens = re.findall(
        r"[a-z0-9]+",
        query_n,
    )

    return [
        token
        for token in tokens
        if token not in mots_ignores
    ]


def titre_correspond_recherche(
    title,
    query,
    type_recherche,
):
    titre_n = normaliser_texte(
        title
    )

    if not titre_n:
        return False

    # --------------------------------------------------------
    # TYPE DE PRODUIT
    # --------------------------------------------------------

    if type_recherche:

        mots_type = TYPE_MOTS.get(
            type_recherche,
            [],
        )

        if not any(
            contient_expression(
                titre_n,
                mot,
            )
            for mot in mots_type
        ):
            return False

    # --------------------------------------------------------
    # MOTS IMPORTANTS
    # --------------------------------------------------------

    mots_importants = (
        mots_importants_recherche(
            query
        )
    )

    # Exemple :
    #
    # T shirt Nike trail
    #
    # devient :
    #
    # nike + trail
    #
    # Les deux doivent être présents.

    if mots_importants:

        if not all(
            contient_expression(
                titre_n,
                mot,
            )
            for mot in mots_importants
        ):
            return False

    return True


# ============================================================
# CONVERSION INR -> EUR
# ============================================================

def obtenir_taux_inr_eur():
    maintenant = time.time()

    taux_cache = _FX_CACHE[
        "rate"
    ]

    age_cache = (
        maintenant
        - _FX_CACHE["timestamp"]
    )

    if (
        taux_cache is not None
        and age_cache < FX_CACHE_TTL
    ):
        return taux_cache

    try:

        response = requests.get(
            "https://api.frankfurter.dev/v2/rate/INR/EUR",
            timeout=3,
        )

        response.raise_for_status()

        data = response.json()

        taux = float(
            data["rate"]
        )

        if taux <= 0:
            raise ValueError(
                "Taux de change invalide"
            )

        _FX_CACHE["rate"] = taux

        _FX_CACHE[
            "timestamp"
        ] = maintenant

        print(
            f"[Conversion] "
            f"Taux INR->EUR : {taux}"
        )

        return taux

    except Exception as e:

        print(
            "[Conversion] API indisponible, "
            "taux de secours utilise : "
            f"{FALLBACK_INR_EUR} "
            f"({e})"
        )

        _FX_CACHE[
            "rate"
        ] = FALLBACK_INR_EUR

        _FX_CACHE[
            "timestamp"
        ] = maintenant

        return FALLBACK_INR_EUR


def convertir_inr_vers_eur(
    prix_inr,
):
    if prix_inr is None:
        return None

    taux = obtenir_taux_inr_eur()

    try:

        return round(
            float(prix_inr)
            * taux,
            2,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# PRIX
# ============================================================

def extraire_prix_inr(
    texte,
):
    if not texte:
        return None

    motifs = [
        r"₹\s*([\d,]+(?:\.\d{1,2})?)",
        r"Rs\.?\s*([\d,]+(?:\.\d{1,2})?)",
        r"INR\s*([\d,]+(?:\.\d{1,2})?)",
    ]

    prix_trouves = []

    for motif in motifs:

        valeurs = re.findall(
            motif,
            str(texte),
            flags=re.IGNORECASE,
        )

        for valeur in valeurs:

            try:

                prix = float(
                    valeur.replace(
                        ",",
                        "",
                    )
                )

                if prix > 0:
                    prix_trouves.append(
                        prix
                    )

            except (
                TypeError,
                ValueError,
            ):
                continue

    if not prix_trouves:
        return None

    # Si le site affiche prix barré + prix promo,
    # on garde le prix le plus bas.
    return min(
        prix_trouves
    )


# ============================================================
# IMAGE
# ============================================================

def normaliser_url_image(
    valeur,
):
    if not valeur:
        return None

    valeur = str(
        valeur
    ).strip()

    # Gestion des srcset :
    # on prend la dernière image,
    # généralement la plus grande.
    if "," in valeur:

        candidats = [
            morceau.strip()
            for morceau
            in valeur.split(",")
            if morceau.strip()
        ]

        if candidats:
            valeur = (
                candidats[-1]
                .split()[0]
            )

    valeur = valeur.split()[0]

    if valeur.startswith("//"):
        valeur = (
            "https:"
            + valeur
        )

    return urljoin(
        BASE_URL,
        valeur,
    )


# ============================================================
# CARTE PRODUIT
# ============================================================

def trouver_carte_produit(
    link,
):
    # On cherche le conteneur le plus proche
    # appartenant réellement au produit.
    #
    # Cela évite d'utiliser le même prix / la
    # même image pour plusieurs produits.

    selecteurs_xpath = [
        "xpath=ancestor::li[1]",
        "xpath=ancestor::*[contains(@class,'card-wrapper')][1]",
        "xpath=ancestor::*[contains(@class,'product-card')][1]",
        "xpath=ancestor::*[contains(@class,'product')][1]",
        "xpath=ancestor::*[contains(@class,'card')][1]",
    ]

    for selecteur in selecteurs_xpath:

        try:

            candidat = link.locator(
                selecteur
            )

            if candidat.count() > 0:
                return candidat.first

        except Exception:
            continue

    return link


# ============================================================
# TITRE
# ============================================================

def extraire_titre(
    card,
    link,
):
    selecteurs = [
        ".card__heading",
        ".card-information__text",
        ".product-card__title",
        "h3",
        "h2",
    ]

    for selecteur in selecteurs:

        try:

            element = card.locator(
                selecteur
            ).first

            if element.count() > 0:

                titre = (
                    element.inner_text(
                        timeout=800
                    )
                    .strip()
                )

                if titre:

                    return " ".join(
                        titre.split()
                    )

        except Exception:
            continue

    try:

        titre = (
            link.get_attribute(
                "title"
            )
            or link.get_attribute(
                "aria-label"
            )
            or link.inner_text(
                timeout=800
            )
            or ""
        ).strip()

        titre = " ".join(
            titre.split()
        )

        if titre:
            return titre

    except Exception:
        pass

    return ""


# ============================================================
# EXTRACTION IMAGE
# ============================================================

def extraire_image(
    card,
):
    try:

        images = card.locator(
            "img"
        )

        nombre_images = (
            images.count()
        )

        if nombre_images == 0:
            return None

        # On teste les premières images de la carte.
        for i in range(
            min(
                nombre_images,
                4,
            )
        ):

            img = images.nth(
                i
            )

            valeur = (
                img.get_attribute(
                    "src"
                )
                or img.get_attribute(
                    "data-src"
                )
                or img.get_attribute(
                    "data-lazy-src"
                )
                or img.get_attribute(
                    "srcset"
                )
            )

            image = normaliser_url_image(
                valeur
            )

            if image:
                return image

    except Exception:
        pass

    return None


# ============================================================
# SCORE PRIX
# ============================================================

def calculer_score_affaire_67(
    prix_eur,
    prix_max,
):
    if prix_eur is None:
        return 0

    try:

        prix_max_float = (
            float(prix_max)
            if prix_max is not None
            else None
        )

    except (
        TypeError,
        ValueError,
    ):
        prix_max_float = None

    if (
        not prix_max_float
        or prix_max_float <= 0
    ):
        return 50

    ratio = (
        prix_eur
        / prix_max_float
    )

    if ratio <= 0.35:
        return 95

    if ratio <= 0.50:
        return 88

    if ratio <= 0.65:
        return 78

    if ratio <= 0.80:
        return 68

    if ratio <= 0.95:
        return 58

    return 45


# ============================================================
# CONNECTEUR 67BEHAVIOUR
# ============================================================

class Behaviour67Connector(
    MarketplaceConnector
):
    name = "67behaviour"
    display_name = "67behaviour"
    enabled = True
    currency = "INR"

    def search(
        self,
        query,
        price_max=None,
        limit=20,
    ):
        query = str(
            query or ""
        ).strip()

        if not query:
            return []

        try:

            limit = int(
                limit
            )

        except (
            TypeError,
            ValueError,
        ):
            limit = 20

        if limit <= 0:
            return []

        if price_max is not None:

            try:
                price_max = float(
                    price_max
                )

            except (
                TypeError,
                ValueError,
            ):
                price_max = None

        type_recherche = (
            detecter_type_recherche(
                query
            )
        )

        # Le taux est récupéré UNE SEULE FOIS
        # pour toute la recherche.
        taux_inr_eur = (
            obtenir_taux_inr_eur()
        )

        url = (
            f"{BASE_URL}/search"
            f"?q={quote(query)}"
        )

        resultats = []

        # URL déjà analysée.
        urls_vues = set()

        # Même titre + même prix déjà enregistré.
        produits_vus = set()

        with sync_playwright() as p:

            browser = (
                p.chromium.launch(
                    headless=False
                )
            )

            page = browser.new_page(
                viewport={
                    "width": 1400,
                    "height": 900,
                },
                locale="en-US",
            )

            try:

                print(
                    "[67behaviour] "
                    f"Recherche : {query}"
                )

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                page.wait_for_timeout(
                    3500
                )

                links = page.locator(
                    'a[href*="/products/"]'
                )

                nombre_liens = (
                    links.count()
                )

                print(
                    "[67behaviour] "
                    f"{nombre_liens} "
                    "liens produits trouves"
                )

                for i in range(
                    nombre_liens
                ):

                    try:

                        link = links.nth(
                            i
                        )

                        href = (
                            link.get_attribute(
                                "href"
                            )
                        )

                        if not href:
                            continue

                        href = urljoin(
                            BASE_URL,
                            href,
                        )

                        href = href.split(
                            "#",
                            1,
                        )[0]

                        href = href.split(
                            "?",
                            1,
                        )[0]

                        # -------------------------
                        # URL DEJA VUE
                        # -------------------------

                        if href in urls_vues:
                            continue

                        urls_vues.add(
                            href
                        )

                        # -------------------------
                        # CARTE PRODUIT
                        # -------------------------

                        card = (
                            trouver_carte_produit(
                                link
                            )
                        )

                        try:

                            texte_carte = (
                                card.inner_text(
                                    timeout=1200
                                )
                            )

                        except Exception:
                            texte_carte = ""

                        texte_carte = " ".join(
                            str(
                                texte_carte
                            ).split()
                        )

                        # -------------------------
                        # TITRE
                        # -------------------------

                        title = extraire_titre(
                            card,
                            link,
                        )

                        if not title:
                            continue

                        # -------------------------
                        # PERTINENCE
                        # -------------------------

                        if not titre_correspond_recherche(
                            title,
                            query,
                            type_recherche,
                        ):
                            continue

                        # -------------------------
                        # PRIX INR
                        # -------------------------

                        prix_inr = (
                            extraire_prix_inr(
                                texte_carte
                            )
                        )

                        if prix_inr is None:
                            continue

                        # -------------------------
                        # PRIX EUR
                        # -------------------------

                        prix_eur = round(
                            prix_inr
                            * taux_inr_eur,
                            2,
                        )

                        # -------------------------
                        # PRIX MAX
                        # -------------------------

                        if (
                            price_max is not None
                            and prix_eur > price_max
                        ):
                            continue

                        # -------------------------
                        # IMAGE
                        # -------------------------

                        image = (
                            extraire_image(
                                card
                            )
                        )

                        # -------------------------
                        # ANTI DOUBLONS
                        # -------------------------

                        cle_produit = (
                            normaliser_texte(
                                title
                            ),
                            round(
                                prix_eur,
                                2,
                            ),
                        )

                        if (
                            cle_produit
                            in produits_vus
                        ):
                            continue

                        produits_vus.add(
                            cle_produit
                        )

                        # -------------------------
                        # SCORES
                        # -------------------------

                        score_affaire = (
                            calculer_score_affaire_67(
                                prix_eur,
                                price_max,
                            )
                        )

                        # La recherche est maintenant
                        # filtrée strictement.
                        score_match = 95

                        # Score volontairement prudent :
                        # ce score ne certifie PAS
                        # l'authenticité du produit.
                        score_confiance = 65

                        score = round(
                            score_match
                            * 0.50
                            + score_confiance
                            * 0.20
                            + score_affaire
                            * 0.30
                        )

                        # -------------------------
                        # RESULTAT
                        # -------------------------

                        resultats.append(
                            {
                                "marketplace": self.name,

                                "titre": title,

                                "prix": prix_eur,

                                "prix_original": prix_inr,

                                "devise_originale": "INR",

                                "devise": "EUR",

                                "lien": href,

                                "image": image,

                                "modele": None,

                                "categorie": "A VERIFIER",

                                "score": score,

                                "score_match": score_match,

                                "score_confiance": score_confiance,

                                "score_affaire": score_affaire,

                                "alertes": [],

                                "raisons": [
                                    "Type de produit correspondant",
                                    "Mots importants de la recherche presents",
                                    "Prix converti INR vers EUR",
                                ],
                            }
                        )

                        if (
                            len(resultats)
                            >= limit
                        ):
                            break

                    except Exception as e:

                        print(
                            "[67behaviour] "
                            "Produit ignore : "
                            f"{e}"
                        )

                        continue

            except Exception as e:

                print(
                    "[67behaviour] "
                    f"Erreur : {e}"
                )

            finally:

                browser.close()

        # ====================================================
        # CLASSEMENT
        # ====================================================

        resultats.sort(
            key=lambda item: (
                -item.get(
                    "score",
                    0,
                ),
                item.get(
                    "prix",
                    999999,
                ),
            )
        )

        print(
            "[67behaviour] "
            f"{len(resultats)} "
            "resultats retenus"
        )

        return resultats[:limit]