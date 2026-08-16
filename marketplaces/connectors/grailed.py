import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from itertools import combinations
from urllib.parse import urljoin

import requests

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

from .base import MarketplaceConnector


BASE_URL = "https://www.grailed.com"
IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    or os.environ.get("LUXE_RADAR_ENV", "").lower() == "production"
)


# V3.7.x : cooldown anti-challenge / anti-blocage. Quand Grailed répond 403/429
# ou affiche un challenge headless, on cesse d'envoyer des requêtes pendant
# quelques minutes au lieu de marteler la protection. Aucun contournement :
# on retire juste temporairement Grailed des sources actives.
_COOLDOWN_SECONDS = int(os.environ.get("LUXE_RADAR_GRAILED_COOLDOWN", "600") or 600)
_COOLDOWN_UNTIL = 0.0


def _grailed_blocked():
    return time.time() < _COOLDOWN_UNTIL


def _grailed_mark_blocked(reason=""):
    global _COOLDOWN_UNTIL
    if time.time() >= _COOLDOWN_UNTIL:
        _COOLDOWN_UNTIL = time.time() + _COOLDOWN_SECONDS
        try:
            from ..source_health import registry as _source_health
            _source_health.record_blocked("Grailed", "challenge/refus observe")
        except Exception:
            pass
        print(
            f"[Grailed] Blocage détecté ({reason}) : mise en pause "
            f"{_COOLDOWN_SECONDS}s pour ne pas insister"
        )


def _recall_mode_enabled():
    return str(os.environ.get("LUXE_RADAR_RECALL_MODE", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


# Secours uniquement si l'API de change ne répond pas.
FALLBACK_USD_EUR = 0.86
FX_CACHE_TTL = 6 * 60 * 60

_FX_CACHE = {
    "rate": None,
    "timestamp": 0.0,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
}

# On reste volontairement sur les pages publiques du site.
MAX_ROUTES_VALIDES = 4
MAX_ROUTES_A_TESTER = 18
MAX_LIENS_PLAYWRIGHT_PAR_ROUTE = 220
MAX_PAGES_PRODUIT_PAR_RECHERCHE = 100


TYPE_ALIASES = {
    "pantalon": {
        "query": (
            "pantalon",
            "pantalons",
            "pants",
            "trousers",
            "jogger",
            "joggers",
            "sweatpants",
        ),
        "title": (
            "pantalon",
            "pants",
            "trousers",
            "jogger",
            "joggers",
            "sweatpants",
            "track pants",
        ),
        "grailed_suffixes": (
            "pants",
            "track-pants",
            "running-pants",
        ),
    },
    "short": {
        "query": (
            "short",
            "shorts",
        ),
        "title": (
            "short",
            "shorts",
        ),
        "grailed_suffixes": (
            "shorts",
            "running-shorts",
        ),
    },
    "tshirt": {
        "query": (
            "tshirt",
            "t shirt",
            "t-shirt",
            "tee",
            "tee shirt",
        ),
        "title": (
            "tshirt",
            "t shirt",
            "tee",
            "tee shirt",
            "jersey",
        ),
        "grailed_suffixes": (
            "t-shirt",
            "tee",
            "shirt",
        ),
    },
    "veste": {
        "query": (
            "veste",
            "jacket",
            "windbreaker",
        ),
        "title": (
            "veste",
            "jacket",
            "windbreaker",
            "shell",
            "rain jacket",
        ),
        "grailed_suffixes": (
            "jacket",
            "running-jacket",
            "windbreaker",
        ),
    },
    "sweat": {
        "query": (
            "sweat",
            "sweatshirt",
            "hoodie",
        ),
        "title": (
            "sweat",
            "sweatshirt",
            "hoodie",
            "hooded",
        ),
        "grailed_suffixes": (
            "hoodie",
            "sweatshirt",
        ),
    },
    "pull": {
        "query": (
            "pull",
            "pullover",
            "sweater",
            "jumper",
        ),
        "title": (
            "pull",
            "pullover",
            "sweater",
            "jumper",
            "knit",
        ),
        "grailed_suffixes": (
            "sweater",
            "knit",
        ),
    },
    "chaussures": {
        "query": (
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
        ),
        "title": (
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
            "footwear",
        ),
        "grailed_suffixes": (
            "shoes",
            "sneakers",
            "footwear",
        ),
    },
    "ensemble": {
        "query": (
            "ensemble", "set", "tracksuit", "track suit", "survetement",
            "survêtement", "co ord", "co-ord", "matching set",
            "two piece", "2 piece", "two piece set", "2 piece set",
            "2 pcs", "2pcs", "2 pieces", "hoodie and joggers",
            "hoodie joggers", "hoodie and pants", "hoodie sweatpants",
            "sweatshirt and joggers", "sweat et pantalon", "top and bottom",
        ),
        "title": (
            "ensemble", "set", "tracksuit", "track suit", "survetement",
            "co ord", "matching set", "two piece", "2 piece",
            "two piece set", "2 piece set", "2 pcs", "2pcs", "2 pieces",
            "hoodie and joggers", "hoodie joggers", "hoodie and pants",
            "hoodie sweatpants", "sweatshirt and joggers",
            "sweat et pantalon", "top and bottom",
        ),
        "grailed_suffixes": (
            "set", "tracksuit", "track-suit", "matching-set",
        ),
    },
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


MOTS_REJET = (
    "replica",
    "replique",
    "fake",
    "counterfeit",
    "1:1",
    "1 1",
    "do not buy",
    "ne pas acheter",
    "scam",
    "annonce test",
    "test listing",
)


# Si la requête n'impose pas un type précis, ces variantes permettent
# d'essayer quelques pages /browse publiques plus ciblées.
SUFFIXES_PUBLICS_GENERIQUES = (
    "shorts",
    "t-shirt",
    "running-jacket",
    "pants",
    "shoes",
)


def normaliser_texte(texte):
    texte = "" if texte is None else str(texte)
    texte = texte.lower().strip()

    texte = unicodedata.normalize(
        "NFKD",
        texte,
    )

    texte = "".join(
        c
        for c in texte
        if not unicodedata.combining(c)
    )

    texte = texte.replace(
        "-",
        " ",
    ).replace(
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


def slugifier(texte):
    texte_n = normaliser_texte(
        texte
    )

    if not texte_n:
        return ""

    return re.sub(
        r"\s+",
        "-",
        texte_n,
    ).strip("-")


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


def detecter_type_recherche(
    query,
):
    query_n = normaliser_texte(
        query
    )

    candidats = []

    for type_produit, data in TYPE_ALIASES.items():
        for alias in data["query"]:
            alias_n = normaliser_texte(
                alias
            )

            candidats.append(
                (
                    len(alias_n),
                    type_produit,
                    alias_n,
                )
            )

    candidats.sort(
        reverse=True
    )

    for _, type_produit, alias_n in candidats:
        if contient_expression(
            query_n,
            alias_n,
        ):
            return type_produit

    return None


def _tokens_types():
    resultat = set()

    for data in TYPE_ALIASES.values():
        for alias in data["query"]:
            resultat.update(
                normaliser_texte(
                    alias
                ).split()
            )

    return resultat


_TOKENS_TYPES = _tokens_types()


def mots_importants_recherche(
    query,
    type_recherche=None,
):
    query_n = normaliser_texte(
        query
    )

    tokens = re.findall(
        r"[a-z0-9]+",
        query_n,
    )

    resultat = []

    for token in tokens:
        if token in MOTS_GENERIQUES:
            continue

        if (
            type_recherche
            and token in _TOKENS_TYPES
        ):
            continue

        if token not in resultat:
            resultat.append(
                token
            )

    return resultat


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

    # "Nike Trail" ne doit pas renvoyer les Portland Trail Blazers.
    if (
        "trail" in mots_importants_recherche(
            query,
            type_recherche,
        )
        and "trail blazers" in titre_n
    ):
        return False

    if any(
        contient_expression(
            titre_n,
            expression,
        )
        for expression in MOTS_REJET
    ):
        return False

    if type_recherche:
        termes_type = TYPE_ALIASES.get(
            type_recherche,
            {},
        ).get(
            "title",
            (),
        )

        if (
            termes_type
            and not any(
                contient_expression(
                    titre_n,
                    mot,
                )
                for mot in termes_type
            )
        ):
            return False

    mots_importants = mots_importants_recherche(
        query,
        type_recherche,
    )

    if (
        mots_importants
        and not all(
            contient_expression(
                titre_n,
                mot,
            )
            for mot in mots_importants
        )
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


def generer_slugs_browse(
    query,
):
    """
    Génère d'abord les pages Grailed les plus ciblées.
    But : éviter de charger /browse/nike avant les pages Nike-Trail.
    """
    type_recherche = detecter_type_recherche(
        query
    )

    query_n = normaliser_texte(
        query
    )

    tous_tokens = re.findall(
        r"[a-z0-9]+",
        query_n,
    )

    importants = mots_importants_recherche(
        query,
        type_recherche,
    )

    slugs = []

    def ajouter(
        valeur,
    ):
        slug = slugifier(
            valeur
        )

        if (
            slug
            and slug not in slugs
        ):
            slugs.append(
                slug
            )

    # 1. Requête exacte.
    ajouter(
        " ".join(
            tous_tokens
        )
    )

    # 2. Requête sans type générique.
    base = "-".join(
        importants
    )

    ajouter(
        " ".join(
            importants
        )
    )

    # 3. Pages ciblées AVANT les pages génériques.
    if base:
        if type_recherche:
            suffixes = TYPE_ALIASES.get(
                type_recherche,
                {},
            ).get(
                "grailed_suffixes",
                (),
            )
        else:
            suffixes = SUFFIXES_PUBLICS_GENERIQUES

        for suffixe in suffixes:
            ajouter(
                f"{base} {suffixe}"
            )

    # 4. N-grams.
    n = len(
        importants
    )

    for taille in range(
        n - 1,
        1,
        -1,
    ):
        for debut in range(
            0,
            n - taille + 1,
        ):
            ajouter(
                " ".join(
                    importants[
                        debut : debut + taille
                    ]
                )
            )

    if n >= 3:
        for paire in combinations(
            importants,
            2,
        ):
            ajouter(
                " ".join(
                    paire
                )
            )

    # 5. Pages génériques seulement en dernier recours.
    for token in reversed(
        importants
    ):
        ajouter(
            token
        )

    return slugs[
        :MAX_ROUTES_A_TESTER
    ]


def obtenir_taux_usd_eur():
    maintenant = time.time()

    rate = _FX_CACHE[
        "rate"
    ]

    age = (
        maintenant
        - _FX_CACHE["timestamp"]
    )

    if (
        rate is not None
        and age < FX_CACHE_TTL
    ):
        return rate

    try:
        response = requests.get(
            "https://api.frankfurter.dev/v2/rate/USD/EUR",
            timeout=4,
        )

        response.raise_for_status()

        data = response.json()

        rate = float(
            data["rate"]
        )

        if rate <= 0:
            raise ValueError(
                "Taux USD/EUR invalide"
            )

        _FX_CACHE[
            "rate"
        ] = rate

        _FX_CACHE[
            "timestamp"
        ] = maintenant

        print(
            f"[Conversion] Taux USD->EUR : {rate}"
        )

        return rate

    except Exception as e:
        _FX_CACHE[
            "rate"
        ] = FALLBACK_USD_EUR

        _FX_CACHE[
            "timestamp"
        ] = maintenant

        print(
            "[Conversion] API indisponible, "
            "taux de secours USD->EUR : "
            f"{FALLBACK_USD_EUR} ({e})"
        )

        return FALLBACK_USD_EUR


def creer_session_http():
    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    return session


def nettoyer_html(
    texte,
):
    texte = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        str(
            texte or ""
        ),
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    texte = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        texte,
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    texte = re.sub(
        r"<[^>]+>",
        " ",
        texte,
    )

    texte = unescape(
        texte
    )

    texte = re.sub(
        r"\s+",
        " ",
        texte,
    )

    return texte.strip()


def extraire_meta(
    html,
    property_name,
):
    motifs = (
        (
            rf"<meta[^>]+property=[\"']"
            rf"{re.escape(property_name)}"
            rf"[\"'][^>]+content=[\"']([^\"']+)[\"']"
        ),
        (
            rf"<meta[^>]+content=[\"']([^\"']+)[\"']"
            rf"[^>]+property=[\"']"
            rf"{re.escape(property_name)}[\"']"
        ),
        (
            rf"<meta[^>]+name=[\"']"
            rf"{re.escape(property_name)}"
            rf"[\"'][^>]+content=[\"']([^\"']+)[\"']"
        ),
    )

    for motif in motifs:
        match = re.search(
            motif,
            html,
            flags=re.IGNORECASE,
        )

        if match:
            return unescape(
                match.group(
                    1
                )
            ).strip()

    return None


def extraire_json_ld(
    html,
):
    resultats = []

    blocs = re.findall(
        (
            r"<script[^>]+type=[\"']"
            r"application/ld\+json[\"'][^>]*>"
            r"(.*?)</script>"
        ),
        html,
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    for bloc in blocs:
        try:
            data = json.loads(
                unescape(
                    bloc
                ).strip()
            )
        except Exception:
            continue

        if isinstance(
            data,
            list,
        ):
            resultats.extend(
                data
            )
        else:
            resultats.append(
                data
            )

    return resultats


def trouver_product_jsonld(
    html,
):
    for data in extraire_json_ld(
        html
    ):
        candidats = []

        if isinstance(
            data,
            dict,
        ):
            candidats.append(
                data
            )

            graph = data.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):
                candidats.extend(
                    item
                    for item in graph
                    if isinstance(
                        item,
                        dict,
                    )
                )

        for candidat in candidats:
            type_ = candidat.get(
                "@type"
            )

            if isinstance(
                type_,
                list,
            ):
                est_produit = (
                    "Product" in type_
                )
            else:
                est_produit = (
                    type_ == "Product"
                )

            if est_produit:
                return candidat

    return {}


def _offres_jsonld(
    product,
):
    if not isinstance(
        product,
        dict,
    ):
        return {}

    offers = product.get(
        "offers"
    )

    if isinstance(
        offers,
        list,
    ):
        offers = (
            offers[0]
            if offers
            else {}
        )

    if not isinstance(
        offers,
        dict,
    ):
        return {}

    return offers


def extraire_prix_usd(
    html,
    product=None,
):
    product = (
        product
        if isinstance(
            product,
            dict,
        )
        else {}
    )

    offers = _offres_jsonld(
        product
    )

    devise = str(
        offers.get(
            "priceCurrency"
        )
        or ""
    ).upper()

    valeur = offers.get(
        "price"
    )

    # Priorité à schema.org Product/Offer.
    if (
        valeur is not None
        and (
            not devise
            or devise == "USD"
        )
    ):
        try:
            prix = float(
                str(
                    valeur
                )
                .replace(
                    ",",
                    "",
                )
                .strip()
            )

            if prix > 0:
                return prix
        except Exception:
            pass

    # Ensuite les données structurées embarquées.
    motifs_json = (
        r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?',
        r'"priceCents"\s*:\s*([0-9]+)',
    )

    for index, motif in enumerate(
        motifs_json
    ):
        valeurs = re.findall(
            motif,
            html,
            flags=re.IGNORECASE,
        )

        for valeur in valeurs[
            :30
        ]:
            try:
                prix = float(
                    valeur
                )

                if index == 1:
                    prix = (
                        prix / 100.0
                    )

                if 1 <= prix <= 100000:
                    return prix
            except Exception:
                continue

    # Dernier secours : prix visible. On évite de prendre le shipping
    # en exigeant que le symbole $ ne soit pas précédé de "+".
    texte = nettoyer_html(
        html
    )

    match = re.search(
        (
            r"(?<!\+)\$\s*"
            r"([0-9]+(?:\.[0-9]{1,2})?)"
        ),
        texte,
    )

    if match:
        try:
            prix = float(
                match.group(
                    1
                )
            )

            if prix > 0:
                return prix
        except Exception:
            pass

    return None


def extraire_shipping_usd(
    html,
):
    texte = nettoyer_html(
        html
    )

    match = re.search(
        (
            r"\+\s*\$\s*"
            r"([0-9]+(?:\.[0-9]{1,2})?)"
            r"\s*Shipping"
        ),
        texte,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    try:
        valeur = float(
            match.group(
                1
            )
        )

        if valeur >= 0:
            return valeur
    except Exception:
        pass

    return None


def listing_est_vendu(
    html,
    product=None,
):
    offers = _offres_jsonld(
        product
    )

    availability = str(
        offers.get(
            "availability"
        )
        or ""
    ).lower()

    if any(
        marqueur in availability
        for marqueur in (
            "soldout",
            "outofstock",
            "discontinued",
        )
    ):
        return True

    texte = nettoyer_html(
        html
    )

    return bool(
        re.search(
            r"\b(?:listing sold|this listing is sold)\b",
            texte,
            flags=re.IGNORECASE,
        )
    )


def extraire_infos_listing(
    html,
    url,
):
    product = trouver_product_jsonld(
        html
    )

    title = None

    if isinstance(
        product,
        dict,
    ):
        title = product.get(
            "name"
        )

    title = (
        title
        or extraire_meta(
            html,
            "og:title",
        )
        or ""
    )

    title = re.sub(
        r"\s*\|\s*Grailed\s*$",
        "",
        str(
            title
        ),
        flags=re.IGNORECASE,
    ).strip()

    image = None

    if isinstance(
        product,
        dict,
    ):
        image_value = product.get(
            "image"
        )

        if isinstance(
            image_value,
            list,
        ):
            image = (
                image_value[0]
                if image_value
                else None
            )
        elif isinstance(
            image_value,
            str,
        ):
            image = image_value

    image = (
        image
        or extraire_meta(
            html,
            "og:image",
        )
    )

    prix_usd = extraire_prix_usd(
        html,
        product=product,
    )

    shipping_usd = extraire_shipping_usd(
        html
    )

    texte = nettoyer_html(
        html
    )

    condition = None

    for valeur in (
        "Gently Used",
        "New",
        "Used",
    ):
        if contient_expression(
            texte,
            valeur,
        ):
            condition = valeur
            break

    return {
        "title": title,
        "price_usd": prix_usd,
        "shipping_usd": shipping_usd,
        "image": image,
        "url": url,
        "sold": listing_est_vendu(
            html,
            product=product,
        ),
        "condition": condition,
    }


def extraire_liens_listing_du_html(
    html,
):
    liens = []

    motifs = (
        (
            r"href=[\"']"
            r"([^\"']*/listings/\d+[^\"']*)"
            r"[\"']"
        ),
        (
            r"[\"']"
            r"([^\"']*\\/listings\\/\d+[^\"']*)"
            r"[\"']"
        ),
    )

    for motif in motifs:
        for valeur in re.findall(
            motif,
            html,
            flags=re.IGNORECASE,
        ):
            liens.append(
                valeur.replace(
                    "\\/",
                    "/",
                )
            )

    resultat = []
    vus = set()

    for href in liens:
        href = unescape(
            href
        )

        href = urljoin(
            BASE_URL,
            href,
        )

        href = (
            href.split(
                "#",
                1,
            )[0]
            .split(
                "?",
                1,
            )[0]
        )

        if "/listings/" not in href:
            continue

        if href in vus:
            continue

        vus.add(
            href
        )

        resultat.append(
            href
        )

    return resultat


def tester_routes_browse_http(
    session,
    query,
):
    routes_valides = []
    liens = []
    vus_liens = set()

    slugs = generer_slugs_browse(
        query
    )

    print(
        "[Grailed] Pages candidates : "
        + ", ".join(
            slugs[
                :8
            ]
        )
    )

    for slug in slugs:
        if (
            len(
                routes_valides
            )
            >= MAX_ROUTES_VALIDES
        ):
            break

        url = (
            f"{BASE_URL}/browse/{slug}"
        )

        try:
            response = session.get(
                url,
                timeout=10,
                allow_redirects=True,
            )
        except Exception:
            continue

        if response.status_code in (403, 429):
            # Protection active : on marque le blocage et on s'arrête.
            _grailed_mark_blocked(str(response.status_code))
            break

        if response.status_code != 200:
            continue

        routes_valides.append(
            response.url
        )

        for lien in extraire_liens_listing_du_html(
            response.text
        ):
            if lien in vus_liens:
                continue

            vus_liens.add(
                lien
            )
            liens.append(
                lien
            )

    return (
        routes_valides,
        liens,
    )


def _normaliser_url_listing(
    href,
):
    if not href:
        return None

    href = str(
        href
    ).replace(
        "\\/",
        "/",
    )

    href = unescape(
        href
    )

    href = urljoin(
        BASE_URL,
        href,
    )

    href = (
        href.split(
            "#",
            1,
        )[0]
        .split(
            "?",
            1,
        )[0]
    )

    if "/listings/" not in href:
        return None

    return href


def _collecter_cartes_http(
    liens,
    query,
    type_recherche,
    objectif=20,
):
    """Récupère quelques listings publics sans navigateur.

    Ce n'est pas un contournement : on lit uniquement les URLs publiques déjà
    présentes dans le HTML de /browse. Cela donne à Grailed une chance de
    fournir de vraies annonces même quand le feed dynamique headless affiche
    un challenge.
    """
    if not liens:
        return []

    plafond = 12 if IS_RENDER else 20
    urls = list(dict.fromkeys(liens))[: max(1, min(plafond, objectif * 2))]
    cartes = []

    def charger(url):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=(3, 6),
                allow_redirects=True,
            )
            if response.status_code != 200:
                return None
            info = extraire_infos_listing(response.text, response.url)
            if not info or info.get("sold"):
                return None
            title = str(info.get("title") or "").strip()
            prix = info.get("price_usd")
            if not title or prix is None:
                return None
            if (
                not _recall_mode_enabled()
                and not titre_correspond_recherche(title, query, type_recherche)
            ):
                return None
            return {
                "href": info.get("url") or url,
                "title": title,
                "price_usd": prix,
                "image": info.get("image"),
                "condition": info.get("condition"),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as executor:
        futurs = [executor.submit(charger, url) for url in urls]
        for futur in as_completed(futurs):
            carte = futur.result()
            if carte:
                cartes.append(carte)
                if len(cartes) >= objectif:
                    break

    if cartes:
        print(f"[Grailed] HTTP listings : {len(cartes)} carte(s) exploitable(s)")
    return cartes


def _titre_depuis_carte(
    carte,
):
    alt = str(
        carte.get(
            "alt"
        )
        or ""
    ).strip()

    if alt:
        alt = re.sub(
            r"^\s*Image:\s*",
            "",
            alt,
            flags=re.IGNORECASE,
        )

        alt = re.sub(
            (
                r"\s+Size\s+.+?"
                r"\s+-\s+\d+\s+"
                r"(?:Thumbnail|Preview).*$"
            ),
            "",
            alt,
            flags=re.IGNORECASE,
        )

        alt = re.sub(
            r"\s+(?:Thumbnail|Preview)\s*$",
            "",
            alt,
            flags=re.IGNORECASE,
        ).strip()

        if len(
            alt
        ) >= 4:
            return alt

    texte = str(
        carte.get(
            "text"
        )
        or ""
    )

    lignes = [
        re.sub(
            r"\s+",
            " ",
            ligne,
        ).strip()
        for ligne in texte.splitlines()
    ]

    lignes = [
        ligne
        for ligne in lignes
        if ligne
    ]

    a_ignorer = (
        "$",
        "% off",
        "shipping",
        "men's",
        "women's",
        "size ",
        "gently used",
        "used",
        "new",
        "grailed verified",
        "verified",
        "save",
    )

    candidats = []

    for ligne in lignes:
        ligne_n = normaliser_texte(
            ligne
        )

        if not ligne_n:
            continue

        if any(
            marqueur in ligne.lower()
            for marqueur in a_ignorer
        ):
            continue

        if re.fullmatch(
            r"[0-9.,]+",
            ligne,
        ):
            continue

        if ligne not in candidats:
            candidats.append(
                ligne
            )

        if len(
            candidats
        ) >= 2:
            break

    return " ".join(
        candidats
    ).strip()


def _prix_depuis_carte(
    carte,
):
    texte = str(
        carte.get(
            "text"
        )
        or ""
    )

    valeurs = re.findall(
        r"(?<!\+)\$\s*([0-9]+(?:\.[0-9]{1,2})?)",
        texte,
    )

    for valeur in valeurs:
        try:
            prix = float(
                valeur
            )

            if 1 <= prix <= 100000:
                return prix
        except Exception:
            continue

    return None


def _condition_depuis_carte(
    carte,
):
    texte = normaliser_texte(
        carte.get(
            "text"
        )
        or ""
    )

    if "gently used" in texte:
        return "Gently Used"

    if contient_expression(
        texte,
        "new",
    ):
        return "New"

    if contient_expression(
        texte,
        "used",
    ):
        return "Used"

    return None


def _cartes_depuis_page(
    page,
):
    """
    Lit directement les cartes du feed Grailed déjà rendu.
    On récupère URL, texte, image et alt sans visiter chaque annonce.
    """
    try:
        brut = page.locator(
            'a[href*="/listings/"]'
        ).evaluate_all(
            """els => els.map(a => {
                let node = a;
                let best = a;

                for (let i = 0; i < 6 && node; i++) {
                    const text = (node.innerText || "").trim();

                    if (
                        text.length >= 12 &&
                        text.length <= 1400 &&
                        text.includes("$")
                    ) {
                        best = node;
                    }

                    node = node.parentElement;
                }

                const img =
                    a.querySelector("img") ||
                    best.querySelector("img");

                return {
                    href: a.href || a.getAttribute("href") || "",
                    text: (best.innerText || a.innerText || "").trim(),
                    alt: img ? (img.alt || "") : "",
                    image: img ? (img.currentSrc || img.src || "") : ""
                };
            })"""
        )
    except Exception:
        return []

    resultat = []
    vus = set()

    for carte in brut:
        href = _normaliser_url_listing(
            carte.get(
                "href"
            )
        )

        if not href:
            continue

        if href in vus:
            continue

        vus.add(
            href
        )

        carte = dict(
            carte
        )

        carte[
            "href"
        ] = href

        resultat.append(
            carte
        )

    return resultat


def _collecter_cartes(
    browser,
    routes,
    mode,
    query,
    type_recherche,
    objectif=20,
):
    cartes = []
    vus = set()

    context = browser.new_context(
        viewport={
            "width": 1440,
            "height": 1000,
        },
        locale="en-US",
    )

    page = context.new_page()

    try:
        for route in routes:
            try:
                page.goto(
                    route,
                    wait_until="domcontentloaded",
                    timeout=6500 if IS_RENDER else 15000,
                )
            except Exception as e:
                print(
                    f"[Grailed] Route {mode} ignorée : {route} ({e})"
                )
                continue

            # On détecte le blocage AVANT d'attendre le rendu du feed :
            # une page bloquée ne remplit jamais de cartes, le délai fixe
            # ne ferait que ralentir la collecte.
            try:
                page.wait_for_timeout(
                    400
                )

                titre_page = (
                    page.title()
                    or ""
                ).lower()

                corps = (
                    page.locator(
                        "body"
                    )
                    .inner_text(
                        timeout=2500
                    )
                    .lower()
                )

                marqueurs_blocage = (
                    "access denied",
                    "just a moment",
                    "verify you are human",
                    "captcha",
                )

                if any(
                    marqueur in titre_page
                    or marqueur in corps
                    for marqueur in marqueurs_blocage
                ):
                    print(
                        "[Grailed] Challenge / blocage détecté "
                        f"en mode {mode} sur {route}"
                    )

                    if mode == "headless" and not cartes:
                        # Le blocage headless est lié au navigateur, pas à
                        # la route : les routes restantes seront bloquées
                        # de la même façon. On sort tôt pour basculer sur
                        # le navigateur visible sans attendre.
                        print(
                            "[Grailed] Blocage headless cohérent -> "
                            "passage au navigateur visible"
                        )
                        break

                    continue

            except Exception:
                pass

            try:
                page.wait_for_timeout(
                    3500
                )
            except Exception:
                pass

            # 3 scrolls au lieu de 8.
            for _ in range(
                3
            ):
                for carte in _cartes_depuis_page(
                    page
                ):
                    href = carte[
                        "href"
                    ]

                    if href in vus:
                        continue

                    title = _titre_depuis_carte(
                        carte
                    )

                    # V2.8.11 : en couverture maximale, on conserve les
                    # vraies cartes même si le modèle n'est pas confirmé.
                    if (
                        not _recall_mode_enabled()
                        and not titre_correspond_recherche(
                            title,
                            query,
                            type_recherche,
                        )
                    ):
                        continue

                    prix = _prix_depuis_carte(
                        carte
                    )

                    if prix is None:
                        continue

                    vus.add(
                        href
                    )

                    carte[
                        "title"
                    ] = title

                    carte[
                        "price_usd"
                    ] = prix

                    carte[
                        "condition"
                    ] = _condition_depuis_carte(
                        carte
                    )

                    cartes.append(
                        carte
                    )

                if len(
                    cartes
                ) >= objectif:
                    break

                try:
                    page.mouse.wheel(
                        0,
                        1800,
                    )

                    page.wait_for_timeout(
                        700
                    )
                except Exception:
                    break

            print(
                "[Grailed] "
                f"{mode} : {len(cartes)} carte(s) pertinente(s) cumulée(s)"
            )

            if len(
                cartes
            ) >= objectif:
                break

    finally:
        context.close()

    return cartes


def decouvrir_cartes_playwright(
    routes,
    query,
    type_recherche,
    objectif=20,
):
    if (
        sync_playwright is None
        or not routes
    ):
        return []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True
        )

        try:
            cartes = _collecter_cartes(
                browser,
                routes,
                "headless",
                query,
                type_recherche,
                objectif=objectif,
            )
        finally:
            browser.close()

        if cartes:
            return cartes

        # V2.8.2 : le fallback visible pouvait coûter ~20-25 s pour 0
        # résultat quand Grailed présentait un challenge. Le radar progressif
        # doit rester rapide : par défaut on s'arrête après le headless.
        # L'ancien comportement reste disponible manuellement pour diagnostic.
        autoriser_visible = (
            not IS_RENDER
            and str(os.environ.get("LUXE_RADAR_GRAILED_VISIBLE", "0")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if not autoriser_visible:
            suffixe = "sur Render" if IS_RENDER else "(mode rapide)"
            print(
                "[Grailed] Feed vide/bloqué en headless -> fallback visible "
                f"ignoré {suffixe}"
            )
            # V3.7.x : feed headless vide = challenge probable. On pause la
            # source au lieu de retenter immédiatement.
            _grailed_mark_blocked("challenge headless")
            return []

        print(
            "[Grailed] Feed vide en headless -> essai navigateur visible"
        )

        browser = p.chromium.launch(
            headless=False
        )

        try:
            cartes = _collecter_cartes(
                browser,
                routes,
                "visible",
                query,
                type_recherche,
                objectif=objectif,
            )
        finally:
            browser.close()

        return cartes


def calculer_score_affaire(
    prix_eur,
    prix_max,
):
    try:
        prix_eur = float(
            prix_eur
        )
        prix_max = float(
            prix_max
        )
    except Exception:
        return 50

    if prix_max <= 0:
        return 50

    ratio = (
        prix_eur
        / prix_max
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


class GrailedConnector(
    MarketplaceConnector
):
    name = "Grailed"
    display_name = "Grailed"
    enabled = True
    currency = "USD"

    supports_pagination = False
    expansion_page_size = 36
    expansion_recall_cap = 36
    max_pages = 1
    cooldown_seconds = 0.5

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

        # V3.7.x : si Grailed est en cooldown (403/429/challenge récents),
        # on répond vide immédiatement au lieu de marteler la protection.
        if _grailed_blocked():
            print(
                "[Grailed] Source en pause (cooldown) -> aucun appel réseau"
            )
            return []

        try:
            limit = max(
                1,
                int(
                    limit
                ),
            )
        except Exception:
            limit = 20

        try:
            price_max_float = (
                float(
                    price_max
                )
                if price_max is not None
                else None
            )
        except Exception:
            price_max_float = None

        type_recherche = detecter_type_recherche(
            query
        )

        print(
            f"[Grailed] Recherche : {query}"
        )

        session = creer_session_http()

        taux_usd_eur = obtenir_taux_usd_eur()

        # --------------------------------------------------------
        # 1. Recherche de pages publiques Grailed réellement valides
        # --------------------------------------------------------

        routes_valides, liens_http = (
            tester_routes_browse_http(
                session,
                query,
            )
        )

        if routes_valides:
            print(
                "[Grailed] Routes valides : "
                + ", ".join(
                    routes_valides
                )
            )
        else:
            print(
                "[Grailed] Aucune route /browse candidate n'a répondu 200"
            )
            # V3.7.x : si la cause est un 403/429, ne pas insister maintenant.
            if _grailed_blocked():
                return []
            return []

        liens = list(
            liens_http
        )

        vus_liens = set(
            liens
        )

        if liens:
            print(
                f"[Grailed] {len(liens)} liens trouvés dans le HTML"
            )

        # --------------------------------------------------------
        # 2. Lecture directe des cartes du feed dynamique
        # --------------------------------------------------------

        objectif_cartes = max(limit * 2, 12)

        # D'abord les URLs de listing réellement présentes dans le HTML public.
        # Cela évite de dépendre systématiquement du feed JavaScript Grailed.
        cartes = _collecter_cartes_http(
            liens,
            query,
            type_recherche,
            objectif=objectif_cartes,
        )

        # Si le HTML public n'a pas fourni de cartes exploitables, on garde le
        # navigateur comme fallback. Aucun challenge n'est contourné.
        if not cartes:
            try:
                cartes = decouvrir_cartes_playwright(
                    routes_valides,
                    query,
                    type_recherche,
                    objectif=objectif_cartes,
                )
            except Exception as e:
                print(
                    f"[Grailed] Navigateur indisponible : {e}"
                )
                cartes = []

        if not cartes:
            print(
                "[Grailed] Aucune carte exploitable trouvée"
            )
            return []

        print(
            "[Grailed] "
            f"{len(cartes)} carte(s) pertinente(s) trouvée(s)"
        )

        # --------------------------------------------------------
        # 3. Construction immédiate des résultats
        #    AUCUNE ouverture individuelle des pages produit.
        # --------------------------------------------------------

        resultats = []
        produits_vus = set()
        diag_titre_prix = 0
        diag_hors_budget = 0
        diag_doublons = 0

        for carte in cartes:
            title = str(
                carte.get(
                    "title"
                )
                or ""
            ).strip()

            prix_usd = carte.get(
                "price_usd"
            )

            if (
                not title
                or prix_usd is None
            ):
                diag_titre_prix += 1
                continue

            prix_eur = round(
                float(
                    prix_usd
                )
                * taux_usd_eur,
                2,
            )

            if (
                price_max_float is not None
                and prix_eur > price_max_float
            ):
                diag_hors_budget += 1
                continue

            cle_produit = (
                normaliser_texte(
                    title
                ),
                carte.get(
                    "href"
                ),
            )

            if cle_produit in produits_vus:
                diag_doublons += 1
                continue

            produits_vus.add(
                cle_produit
            )

            score_affaire = calculer_score_affaire(
                prix_eur,
                price_max_float,
            )

            score_match = 95
            score_confiance = 70

            if carte.get(
                "image"
            ):
                score_confiance += 3

            if carte.get(
                "condition"
            ):
                score_confiance += 2

            score_confiance = min(
                score_confiance,
                80,
            )

            score = round(
                score_match
                * 0.50
                + score_confiance
                * 0.20
                + score_affaire
                * 0.30
            )

            resultats.append(
                {
                    "marketplace": self.name,
                    "titre": title,
                    "prix": prix_eur,
                    "lien": carte.get(
                        "href"
                    ),
                    "image": carte.get(
                        "image"
                    ),
                    "modele": None,
                    "prix_original": round(
                        float(
                            prix_usd
                        ),
                        2,
                    ),
                    "devise_originale": "USD",
                    "devise": "EUR",
                    "shipping": None,
                    "condition": carte.get(
                        "condition"
                    ),
                    "categorie": "A VERIFIER",
                    "score": score,
                    "score_match": score_match,
                    "score_confiance": score_confiance,
                    "score_affaire": score_affaire,
                    "alertes": [
                        "Prix converti USD vers EUR",
                        "Livraison et taxes éventuelles non incluses",
                        "Authenticité non certifiée par le radar",
                    ],
                    "raisons": [
                        "Titre correspondant strictement à la recherche",
                        "Carte publique Grailed chargée dans le navigateur",
                    ],
                }
            )

            if len(
                resultats
            ) >= limit:
                break

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
            "[Grailed] "
            f"{len(resultats)} résultats retenus"
        )
        print(
            "[Grailed][DIAG] "
            f"cartes={len(cartes)} | invalides={diag_titre_prix} | "
            f"hors_budget={diag_hors_budget} | doublons={diag_doublons}"
        )

        return resultats[
            :limit
        ]
