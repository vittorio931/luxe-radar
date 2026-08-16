import os
import time
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

from .base import MarketplaceConnector


# ============================================================
# CONFIGURATION EBAY
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# En local, charge .env s'il existe. En production, Render injecte les
# secrets via les variables d'environnement : aucun fichier physique requis.
load_dotenv(
    dotenv_path=ENV_FILE,
    override=False,
)

EBAY_CLIENT_ID = os.getenv(
    "EBAY_CLIENT_ID",
    "",
).strip()

EBAY_CLIENT_SECRET = os.getenv(
    "EBAY_CLIENT_SECRET",
    "",
).strip()

IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    or os.environ.get("LUXE_RADAR_ENV", "").lower() == "production"
)

EBAY_MARKETPLACE_ID = "EBAY_FR"

TOKEN_URL = (
    "https://api.ebay.com/"
    "identity/v1/oauth2/token"
)

SEARCH_URL = (
    "https://api.ebay.com/"
    "buy/browse/v1/item_summary/search"
)

TOKEN_SCOPE = (
    "https://api.ebay.com/oauth/api_scope"
)


_TOKEN_CACHE = {
    "token": None,
    "expires_at": 0.0,
}


# ============================================================
# SESSION HTTP + RETRIES
# ============================================================

def creer_session_http():
    retry_budget = 1 if IS_RENDER else 3
    retry = Retry(
        total=retry_budget,
        connect=retry_budget,
        read=retry_budget,
        status=retry_budget,
        backoff_factor=0.6,

        status_forcelist=(
            429,
            500,
            502,
            503,
            504,
        ),

        allowed_methods=frozenset(
            {
                "GET",
                "POST",
            }
        ),

        respect_retry_after_header=True,
        raise_on_status=False,
        # V4.1 : une réponse 429 eBay peut demander un Retry-After de 30-60 s.
        # On plafonne l'attente à 3 s pour ne pas bloquer le pipeline Render.
        retry_after_max=3,
    )

    adapter = HTTPAdapter(
        max_retries=retry
    )

    session = requests.Session()

    session.mount(
        "https://",
        adapter,
    )

    return session


_SESSION = creer_session_http()


# ============================================================
# TYPES DE PRODUITS
# ============================================================

TYPE_ALIASES = {

    "tshirt": [
        "t shirt",
        "t-shirt",
        "tshirt",
        "tee",
    ],

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

    "polo": [
        "polo",
    ],

    "chemise": [
        "chemise",
        "button shirt",
        "dress shirt",
    ],

    "pull": [
        "pull",
        "pullover",
        "sweater",
        "jumper",
        "knit",
    ],

    "ensemble": [
        "ensemble",
        "set",
        "tracksuit",
        "track suit",
        "survetement",
        "survêtement",
        "co ord",
        "co-ord",
        "matching set",
        "two piece",
        "2 piece",
        "two piece set",
        "2 piece set",
        "2 pcs",
        "2pcs",
        "2 pieces",
        "hoodie and joggers",
        "hoodie joggers",
        "hoodie and pants",
        "hoodie sweatpants",
        "sweatshirt and joggers",
        "sweat et pantalon",
        "top and bottom",
    ],
}


TYPE_TITRE = {

    "tshirt": [
        "t shirt",
        "tshirt",
        "tee",
    ],

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

    "polo": [
        "polo",
    ],

    "chemise": [
        "chemise",
        "button shirt",
        "dress shirt",
    ],

    "pull": [
        "pull",
        "pullover",
        "sweater",
        "jumper",
        "knit",
    ],

    "ensemble": [
        "ensemble",
        "set",
        "tracksuit",
        "track suit",
        "survetement",
        "co ord",
        "matching set",
        "two piece",
        "2 piece",
        "two piece set",
        "2 piece set",
        "2 pcs",
        "2pcs",
        "2 pieces",
        "hoodie and joggers",
        "hoodie joggers",
        "hoodie and pants",
        "hoodie sweatpants",
        "sweatshirt and joggers",
        "sweat et pantalon",
        "top and bottom",
    ],
}


# Mots qui ne servent pas vraiment
# à identifier un produit précis.
MOTS_GENERIQUES = {
    "a",
    "an",
    "the",
    "le",
    "la",
    "les",
    "un",
    "une",
    "de",
    "du",
    "des",
    "pour",
    "avec",
    "et",
    "and",
}


# ============================================================
# OUTILS TEXTE
# ============================================================

def normaliser_texte(texte):
    texte = str(
        texte or ""
    ).lower().strip()

    # Retirer les accents.
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

    texte = re.sub(
        r"\b(?:essantials|essencials|essensials|essentails)\b",
        "essentials",
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

    if not texte_n:
        return False

    if not expression_n:
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
# DETECTION DU TYPE DE PRODUIT
# ============================================================

def detecter_type_recherche(
    query,
):
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


# ============================================================
# MOTS IMPORTANTS
# ============================================================

def mots_importants_recherche(
    query,
    type_recherche=None,
):
    query_n = normaliser_texte(
        query
    )

    mots_ignores = set(
        MOTS_GENERIQUES
    )

    # Si l'utilisateur recherche :
    #
    # "T shirt Nike Trail"
    #
    # on enlève les mots décrivant
    # simplement le type "T shirt".
    #
    # Il reste :
    # Nike + Trail

    if type_recherche:

        for alias in TYPE_ALIASES.get(
            type_recherche,
            [],
        ):

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


# ============================================================
# PERTINENCE
# ============================================================

def titre_correspond_recherche(
    title,
    query,
    type_recherche=None,
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

        expressions_type = (
            TYPE_TITRE.get(
                type_recherche,
                [],
            )
        )

        if expressions_type:

            type_present = any(
                contient_expression(
                    titre_n,
                    expression,
                )
                for expression
                in expressions_type
            )

            if not type_present:
                return False

    # --------------------------------------------------------
    # MOTS IMPORTANTS
    # --------------------------------------------------------

    mots_importants = (
        mots_importants_recherche(
            query,
            type_recherche=
                type_recherche,
        )
    )

    # TOUS les mots importants
    # doivent être présents.
    #
    # Nike Trail
    #
    # Nike présent + Trail présent.
    #
    # "running trail" sans Nike
    # est donc rejeté.

    if mots_importants:

        if not all(
            contient_expression(
                titre_n,
                mot,
            )
            for mot
            in mots_importants
        ):
            return False

    query_n = normaliser_texte(query)
    if "essentials" in query_n.split() and "essentials" in titre_n.split():
        indique_fog = any(
            marker in titre_n
            for marker in ("fear of god", "fog essentials", "essentials fear of god")
        )
        concurrents = (
            "adidas", "nike", "reebok", "puma", "asos design",
            "new balance", "under armour",
        )
        if not indique_fog and any(brand in titre_n for brand in concurrents):
            return False

    return True


# ============================================================
# DIAGNOSTIC EBAY
# ============================================================

def diagnostic_ebay():
    return {

        "env_file":
            str(ENV_FILE),

        "env_existe":
            ENV_FILE.exists(),

        "client_id_charge":
            bool(EBAY_CLIENT_ID),

        "client_secret_charge":
            bool(
                EBAY_CLIENT_SECRET
            ),

        "longueur_client_id":
            len(EBAY_CLIENT_ID),

        "longueur_client_secret":
            len(
                EBAY_CLIENT_SECRET
            ),

        "marketplace":
            EBAY_MARKETPLACE_ID,

        "environnement":
            "PRODUCTION",
    }


# ============================================================
# TOKEN OAUTH
# ============================================================

def vider_cache_token():
    _TOKEN_CACHE["token"] = None

    _TOKEN_CACHE[
        "expires_at"
    ] = 0.0


def obtenir_token_ebay(
    force=False,
):
    maintenant = time.time()

    if not force:

        token_cache = (
            _TOKEN_CACHE.get(
                "token"
            )
        )

        expiration = (
            _TOKEN_CACHE.get(
                "expires_at",
                0.0,
            )
        )

        # On réutilise le token
        # tant qu'il reste valide.
        if (
            token_cache
            and maintenant
            < expiration - 60
        ):
            return token_cache

    if not EBAY_CLIENT_ID:
        raise RuntimeError(
            "EBAY_CLIENT_ID absent ou vide dans les variables d'environnement"
        )

    if not EBAY_CLIENT_SECRET:
        raise RuntimeError(
            "EBAY_CLIENT_SECRET absent ou vide dans les variables d'environnement"
        )

    try:

        response = _SESSION.post(
            TOKEN_URL,

            auth=HTTPBasicAuth(
                EBAY_CLIENT_ID,
                EBAY_CLIENT_SECRET,
            ),

            headers={
                "Content-Type":
                    "application/"
                    "x-www-form-urlencoded",

                "Accept":
                    "application/json",
            },

            data={
                "grant_type":
                    "client_credentials",

                "scope":
                    TOKEN_SCOPE,
            },

            timeout=(
                3 if IS_RENDER else 5,
                8 if IS_RENDER else 15,
            ),
        )

    except requests.RequestException as e:

        raise RuntimeError(
            "Impossible de contacter "
            f"eBay OAuth : {e}"
        ) from e

    if response.status_code != 200:

        raise RuntimeError(
            "Impossible d'obtenir "
            "le token eBay : "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

    try:

        payload = (
            response.json()
        )

    except ValueError as e:

        raise RuntimeError(
            "eBay a renvoyé une "
            "réponse OAuth non JSON."
        ) from e

    token = payload.get(
        "access_token"
    )

    if not token:

        raise RuntimeError(
            "La réponse OAuth eBay "
            "ne contient aucun "
            "access_token."
        )

    try:

        expires_in = int(
            payload.get(
                "expires_in",
                7200,
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        expires_in = 7200

    _TOKEN_CACHE[
        "token"
    ] = token

    _TOKEN_CACHE[
        "expires_at"
    ] = (
        maintenant
        + expires_in
    )

    print(
        "[eBay] Token OAuth "
        "Production obtenu"
    )

    return token


# ============================================================
# PRIX
# ============================================================

def extraire_prix_eur(
    item,
):
    price = (
        item.get("price")
        or {}
    )

    if (
        price.get("currency")
        != "EUR"
    ):
        return None

    try:

        valeur = round(
            float(
                price.get(
                    "value"
                )
            ),
            2,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    if valeur < 0:
        return None

    return valeur


# ============================================================
# FRAIS DE PORT
# ============================================================

def extraire_frais_port_eur(
    item,
):
    frais = []

    for option in (
        item.get(
            "shippingOptions"
        )
        or []
    ):

        cout = (
            option.get(
                "shippingCost"
            )
            or {}
        )

        if (
            cout.get("currency")
            != "EUR"
        ):
            continue

        try:

            valeur = float(
                cout.get(
                    "value"
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if valeur >= 0:

            frais.append(
                valeur
            )

    if not frais:
        return None

    return round(
        min(frais),
        2,
    )


# ============================================================
# IMAGE
# ============================================================

def extraire_image(
    item,
):
    image = (
        item.get("image")
        or {}
    )

    if isinstance(
        image,
        dict,
    ):
        return image.get(
            "imageUrl"
        )

    return None


# ============================================================
# VENDEUR
# ============================================================

def extraire_vendeur(
    item,
):
    seller = (
        item.get("seller")
        or {}
    )

    if not isinstance(
        seller,
        dict,
    ):
        seller = {}

    return {

        "nom":
            seller.get(
                "username"
            ),

        "score":
            seller.get(
                "feedbackScore"
            ),

        "pourcentage":
            seller.get(
                "feedbackPercentage"
            ),

        "type_compte":
            seller.get(
                "sellerAccountType"
            ),
    }


# ============================================================
# LOCALISATION
# ============================================================

def extraire_localisation(
    item,
):
    localisation = (
        item.get(
            "itemLocation"
        )
        or {}
    )

    if not isinstance(
        localisation,
        dict,
    ):
        return None

    morceaux = []

    ville = localisation.get(
        "city"
    )

    pays = localisation.get(
        "country"
    )

    if ville:
        morceaux.append(
            str(ville)
        )

    if pays:
        morceaux.append(
            str(pays)
        )

    if not morceaux:
        return None

    return ", ".join(
        morceaux
    )


# ============================================================
# LIEN PROPRE
# ============================================================

def nettoyer_lien_ebay(
    url,
):
    if not url:
        return None

    try:

        parties = urlsplit(
            str(url)
        )

        # Enlève les énormes paramètres
        # ?hash=...&amdata=...
        # sans toucher à l'annonce.

        return urlunsplit(
            (
                parties.scheme,
                parties.netloc,
                parties.path,
                "",
                "",
            )
        )

    except Exception:
        return str(url)


def extraire_lien(
    item,
):
    lien = (
        item.get(
            "itemWebUrl"
        )
        or item.get(
            "itemAffiliateWebUrl"
        )
    )

    return nettoyer_lien_ebay(
        lien
    )


# ============================================================
# SCORE AFFAIRE
# ============================================================

def calculer_score_affaire(
    prix,
    prix_max,
):
    if prix is None:
        return 0

    if prix_max is None:
        return 50

    try:

        prix_max = float(
            prix_max
        )

    except (
        TypeError,
        ValueError,
    ):
        return 50

    if prix_max <= 0:
        return 50

    ratio = (
        prix
        / prix_max
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
# SCORE DE CORRESPONDANCE
# ============================================================

def calculer_score_match(
    title,
    query,
    type_recherche,
):
    score = 90

    # Type demandé trouvé.
    if type_recherche:
        score += 5

    # Bonus si toute la recherche
    # apparaît telle quelle.
    if contient_expression(
        title,
        query,
    ):
        score += 5

    return min(
        score,
        100,
    )


# ============================================================
# SCORE VENDEUR / CONFIANCE
# ============================================================

def calculer_score_confiance(
    item,
    vendeur,
):
    # IMPORTANT :
    #
    # Ce score mesure surtout
    # les informations du vendeur
    # et l'expérience eBay.
    #
    # IL NE CERTIFIE PAS
    # L'AUTHENTICITE DU PRODUIT.

    score = 55

    pourcentage = vendeur.get(
        "pourcentage"
    )

    feedback_score = vendeur.get(
        "score"
    )

    try:

        pourcentage = float(
            pourcentage
        )

    except (
        TypeError,
        ValueError,
    ):
        pourcentage = None

    try:

        feedback_score = int(
            feedback_score
        )

    except (
        TypeError,
        ValueError,
    ):
        feedback_score = None

    # Evaluation positive.
    if pourcentage is not None:

        if pourcentage >= 99.5:
            score += 20

        elif pourcentage >= 99.0:
            score += 17

        elif pourcentage >= 98.0:
            score += 12

        elif pourcentage >= 95.0:
            score += 6

        elif pourcentage < 90.0:
            score -= 15

    # Nombre d'évaluations.
    if feedback_score is not None:

        if feedback_score >= 1000:
            score += 10

        elif feedback_score >= 100:
            score += 7

        elif feedback_score >= 20:
            score += 4

        elif feedback_score < 5:
            score -= 5

    # Bonus eBay Top Rated.
    if (
        item.get(
            "topRatedBuyingExperience"
        )
        is True
    ):
        score += 5

    return max(
        25,
        min(
            score,
            95,
        ),
    )


# ============================================================
# ALERTES
# ============================================================

def construire_alertes(
    item,
    vendeur,
    prix,
    prix_max,
):
    alertes = []

    try:

        pourcentage = float(
            vendeur.get(
                "pourcentage"
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        pourcentage = None

    try:

        feedback_score = int(
            vendeur.get(
                "score"
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        feedback_score = None

    if (
        pourcentage is not None
        and pourcentage < 95
    ):

        alertes.append(
            "Evaluation vendeur "
            "inferieure a 95 %"
        )

    if (
        feedback_score is not None
        and feedback_score < 10
    ):

        alertes.append(
            "Vendeur avec peu "
            "d'evaluations"
        )

    # Un prix très inférieur au budget
    # mérite au moins une vérification.
    if (
        prix_max
        and prix is not None
    ):

        try:

            if (
                prix
                <= float(prix_max)
                * 0.25
            ):

                alertes.append(
                    "Prix tres bas par rapport "
                    "au budget maximum"
                )

        except (
            TypeError,
            ValueError,
        ):
            pass

    return alertes


# ============================================================
# CATEGORIE
# ============================================================

def determiner_categorie(
    score,
    score_confiance,
):
    if score_confiance < 45:

        return "A VERIFIER"

    if score >= 85:

        return (
            "EXCELLENTE AFFAIRE"
        )

    if score >= 70:

        return (
            "BONNE AFFAIRE"
        )

    if score >= 55:

        return "INTERESSANTE"

    return "A VERIFIER"


# ============================================================
# CONNECTEUR EBAY
# ============================================================

class EbayConnector(
    MarketplaceConnector
):
    name = "eBay"

    display_name = "eBay"

    enabled = True

    currency = "EUR"

    supports_pagination = True
    expansion_page_size = 50
    expansion_recall_cap = 150
    max_pages = 4
    empty_pages_threshold = 3
    cooldown_seconds = 0.4


    def search(
        self,
        query,
        price_max=None,
        limit=20,
        page=1,
    ):
        query = str(
            query or ""
        ).strip()

        if not query:
            return []

        # ----------------------------------------------------
        # LIMITE
        # ----------------------------------------------------

        try:

            limit = int(
                limit
            )

        except (
            TypeError,
            ValueError,
        ):

            limit = 20

        limit = max(
            1,
            min(
                limit,
                200,
            ),
        )

        # ----------------------------------------------------
        # PRIX MAX
        # ----------------------------------------------------

        try:

            prix_max_float = (
                float(price_max)
                if price_max
                is not None
                else None
            )

        except (
            TypeError,
            ValueError,
        ):

            prix_max_float = None

        if (
            prix_max_float
            is not None
            and prix_max_float <= 0
        ):
            prix_max_float = None

        # ----------------------------------------------------
        # ANALYSE DE LA RECHERCHE
        # ----------------------------------------------------

        type_recherche = (
            detecter_type_recherche(
                query
            )
        )

        mots_importants = (
            mots_importants_recherche(
                query,
                type_recherche=
                    type_recherche,
            )
        )

        # On demande plus d'annonces
        # à eBay que nécessaire,
        # car notre filtre strict
        # va ensuite en éliminer.
        # V2.9.2 : 5x + plafond 200 ralentissait fortement les requêtes
        # larges alors que le radar sait maintenant paginer à l'infini.
        # Un sur-échantillonnage x2 suffit pour alimenter le filtre strict.
        # Le plafond 200 (pageSize API max) sert au collecteur profond ;
        # le chemin live reste sous 2x et garde un pageSize de 120 au plus.
        request_limit = min(
            max(
                limit * 2,
                50,
            ),
            200,
        )

        try:
            page = max(1, min(int(page or 1), 100))
        except (TypeError, ValueError):
            page = 1
        request_offset = (page - 1) * request_limit

        query_api = query
        if type_recherche == "ensemble" and mots_importants:
            # Pour les ensembles, les vendeurs utilisent ensemble/set/tracksuit.
            # On interroge eBay avec la marque/modèle seulement puis le filtre
            # local impose le type, ce qui évite de perdre les titres anglais.
            query_api = " ".join(mots_importants)

        params = {
            "q":
                query_api,

            "limit":
                request_limit,

            "offset":
                request_offset,

            "fieldgroups":
                "EXTENDED",
        }

        # ----------------------------------------------------
        # FILTRE PRIX COTE EBAY
        # ----------------------------------------------------

        if prix_max_float is not None:

            params[
                "filter"
            ] = (
                f"price:[..{prix_max_float}],"
                "priceCurrency:EUR"
            )

        # ----------------------------------------------------
        # TOKEN
        # ----------------------------------------------------

        token = (
            obtenir_token_ebay()
        )

        headers = {

            "Authorization":
                f"Bearer {token}",

            "X-EBAY-C-MARKETPLACE-ID":
                EBAY_MARKETPLACE_ID,

            "Accept":
                "application/json",
        }

        print(
            f"[eBay] Recherche : {query} | page={page}"
        )
        if query_api != query:
            print(f"[eBay] Requête large ensemble : {query_api}")

        print(
            "[eBay] Filtre strict : "
            + (
                " + ".join(
                    mots_importants
                )
                if mots_importants
                else "type uniquement"
            )
        )

        # ----------------------------------------------------
        # APPEL BROWSE API
        # ----------------------------------------------------

        try:

            response = _SESSION.get(
                SEARCH_URL,

                headers=headers,

                params=params,

                timeout=(
                    3 if IS_RENDER else 5,
                    8 if IS_RENDER else 20,
                ),
            )

        except requests.RequestException as e:

            raise RuntimeError(
                "Impossible de contacter "
                "la Browse API eBay : "
                f"{e}"
            ) from e

        # ----------------------------------------------------
        # TOKEN EXPIRE
        # ----------------------------------------------------

        # Si jamais le token devient
        # invalide, on le renouvelle
        # automatiquement UNE fois.

        if response.status_code == 401:

            vider_cache_token()

            token = (
                obtenir_token_ebay(
                    force=True
                )
            )

            headers[
                "Authorization"
            ] = (
                f"Bearer {token}"
            )

            response = _SESSION.get(
                SEARCH_URL,

                headers=headers,

                params=params,

                timeout=(
                    3 if IS_RENDER else 5,
                    8 if IS_RENDER else 20,
                ),
            )

        # ----------------------------------------------------
        # ERREURS EBAY
        # ----------------------------------------------------

        if response.status_code != 200:

            raise RuntimeError(
                "[eBay] Erreur Browse API : "
                f"{response.status_code} "
                f"{response.text[:700]}"
            )

        try:

            data = response.json()

        except ValueError as e:

            raise RuntimeError(
                "[eBay] La Browse API "
                "a renvoye une reponse "
                "non JSON."
            ) from e

        items = (
            data.get(
                "itemSummaries"
            )
            or []
        )

        print(
            f"[eBay] {len(items)} "
            "annonces recues"
        )

        # ----------------------------------------------------
        # RESULTATS
        # ----------------------------------------------------

        resultats = []

        ids_vus = set()

        liens_vus = set()

        for item in items:

            # ------------------------------------------------
            # TITRE
            # ------------------------------------------------

            titre = str(
                item.get(
                    "title"
                )
                or ""
            ).strip()

            if not titre:
                continue

            # ------------------------------------------------
            # PERTINENCE STRICTE
            # ------------------------------------------------

            if not titre_correspond_recherche(
                titre,
                query,
                type_recherche=
                    type_recherche,
            ):
                continue

            # ------------------------------------------------
            # PRIX
            # ------------------------------------------------

            prix = (
                extraire_prix_eur(
                    item
                )
            )

            if prix is None:
                continue

            if (
                prix_max_float
                is not None
                and prix
                > prix_max_float
            ):
                continue

            # ------------------------------------------------
            # ID / ANTI-DOUBLONS
            # ------------------------------------------------

            item_id = str(
                item.get(
                    "itemId"
                )
                or item.get(
                    "legacyItemId"
                )
                or ""
            ).strip()

            if item_id:

                if item_id in ids_vus:
                    continue

                ids_vus.add(
                    item_id
                )

            # ------------------------------------------------
            # LIEN
            # ------------------------------------------------

            lien = (
                extraire_lien(
                    item
                )
            )

            if not lien:
                continue

            if lien in liens_vus:
                continue

            liens_vus.add(
                lien
            )

            # ------------------------------------------------
            # IMAGE
            # ------------------------------------------------

            image = (
                extraire_image(
                    item
                )
            )

            # ------------------------------------------------
            # VENDEUR
            # ------------------------------------------------

            vendeur = (
                extraire_vendeur(
                    item
                )
            )

            # ------------------------------------------------
            # LIVRAISON
            # ------------------------------------------------

            frais_port = (
                extraire_frais_port_eur(
                    item
                )
            )

            prix_total = prix

            if frais_port is not None:

                prix_total = round(
                    prix
                    + frais_port,
                    2,
                )

            # ------------------------------------------------
            # SCORE MATCH
            # ------------------------------------------------

            score_match = (
                calculer_score_match(
                    titre,
                    query,
                    type_recherche,
                )
            )

            # ------------------------------------------------
            # SCORE CONFIANCE VENDEUR
            # ------------------------------------------------

            score_confiance = (
                calculer_score_confiance(
                    item,
                    vendeur,
                )
            )

            # ------------------------------------------------
            # SCORE AFFAIRE
            # ------------------------------------------------

            score_affaire = (
                calculer_score_affaire(
                    prix,
                    prix_max_float,
                )
            )

            # ------------------------------------------------
            # SCORE FINAL
            # ------------------------------------------------

            score = round(

                score_match
                * 0.45

                + score_confiance
                * 0.30

                + score_affaire
                * 0.25
            )

            categorie = (
                determiner_categorie(
                    score,
                    score_confiance,
                )
            )

            # ------------------------------------------------
            # ALERTES
            # ------------------------------------------------

            alertes = (
                construire_alertes(
                    item,
                    vendeur,
                    prix,
                    prix_max_float,
                )
            )

            # ------------------------------------------------
            # RAISONS
            # ------------------------------------------------

            raisons = [

                "Annonce recuperee "
                "via l'API officielle eBay",

                "Tous les mots importants "
                "de la recherche sont "
                "presents dans le titre",

                "Prix retourne directement "
                "par eBay en EUR",
            ]

            if (
                vendeur.get(
                    "pourcentage"
                )
                is not None
            ):

                raisons.append(
                    "Evaluation positive vendeur : "
                    f"{vendeur['pourcentage']} %"
                )

            if (
                item.get(
                    "topRatedBuyingExperience"
                )
                is True
            ):

                raisons.append(
                    "Experience d'achat "
                    "Top Rated eBay"
                )

            # ------------------------------------------------
            # RESULTAT NORMALISE
            # ------------------------------------------------

            resultats.append(
                {

                    "marketplace":
                        self.name,

                    "titre":
                        titre,

                    "prix":
                        prix,

                    "prix_original":
                        prix,

                    "devise_originale":
                        "EUR",

                    "devise":
                        "EUR",

                    "frais_port":
                        frais_port,

                    "prix_total":
                        prix_total,

                    "lien":
                        lien,

                    "image":
                        image,

                    "modele":
                        None,

                    "categorie":
                        categorie,

                    "score":
                        score,

                    "score_match":
                        score_match,

                    "score_confiance":
                        score_confiance,

                    "score_affaire":
                        score_affaire,

                    "vendeur":
                        vendeur.get(
                            "nom"
                        ),

                    "vendeur_score":
                        vendeur.get(
                            "score"
                        ),

                    "vendeur_feedback":
                        vendeur.get(
                            "pourcentage"
                        ),

                    "vendeur_type":
                        vendeur.get(
                            "type_compte"
                        ),

                    "condition":
                        item.get(
                            "condition"
                        ),

                    "localisation":
                        extraire_localisation(
                            item
                        ),

                    "buying_options":
                        item.get(
                            "buyingOptions"
                        )
                        or [],

                    "description_courte":
                        item.get(
                            "shortDescription"
                        ),

                    "item_id":
                        item.get(
                            "itemId"
                        ),

                    "legacy_item_id":
                        item.get(
                            "legacyItemId"
                        ),

                    "alertes":
                        alertes,

                    "raisons":
                        raisons,
                }
            )

            if (
                len(resultats)
                >= limit
            ):
                break

        # ====================================================
        # CLASSEMENT
        # ====================================================

        resultats.sort(
            key=lambda x: (

                -x.get(
                    "score",
                    0,
                ),

                x.get(
                    "prix_total",
                    x.get(
                        "prix",
                        999999,
                    ),
                ),
            )
        )

        print(
            f"[eBay] "
            f"{len(resultats)} "
            "resultats retenus"
        )

        return resultats[:limit]
    def search_page(self, query, price_max=None, limit=20, page=1):
        return self.search(query=query, price_max=price_max, limit=limit, page=page)
