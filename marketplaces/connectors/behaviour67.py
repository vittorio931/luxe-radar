from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urljoin, urlparse

import requests
from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector


BASE_URL = "https://www.67behaviour.com"
SEARCH_URL = f"{BASE_URL}/search"

# 67behaviour affiche normalement ses prix en INR.
# Le connecteur vérifie néanmoins la devise via /cart.js quand c'est possible.
DEFAULT_STORE_CURRENCY = "INR"

# Taux de secours utilisé uniquement si l'API de change ne répond pas.
FALLBACK_INR_EUR = 0.00908
FX_CACHE_TTL = 6 * 60 * 60

HTTP_CONNECT_TIMEOUT = 4
HTTP_READ_TIMEOUT = 12
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
HTTP_MAX_WORKERS = 6

# Nombre maximum de liens produits analysés par recherche.
# Le plafond évite une explosion du nombre de requêtes sur une recherche très large.
MAX_PRODUCT_CANDIDATES = 180

# Playwright ne sert plus de moteur principal : il n'est utilisé qu'en secours
# si la page de recherche HTTP ne permet pas de récupérer les liens produits.
PLAYWRIGHT_DISCOVERY_TIMEOUT_MS = 15_000
PLAYWRIGHT_SELECTOR_TIMEOUT_MS = 8_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.7",
    "Cache-Control": "no-cache",
}

AJAX_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json,text/javascript,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.7",
    "Referer": BASE_URL + "/",
}

_FX_CACHE = {
    "rate": None,
    "timestamp": 0.0,
}
_FX_REFRESH_LOCK = threading.Lock()
_FX_REFRESHING = False
_FX_LAST_ATTEMPT = 0.0
_FX_RETRY_INTERVAL = 15 * 60

_THREAD_LOCAL = threading.local()


# ============================================================
# TYPES DE PRODUITS
# ============================================================

# Un dictionnaire unique sert à la fois à détecter le type demandé et à
# vérifier le type du résultat. Cela évite les incohérences entre deux listes.
TYPE_ALIASES = {
    "pantalon": {
        "pantalon",
        "pantalons",
        "pants",
        "trousers",
        "trouser",
        "jogger",
        "joggers",
        "sweatpants",
        "track pants",
        "cargo",
        "hose",
        "pantalones",
        "calca",
        "calcas",
        "calças",
    },
    "short": {
        "short",
        "shorts",
        "bermuda",
        "pantalon corto",
        "pantalones cortos",
    },
    "tshirt": {
        "tshirt",
        "t shirt",
        "t-shirt",
        "tee shirt",
        "teeshirt",
        "tee",
        "camiseta",
        "camiseta manga corta",
    },
    "polo": {
        "polo",
        "polo shirt",
    },
    "veste": {
        "veste",
        "vestes",
        "jacket",
        "jacke",
        "chaqueta",
        "windbreaker",
        "track jacket",
        "coat",
        "blouson",
        "manteau",
        "coupe vent",
        "anorak",
    },
    "sweat": {
        "sweat",
        "sweatshirt",
        "hoodie",
        "hooded",
        "sweat a capuche",
        "sweat à capuche",
    },
    "pull": {
        "pull",
        "pullover",
        "sweater",
        "knit",
        "jumper",
        "fleece",
        "polaire",
    },
    "chemise": {
        "chemise",
        "shirt",
        "button shirt",
        "button down",
    },
    "chaussures": {
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
        "running shoe",
        "running shoes",
        "runner",
        "runners",
        "schuhe",
        "sapatilhas",
        "zapatillas",
    },
    "chaussettes": {
        "chaussette",
        "chaussettes",
        "sock",
        "socks",
        "calcetines",
        "socken",
    },
    "maillot": {
        "maillot",
        "jersey",
    },
    "ensemble": {
        "ensemble",
        "set",
        "tracksuit",
        "outfit set",
    },
    "gilet": {
        "gilet",
        "vest",
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
    "et",
    "for",
    "with",
    "homme",
    "hommes",
    "femme",
    "femmes",
    "men",
    "mens",
    "man",
    "women",
    "womens",
    "woman",
    "unisex",
    "unisexe",
    "taille",
    "size",
}

# Faux positifs sémantiques connus.
# Exemple : "Nike Trail" ne doit pas ramener des articles NBA "Trail Blazers".
MOTS_CONTEXTE_TRAIL_BLAZERS = {
    "nba",
    "portland",
    "blazers",
    "lillard",
    "basketball",
}


# ============================================================
# OUTILS GENERAUX
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
        if not unicodedata.combining(caractere)
    )

    texte = texte.replace("-", " ")
    texte = texte.replace("_", " ")
    texte = texte.replace("’", "'")
    texte = re.sub(r"[^a-z0-9\s']", " ", texte)
    texte = re.sub(r"\s+", " ", texte)

    return texte.strip()


def contient_expression(texte, expression):
    texte_n = normaliser_texte(texte)
    expression_n = normaliser_texte(expression)

    if not texte_n or not expression_n:
        return False

    pattern = (
        r"(?<![a-z0-9])"
        + re.escape(expression_n)
        + r"(?![a-z0-9])"
    )

    return re.search(
        pattern,
        texte_n,
    ) is not None


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


def _dedupe(iterable):
    resultat = []
    vus = set()

    for valeur in iterable:
        if valeur in vus:
            continue

        vus.add(valeur)
        resultat.append(valeur)

    return resultat


def _tokens(texte):
    return re.findall(
        r"[a-z0-9]+",
        normaliser_texte(texte),
    )


# ============================================================
# HTTP ROBUSTE
# ============================================================


def construire_session(headers=None):
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.45,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )

    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(headers or REQUEST_HEADERS)

    return session


def _session_thread(cookies=None):
    session = getattr(
        _THREAD_LOCAL,
        "session",
        None,
    )

    if session is None:
        session = construire_session(
            AJAX_HEADERS
        )
        _THREAD_LOCAL.session = session

    if cookies:
        session.cookies.update(cookies)

    return session


# ============================================================
# TYPE / PERTINENCE
# ============================================================


def detecter_type_recherche(query):
    query_n = normaliser_texte(query)

    candidats = []

    for type_produit, aliases in TYPE_ALIASES.items():
        for alias in aliases:
            alias_n = normaliser_texte(alias)
            candidats.append(
                (
                    len(alias_n),
                    type_produit,
                    alias_n,
                )
            )

    # Expressions longues d'abord : "pantalon corto" avant "pantalon".
    candidats.sort(reverse=True)

    for _, type_produit, alias_n in candidats:
        if contient_expression(
            query_n,
            alias_n,
        ):
            return type_produit

    return None


def _tokens_alias_types():
    tokens = set()

    for aliases in TYPE_ALIASES.values():
        for alias in aliases:
            tokens.update(
                _tokens(alias)
            )

    return tokens


_TOKENS_TYPES = _tokens_alias_types()


def mots_importants_recherche(
    query,
    type_recherche=None,
):
    mots = []

    for token in _tokens(query):
        if token in MOTS_GENERIQUES:
            continue

        if (
            type_recherche
            and token in _TOKENS_TYPES
        ):
            continue

        mots.append(token)

    return _dedupe(mots)


def _titre_contient_type(
    titre,
    type_recherche,
):
    if not type_recherche:
        return True

    aliases = TYPE_ALIASES.get(
        type_recherche,
        set(),
    )

    if not aliases:
        return True

    return any(
        contient_expression(
            titre,
            alias,
        )
        for alias in aliases
    )


def _faux_positif_connu(
    titre,
    query,
):
    titre_n = normaliser_texte(titre)
    query_n = normaliser_texte(query)

    if not titre_n or not query_n:
        return False

    titre_tokens = set(
        _tokens(titre_n)
    )
    query_tokens = set(
        _tokens(query_n)
    )

    cherche_trail = "trail" in query_tokens
    cherche_blazers = "blazers" in query_tokens
    cherche_portland = "portland" in query_tokens

    if (
        cherche_trail
        and not cherche_blazers
        and "trail blazers" in titre_n
    ):
        return True

    contexte_nba = bool(
        titre_tokens.intersection(
            MOTS_CONTEXTE_TRAIL_BLAZERS
        )
    )

    if (
        cherche_trail
        and not cherche_portland
        and not cherche_blazers
        and contexte_nba
        and "trail" in titre_tokens
        and (
            "blazers" in titre_tokens
            or "portland" in titre_tokens
            or "nba" in titre_tokens
        )
    ):
        return True

    return False


def titre_correspond_recherche(
    title,
    query,
    type_recherche=None,
):
    titre_n = normaliser_texte(title)

    if not titre_n:
        return False

    if _faux_positif_connu(
        titre_n,
        query,
    ):
        return False

    if (
        type_recherche
        and not _titre_contient_type(
            titre_n,
            type_recherche,
        )
    ):
        return False

    mots_importants = mots_importants_recherche(
        query,
        type_recherche,
    )

    if mots_importants and not all(
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


def _actualiser_taux_inr_eur_arriere_plan():
    """Rafraîchit le taux sans jamais bloquer la recherche utilisateur."""
    global _FX_REFRESHING
    try:
        session = construire_session(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        try:
            response = session.get(
                "https://api.frankfurter.dev/v2/rate/INR/EUR",
                timeout=(2, 3),
            )
            response.raise_for_status()
            taux = float(response.json()["rate"])
        finally:
            session.close()

        if taux <= 0:
            raise ValueError("Taux de change invalide")

        with _FX_REFRESH_LOCK:
            _FX_CACHE["rate"] = taux
            _FX_CACHE["timestamp"] = time.time()
        print(f"[Conversion] Taux INR->EUR actualisé en arrière-plan : {taux}")
    except Exception as e:
        # Le taux de secours/caché reste disponible ; aucune recherche n'attend ce réseau.
        print(f"[Conversion] Actualisation INR->EUR différée : {e}")
    finally:
        with _FX_REFRESH_LOCK:
            _FX_REFRESHING = False


def obtenir_taux_inr_eur():
    """Retourne immédiatement un taux utilisable et rafraîchit le live en fond.

    L'ancien comportement pouvait bloquer la première vague près de 15-20 s si
    Frankfurter répondait lentement. Pour un radar shopping, une variation de
    quelques dixièmes de pourcent du change est moins grave qu'un blocage UI.
    """
    global _FX_REFRESHING, _FX_LAST_ATTEMPT
    maintenant = time.time()

    with _FX_REFRESH_LOCK:
        taux_cache = _FX_CACHE["rate"]
        age_cache = maintenant - _FX_CACHE["timestamp"]
        if taux_cache is None:
            taux_cache = FALLBACK_INR_EUR
            _FX_CACHE["rate"] = taux_cache
            _FX_CACHE["timestamp"] = maintenant

        doit_rafraichir = (
            not _FX_REFRESHING
            and maintenant - _FX_LAST_ATTEMPT >= _FX_RETRY_INTERVAL
            and (age_cache >= FX_CACHE_TTL or taux_cache == FALLBACK_INR_EUR)
        )

        if doit_rafraichir:
            _FX_REFRESHING = True
            _FX_LAST_ATTEMPT = maintenant
            threading.Thread(
                target=_actualiser_taux_inr_eur_arriere_plan,
                name="luxe-fx-inr",
                daemon=True,
            ).start()

        return taux_cache

def convertir_inr_vers_eur(prix_inr):
    prix = _safe_float(prix_inr)

    if prix is None:
        return None

    return round(
        prix * obtenir_taux_inr_eur(),
        2,
    )


# ============================================================
# URLS / IMAGES
# ============================================================


def normaliser_url_image(valeur):
    if not valeur:
        return None

    valeur = str(valeur).strip()

    if "," in valeur:
        candidats = [
            morceau.strip()
            for morceau in valeur.split(",")
            if morceau.strip()
        ]

        if candidats:
            valeur = candidats[-1].split()[0]

    valeur = valeur.split()[0]

    if valeur.startswith("//"):
        valeur = "https:" + valeur

    return urljoin(
        BASE_URL,
        valeur,
    )


def _normaliser_url_produit(valeur):
    if not valeur:
        return None

    valeur = html_lib.unescape(
        str(valeur).strip()
    )

    url = urljoin(
        BASE_URL,
        valeur,
    )

    parsed = urlparse(url)

    if parsed.netloc.lower() not in {
        "67behaviour.com",
        "www.67behaviour.com",
    }:
        return None

    path = parsed.path.rstrip("/")

    if "/products/" not in path:
        return None

    # On garde exactement /products/<handle>.
    match = re.search(
        r"/products/([^/?#]+)",
        path,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    handle = match.group(1).strip()

    if not handle:
        return None

    return f"{BASE_URL}/products/{handle}"


def _url_ajax_produit(url_produit):
    return url_produit.rstrip("/") + ".js"


# ============================================================
# DECOUVERTE DES PRODUITS
# ============================================================


def _extraire_liens_produits_html(texte_html):
    if not texte_html:
        return []

    motifs = [
        r'''href\s*=\s*["']([^"']*/products/[^"']+)["']''',
        r'''["'](https?://(?:www\.)?67behaviour\.com/products/[^"']+)["']''',
    ]

    liens = []

    for motif in motifs:
        for valeur in re.findall(
            motif,
            texte_html,
            flags=re.IGNORECASE,
        ):
            url = _normaliser_url_produit(
                valeur
            )

            if url:
                liens.append(url)

    return _dedupe(liens)


def _decouvrir_liens_http(
    session,
    query,
):
    url = (
        f"{SEARCH_URL}"
        f"?q={quote(query)}"
        "&type=product"
    )

    response = session.get(
        url,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    return _extraire_liens_produits_html(
        response.text
    )


def _decouvrir_liens_playwright(query):
    """
    Secours uniquement.

    Le navigateur n'est ouvert que pour récupérer les href produits.
    Les données produits sont ensuite récupérées via HTTP, ce qui évite
    le bug historique "Target page/context/browser has been closed" pendant
    l'analyse complète des cartes.
    """
    url = (
        f"{SEARCH_URL}"
        f"?q={quote(query)}"
        "&type=product"
    )

    derniere_erreur = None

    # Un essai headless puis, sur une machine locale, un essai visible.
    for headless in (True, False):
        try:
            with sync_playwright() as p:
                browser = None
                context = None

                try:
                    browser = p.chromium.launch(
                        headless=headless
                    )

                    context = browser.new_context(
                        viewport={
                            "width": 1400,
                            "height": 900,
                        },
                        locale="en-US",
                        user_agent=USER_AGENT,
                    )

                    page = context.new_page()

                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=PLAYWRIGHT_DISCOVERY_TIMEOUT_MS,
                    )

                    try:
                        page.wait_for_selector(
                            'a[href*="/products/"]',
                            timeout=PLAYWRIGHT_SELECTOR_TIMEOUT_MS,
                        )
                    except PlaywrightTimeoutError:
                        # La page peut avoir fini de charger sans déclencher
                        # le sélecteur immédiatement. On continue avec ce qui existe.
                        pass

                    if page.is_closed():
                        raise RuntimeError(
                            "La page 67behaviour s'est fermee pendant la decouverte"
                        )

                    links = page.locator(
                        'a[href*="/products/"]'
                    )

                    nombre = links.count()
                    resultat = []

                    for i in range(nombre):
                        if page.is_closed():
                            break

                        try:
                            href = links.nth(i).get_attribute(
                                "href"
                            )
                        except PlaywrightError:
                            continue

                        lien = _normaliser_url_produit(
                            href
                        )

                        if lien:
                            resultat.append(lien)

                    resultat = _dedupe(resultat)

                    if resultat:
                        return resultat

                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass

                    if browser is not None:
                        try:
                            if browser.is_connected():
                                browser.close()
                        except Exception:
                            pass

        except Exception as e:
            derniere_erreur = e
            continue

    if derniere_erreur:
        print(
            "[67behaviour] "
            "Playwright secours indisponible : "
            f"{derniere_erreur}"
        )

    return []


# ============================================================
# DONNEES PRODUIT SHOPIFY
# ============================================================


def _prix_shopify_vers_unite(valeur):
    """
    L'Ajax Product API Shopify renvoie les prix monétaires dans la plus
    petite unité de la devise de présentation (ex. paise / cents).
    """
    nombre = _safe_float(valeur)

    if nombre is None:
        return None

    return round(
        nombre / 100.0,
        2,
    )


def _premiere_image_shopify(data):
    valeur = data.get(
        "featured_image"
    )

    if isinstance(valeur, dict):
        valeur = (
            valeur.get("src")
            or valeur.get("url")
        )

    if not valeur:
        images = data.get("images") or []

        if images:
            valeur = images[0]

            if isinstance(valeur, dict):
                valeur = (
                    valeur.get("src")
                    or valeur.get("url")
                )

    return normaliser_url_image(
        valeur
    )


def _extraire_reference_shopify(data):
    variantes = data.get("variants") or []

    # Préférence à une variante disponible, sinon la première.
    candidats = sorted(
        variantes,
        key=lambda variante: not bool(
            variante.get("available")
        ),
    )

    for variante in candidats:
        sku = str(
            variante.get("sku") or ""
        ).strip()

        if sku:
            return sku

    return None


def _disponibilite_shopify(data):
    variantes = data.get("variants") or []

    if variantes:
        return any(
            bool(variante.get("available"))
            for variante in variantes
        )

    disponible = data.get("available")

    if disponible is None:
        return None

    return bool(disponible)


def _produit_depuis_shopify_json(
    data,
    url_produit,
    devise,
):
    if not isinstance(data, dict):
        return None

    title = " ".join(
        str(
            data.get("title") or ""
        ).split()
    )

    if not title:
        return None

    prix = _prix_shopify_vers_unite(
        data.get("price")
    )

    if prix is None or prix <= 0:
        return None

    compare_at = _prix_shopify_vers_unite(
        data.get("compare_at_price")
    )

    if (
        compare_at is not None
        and compare_at <= prix
    ):
        compare_at = None

    discount_percent = None

    if compare_at:
        discount_percent = round(
            max(
                0.0,
                (1 - prix / compare_at) * 100,
            ),
            1,
        )

    return {
        "titre": title,
        "prix_original": prix,
        "prix_compare_original": compare_at,
        "devise_originale": devise,
        "image": _premiere_image_shopify(data),
        "lien": _normaliser_url_produit(
            data.get("url")
        ) or url_produit,
        "reference": _extraire_reference_shopify(data),
        "vendor": data.get("vendor"),
        "type_produit_site": data.get("type"),
        "disponible": _disponibilite_shopify(data),
        "reduction_pourcent": discount_percent,
    }


def _extraire_meta_html(
    texte_html,
    cle,
):
    if not texte_html:
        return None

    cle_escaped = re.escape(cle)

    motifs = [
        rf'''<meta[^>]+(?:property|name)=["']{cle_escaped}["'][^>]+content=["']([^"']+)["']''',
        rf'''<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']{cle_escaped}["']''',
    ]

    for motif in motifs:
        match = re.search(
            motif,
            texte_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if match:
            return html_lib.unescape(
                match.group(1).strip()
            )

    return None


def _produit_depuis_html_fallback(
    texte_html,
    url_produit,
    devise,
):
    """Fallback si /products/<handle>.js n'est pas disponible."""
    title = (
        _extraire_meta_html(
            texte_html,
            "og:title",
        )
        or _extraire_meta_html(
            texte_html,
            "twitter:title",
        )
    )

    prix_brut = (
        _extraire_meta_html(
            texte_html,
            "product:price:amount",
        )
        or _extraire_meta_html(
            texte_html,
            "og:price:amount",
        )
    )

    devise_meta = (
        _extraire_meta_html(
            texte_html,
            "product:price:currency",
        )
        or _extraire_meta_html(
            texte_html,
            "og:price:currency",
        )
        or devise
    )

    image = _extraire_meta_html(
        texte_html,
        "og:image",
    )

    prix = _safe_float(prix_brut)

    if not title or prix is None or prix <= 0:
        return None

    return {
        "titre": " ".join(title.split()),
        "prix_original": round(prix, 2),
        "prix_compare_original": None,
        "devise_originale": str(
            devise_meta or devise
        ).upper(),
        "image": normaliser_url_image(image),
        "lien": url_produit,
        "reference": None,
        "vendor": None,
        "type_produit_site": None,
        "disponible": None,
        "reduction_pourcent": None,
    }


def _charger_produit_http(
    url_produit,
    devise,
    cookies=None,
):
    session = _session_thread(
        cookies
    )

    # 1) Shopify Ajax Product API : beaucoup plus léger et stable que le DOM.
    try:
        response = session.get(
            _url_ajax_produit(url_produit),
            timeout=HTTP_TIMEOUT,
        )

        if response.ok:
            content_type = (
                response.headers.get(
                    "content-type",
                    "",
                ).lower()
            )

            if (
                "json" in content_type
                or response.text.lstrip().startswith("{")
            ):
                data = response.json()
                produit = _produit_depuis_shopify_json(
                    data,
                    url_produit,
                    devise,
                )

                if produit:
                    return produit

    except Exception:
        pass

    # 2) Fallback page HTML + métadonnées OpenGraph.
    try:
        response = session.get(
            url_produit,
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()

        return _produit_depuis_html_fallback(
            response.text,
            url_produit,
            devise,
        )

    except Exception:
        return None


def _devise_boutique(session):
    try:
        response = session.get(
            f"{BASE_URL}/cart.js",
            headers=AJAX_HEADERS,
            timeout=(3, 6),
        )

        if response.ok:
            data = response.json()
            devise = str(
                data.get("currency")
                or DEFAULT_STORE_CURRENCY
            ).upper()

            if re.fullmatch(
                r"[A-Z]{3}",
                devise,
            ):
                return devise

    except Exception:
        pass

    return DEFAULT_STORE_CURRENCY


# ============================================================
# CONVERSION PRIX PRODUIT VERS EUR
# ============================================================


def _prix_vers_eur(
    prix,
    devise,
    taux_inr_eur,
):
    prix_float = _safe_float(prix)

    if prix_float is None:
        return None

    devise = str(
        devise or DEFAULT_STORE_CURRENCY
    ).upper()

    if devise == "EUR":
        return round(prix_float, 2)

    if devise == "INR":
        return round(
            prix_float * taux_inr_eur,
            2,
        )

    # On préfère ignorer un prix plutôt que faire une conversion fausse.
    return None


# ============================================================
# SCORE PRIX
# ============================================================


def calculer_score_affaire_67(
    prix_eur,
    prix_max,
):
    prix = _safe_float(prix_eur)
    plafond = _safe_float(prix_max)

    if prix is None:
        return 0

    if plafond is None or plafond <= 0:
        return 50

    ratio = prix / plafond

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


# ============================================================
# COMPATIBILITE AVEC L'ANCIEN CONNECTEUR
# ============================================================


def extraire_prix_inr(texte):
    """Conservé pour compatibilité avec d'éventuels imports existants."""
    if not texte:
        return None

    motifs = [
        r"₹\s*([\d,]+(?:\.\d{1,2})?)",
        r"Rs\.?\s*([\d,]+(?:\.\d{1,2})?)",
        r"INR\s*([\d,]+(?:\.\d{1,2})?)",
    ]

    prix_trouves = []

    for motif in motifs:
        for valeur in re.findall(
            motif,
            str(texte),
            flags=re.IGNORECASE,
        ):
            try:
                prix = float(
                    valeur.replace(",", "")
                )
            except (TypeError, ValueError):
                continue

            if prix > 0:
                prix_trouves.append(prix)

    if not prix_trouves:
        return None

    return min(prix_trouves)


def trouver_carte_produit(link):
    """Compatibilité legacy : renvoie le meilleur ancêtre produit connu."""
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


def extraire_titre(card, link):
    """Compatibilité legacy."""
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
                titre = element.inner_text(
                    timeout=800
                ).strip()

                if titre:
                    return " ".join(
                        titre.split()
                    )
        except Exception:
            continue

    try:
        titre = (
            link.get_attribute("title")
            or link.get_attribute("aria-label")
            or link.inner_text(timeout=800)
            or ""
        ).strip()

        return " ".join(
            titre.split()
        )
    except Exception:
        return ""


def extraire_image(card):
    """Compatibilité legacy."""
    try:
        images = card.locator("img")
        nombre_images = images.count()

        for i in range(
            min(nombre_images, 4)
        ):
            img = images.nth(i)
            valeur = (
                img.get_attribute("src")
                or img.get_attribute("data-src")
                or img.get_attribute("data-lazy-src")
                or img.get_attribute("srcset")
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
# CONNECTEUR 67BEHAVIOUR
# ============================================================


class Behaviour67Connector(MarketplaceConnector):
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

        limit = max(
            1,
            _safe_int(
                limit,
                20,
            ),
        )

        if price_max is not None:
            price_max = _safe_float(
                price_max
            )

            if (
                price_max is not None
                and price_max <= 0
            ):
                return []

        type_recherche = detecter_type_recherche(
            query
        )

        print(
            "[67behaviour] "
            f"Recherche : {query}"
        )

        session = construire_session()

        try:
            devise_boutique = _devise_boutique(
                session
            )

            # Le taux n'est récupéré qu'une fois par recherche.
            taux_inr_eur = (
                obtenir_taux_inr_eur()
                if devise_boutique == "INR"
                else FALLBACK_INR_EUR
            )

            liens = []

            # ----------------------------------------------------
            # 1. DECOUVERTE HTTP — chemin principal
            # ----------------------------------------------------
            try:
                liens = _decouvrir_liens_http(
                    session,
                    query,
                )

                if liens:
                    print(
                        "[67behaviour] "
                        f"{len(liens)} liens produits trouves via HTTP"
                    )

            except Exception as e:
                print(
                    "[67behaviour] "
                    "Recherche HTTP indisponible : "
                    f"{e}"
                )

            # ----------------------------------------------------
            # 2. PLAYWRIGHT — secours seulement
            # ----------------------------------------------------
            if not liens:
                print(
                    "[67behaviour] "
                    "Passage au mode navigateur de secours"
                )

                liens = _decouvrir_liens_playwright(
                    query
                )

                if liens:
                    print(
                        "[67behaviour] "
                        f"{len(liens)} liens produits trouves via navigateur"
                    )

            if not liens:
                print(
                    "[67behaviour] "
                    "0 lien produit exploitable"
                )
                return []

            # On analyse plus de candidats que la limite finale pour laisser
            # le filtre de pertinence et le classement faire leur travail.
            nombre_candidats = min(
                len(liens),
                max(
                    min(limit * 4, MAX_PRODUCT_CANDIDATES),
                    min(80, MAX_PRODUCT_CANDIDATES),
                ),
            )

            liens = liens[:nombre_candidats]
            cookies = session.cookies.get_dict()

            resultats = []
            produits_vus = set()

            # ----------------------------------------------------
            # 3. CHARGEMENT PRODUITS PAR L'API AJAX SHOPIFY
            # ----------------------------------------------------
            with ThreadPoolExecutor(
                max_workers=min(
                    HTTP_MAX_WORKERS,
                    max(1, len(liens)),
                )
            ) as executor:
                futures = {
                    executor.submit(
                        _charger_produit_http,
                        lien,
                        devise_boutique,
                        cookies,
                    ): lien
                    for lien in liens
                }

                for future in as_completed(futures):
                    try:
                        produit = future.result()
                    except Exception:
                        continue

                    if not produit:
                        continue

                    title = produit.get(
                        "titre"
                    ) or ""

                    if not titre_correspond_recherche(
                        title,
                        query,
                        type_recherche,
                    ):
                        continue

                    prix_original = _safe_float(
                        produit.get(
                            "prix_original"
                        )
                    )

                    devise_originale = str(
                        produit.get(
                            "devise_originale"
                        )
                        or devise_boutique
                    ).upper()

                    prix_eur = _prix_vers_eur(
                        prix_original,
                        devise_originale,
                        taux_inr_eur,
                    )

                    if prix_eur is None:
                        continue

                    if (
                        price_max is not None
                        and prix_eur > price_max
                    ):
                        continue

                    cle_produit = (
                        normaliser_texte(title),
                        round(prix_eur, 2),
                    )

                    if cle_produit in produits_vus:
                        continue

                    produits_vus.add(
                        cle_produit
                    )

                    score_affaire = calculer_score_affaire_67(
                        prix_eur,
                        price_max,
                    )

                    score_match = 95
                    score_confiance = 65

                    score = round(
                        score_match * 0.50
                        + score_confiance * 0.20
                        + score_affaire * 0.30
                    )

                    alertes = [
                        "Prix affiché hors éventuels frais de livraison, taxes ou import"
                    ]

                    disponible = produit.get(
                        "disponible"
                    )

                    if disponible is False:
                        alertes.append(
                            "Produit actuellement indisponible sur 67behaviour"
                        )

                    raisons = [
                        "Type et mots importants de la recherche vérifiés",
                        "Données produit récupérées directement depuis la fiche produit",
                    ]

                    if devise_originale == "INR":
                        raisons.append(
                            "Prix converti INR vers EUR"
                        )

                    reduction = produit.get(
                        "reduction_pourcent"
                    )

                    if reduction is not None:
                        raisons.append(
                            f"Réduction affichée : {reduction:.1f}%"
                        )

                    resultat = {
                        "marketplace": self.name,
                        "titre": title,
                        "prix": prix_eur,
                        "prix_original": prix_original,
                        "prix_compare_original": produit.get(
                            "prix_compare_original"
                        ),
                        "devise_originale": devise_originale,
                        "devise": "EUR",
                        "lien": produit.get("lien"),
                        "image": produit.get("image"),
                        "modele": None,
                        "reference": produit.get("reference"),
                        "vendor": produit.get("vendor"),
                        "type_produit_site": produit.get(
                            "type_produit_site"
                        ),
                        "disponible": disponible,
                        "reduction_pourcent": reduction,
                        "categorie": "A VERIFIER",
                        "score": score,
                        "score_match": score_match,
                        "score_confiance": score_confiance,
                        "score_affaire": score_affaire,
                        "alertes": _dedupe(alertes),
                        "raisons": _dedupe(raisons),
                    }

                    resultats.append(
                        resultat
                    )

            # ----------------------------------------------------
            # 4. CLASSEMENT
            # ----------------------------------------------------
            resultats.sort(
                key=lambda item: (
                    item.get("disponible") is False,
                    -_safe_float(
                        item.get("score"),
                        0,
                    ),
                    -_safe_float(
                        item.get("score_confiance"),
                        0,
                    ),
                    _safe_float(
                        item.get("prix"),
                        999999,
                    ),
                    normaliser_texte(
                        item.get("titre")
                    ),
                )
            )

            print(
                "[67behaviour] "
                f"{len(resultats)} resultats retenus"
            )

            return resultats[:limit]

        except Exception as e:
            print(
                "[67behaviour] "
                f"Erreur globale : {e}"
            )
            return []

        finally:
            session.close()
