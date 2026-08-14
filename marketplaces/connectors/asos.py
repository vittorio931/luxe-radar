"""Connecteur ASOS public, multi-page et sensible à la locale.

Objectifs :
- privilégier ASOS France pour obtenir les prix en EUR et des titres proches
  des requêtes françaises de l'utilisateur ;
- parcourir plusieurs pages publiques de résultats sans contourner CAPTCHA,
  authentification ou contrôle d'accès ;
- garder en priorité les cartes dont le titre couvre réellement la requête ;
- utiliser la version en-GB uniquement comme complément/fallback lorsque la
  recherche française fournit trop peu de correspondances fortes.

Le connecteur reste volontairement conservateur : le filtre universel du radar
réévalue ensuite chaque résultat et peut encore l'écarter.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import html as html_lib
import os
import re
import threading
import time
import unicodedata
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.asos.com"
SEARCH_URL_FR = f"{BASE_URL}/fr/search/"
SEARCH_URL_GB = f"{BASE_URL}/search/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

REQUEST_HEADERS_BASE = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Encoding": "identity",
}

REQUEST_HEADERS_FR = {
    **REQUEST_HEADERS_BASE,
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
}

REQUEST_HEADERS_GB = {
    **REQUEST_HEADERS_BASE,
    "Accept-Language": "en-GB,en;q=0.9,fr;q=0.7",
}

HTTP_CONNECT_TIMEOUT = 4
HTTP_READ_TIMEOUT = 15
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)

GBP_EUR_FALLBACK = 1.16
FX_CACHE_TTL = 6 * 60 * 60

_MAX_ITEMS = 200
_DEFAULT_FR_PAGES = 2
_DEFAULT_GB_FALLBACK_PAGES = 1
_MAX_PAGES = 5
_MAX_QUERY_VARIANTS = 8
_MIN_STRONG_MATCHES_BEFORE_GB_FALLBACK = 12

_FX_CACHE = {
    "rate": None,
    "timestamp": 0.0,
}
_FX_LOCK = threading.Lock()
_FX_REFRESHING = False
_FX_LAST_ATTEMPT = 0.0
_FX_RETRY_INTERVAL = 15 * 60

# L'état JSON embarqué (window.asos.plp) contient habituellement l'image
# principale. Ce motif reste volontairement permissif sur le chemin produit.
_ETAT_IMG_RE = re.compile(
    r'"id":(\d+),"productCode":\d+,"url":"[^"]*?","price":[0-9.]+,'
    r'"description":"[^"]*?","image":"(images\.asos-media\.com/[^"]+)"',
    flags=re.IGNORECASE,
)

_ANCHOR_TAG_RE = re.compile(r"<a\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
_HREF_ATTR_RE = re.compile(
    r"\bhref\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_ARIA_ATTR_RE = re.compile(
    r"\baria-label\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_IMG_RE = re.compile(
    r'<img[^>]+(?:src|data-src)="([^"]*images\.asos-media\.com/[^"]+)"',
    flags=re.IGNORECASE,
)

_IMAGE_TAILLE = "?$n_480w$&wid=476&fit=constrain"

_STOPWORDS_QUERY = {
    "de", "du", "des", "le", "la", "les", "un", "une", "pour", "avec", "et",
    "the", "for", "with", "of", "a", "an", "homme", "femme", "men", "mens", "women",
    "womens", "taille", "size",
}


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


def _normaliser_texte(texte):
    texte = unicodedata.normalize("NFKD", str(texte or ""))
    texte = "".join(car for car in texte if not unicodedata.combining(car))
    texte = texte.lower().replace("’", "'")
    texte = re.sub(r"[^a-z0-9]+", " ", texte)
    texte = " ".join(texte.split())
    texte = re.sub(
        r"\b(?:essantials|essencials|essensials|essentails)\b",
        "essentials",
        texte,
    )
    return texte


def _tokens_importants(texte):
    return [
        token
        for token in _normaliser_texte(texte).split()
        if len(token) >= 2 and token not in _STOPWORDS_QUERY
    ]


def _query_essentials(query):
    qn = _normaliser_texte(query)
    return "essentials" in qn.split() or "fear of god essentials" in qn


def _titre_autre_marque_essentials(titre):
    tn = _normaliser_texte(titre)
    if "essentials" not in tn.split():
        return False
    if any(marker in tn for marker in ("fear of god", "fog essentials", "essentials fear of god")):
        return False
    # Le mot Essentials est aussi une gamme chez plusieurs marques. On ne veut
    # pas les confondre avec Fear of God ESSENTIALS.
    concurrents = (
        "adidas", "nike", "reebok", "puma", "asos design", "new balance",
        "under armour", "lacoste", "champion", "fila", "tommy hilfiger",
        "calvin klein", "jack jones", "jack & jones", "hugo boss", "boss",
        "ralph lauren", "river island", "weekday", "abercrombie", "hollister",
        "ellesse", "levis", "levi's",
    )
    return any(brand in tn for brand in concurrents)


def _token_requete_present_dans_titre(token, titre_n):
    if token == "essentials":
        return any(
            marker in titre_n
            for marker in (
                "essentials",
                "fear of god",
                "fog",
                "fog essentials",
                "essentials fear of god",
            )
        )
    return re.search(
        rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])",
        titre_n,
    ) is not None


def _score_pertinence_titre(titre, query):
    """Score local ASOS, sensible au type de produit et à ESSENTIALS.

    Une requête ``t shirt Nike Trail`` ne doit pas exiger le mot littéral
    ``shirt`` en plus du type : ``Nike Trail - T-shirt ...`` est un match fort
    dès que Nike + Trail + une forme de t-shirt sont présents.
    """
    titre_n = _normaliser_texte(titre)
    query_n = _normaliser_texte(query)
    if not titre_n or not query_n:
        return 0, False

    type_nom, _ = _detecter_type_recherche(query)
    type_tokens = set()
    if type_nom:
        for alias in _TYPE_QUERY_CONFIG[type_nom]["aliases"]:
            type_tokens.update(_normaliser_texte(alias).split())

    tokens = [
        token for token in _tokens_importants(query)
        if token not in type_tokens
    ]

    type_ok = True if not type_nom else _titre_compatible_type(titre_n, type_nom)

    if _query_essentials(query) and _titre_autre_marque_essentials(titre_n):
        return 5, False

    presents = sum(
        1 for token in tokens
        if _token_requete_present_dans_titre(token, titre_n)
    )
    couverture = presents / max(len(tokens), 1)
    fort = type_ok and presents == len(tokens)

    score = int(round(couverture * 70))
    if type_ok:
        score += 15
    if fort:
        score += 15
    if query_n and query_n in titre_n:
        score += 5

    return min(score, 100), fort


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
        pool_connections=5,
        pool_maxsize=5,
    )

    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(headers or REQUEST_HEADERS_FR)
    return session


def _actualiser_taux_gbp_eur_arriere_plan():
    """Actualise le taux live sans jamais bloquer une recherche ASOS."""
    global _FX_REFRESHING
    try:
        session = construire_session(REQUEST_HEADERS_GB)
        try:
            response = session.get(
                "https://api.frankfurter.dev/v2/rate/GBP/EUR",
                timeout=(2, 3),
            )
            response.raise_for_status()
            taux = float(response.json()["rate"])
        finally:
            session.close()

        if taux <= 0:
            raise ValueError("Taux de change invalide")

        with _FX_LOCK:
            _FX_CACHE["rate"] = taux
            _FX_CACHE["timestamp"] = time.time()
        print(f"[ASOS] Taux GBP->EUR actualisé en arrière-plan : {taux}")
    except Exception as e:
        print(f"[ASOS] Actualisation GBP->EUR différée : {e}")
    finally:
        with _FX_LOCK:
            _FX_REFRESHING = False


def obtenir_taux_gbp_eur():
    """Retourne immédiatement un taux utilisable ; le live se rafraîchit en fond."""
    global _FX_REFRESHING, _FX_LAST_ATTEMPT
    maintenant = time.time()

    with _FX_LOCK:
        taux_cache = _FX_CACHE["rate"]
        age_cache = maintenant - _FX_CACHE["timestamp"]

        if taux_cache is None:
            taux_cache = GBP_EUR_FALLBACK
            _FX_CACHE["rate"] = taux_cache
            _FX_CACHE["timestamp"] = maintenant

        doit_rafraichir = (
            not _FX_REFRESHING
            and maintenant - _FX_LAST_ATTEMPT >= _FX_RETRY_INTERVAL
            and (age_cache >= FX_CACHE_TTL or taux_cache == GBP_EUR_FALLBACK)
        )
        if doit_rafraichir:
            _FX_REFRESHING = True
            _FX_LAST_ATTEMPT = maintenant
            threading.Thread(
                target=_actualiser_taux_gbp_eur_arriere_plan,
                name="luxe-fx-gbp",
                daemon=True,
            ).start()

        return taux_cache

def _parse_nombre_prix(valeur):
    valeur = str(valeur or "").replace("\xa0", " ").strip()
    valeur = valeur.replace(" ", "")
    if not valeur:
        return None

    # 1.234,56 -> 1234.56 ; 1,234.56 -> 1234.56 ; 34,99 -> 34.99
    if "," in valeur and "." in valeur:
        if valeur.rfind(",") > valeur.rfind("."):
            valeur = valeur.replace(".", "").replace(",", ".")
        else:
            valeur = valeur.replace(",", "")
    elif "," in valeur:
        valeur = valeur.replace(",", ".")

    return _safe_float(valeur)


def _prix_et_devise_depuis_label(label):
    texte = html_lib.unescape(str(label or "")).replace("\xa0", " ")

    # D'abord les formulations qui désignent explicitement le prix courant.
    motifs_courants = [
        r"(?:current\s+price|now)\s*[:\-]?\s*([£€])?\s*([0-9]{1,6}(?:[.,][0-9]{1,2})?)\s*([£€])?",
        r"(?:prix\s+actuel|maintenant)\s*[:\-]?\s*([£€])?\s*([0-9]{1,6}(?:[.,][0-9]{1,2})?)\s*([£€])?",
    ]

    for motif in motifs_courants:
        match = re.search(motif, texte, flags=re.IGNORECASE)
        if not match:
            continue
        symbole = match.group(1) or match.group(3)
        valeur = _parse_nombre_prix(match.group(2))
        if valeur is not None and symbole in {"£", "€"}:
            return valeur, ("GBP" if symbole == "£" else "EUR")

    # Fallback : ASOS peut changer le libellé mais conserve le symbole monétaire.
    trouves = []
    motif_symbole = re.compile(
        r"(?:([£€])\s*([0-9]{1,6}(?:[.,][0-9]{1,2})?))|(?:([0-9]{1,6}(?:[.,][0-9]{1,2})?)\s*([£€]))",
        flags=re.IGNORECASE,
    )
    for match in motif_symbole.finditer(texte):
        symbole = match.group(1) or match.group(4)
        nombre = match.group(2) or match.group(3)
        valeur = _parse_nombre_prix(nombre)
        if valeur is not None and valeur > 0:
            trouves.append((valeur, "GBP" if symbole == "£" else "EUR"))

    if not trouves:
        return None, None

    # Sur les cartes soldées le prix courant est normalement le plus bas.
    return min(trouves, key=lambda item: item[0])


def _prix_depuis_label(label):
    """Compatibilité avec les anciens tests/imports."""
    prix, _ = _prix_et_devise_depuis_label(label)
    return prix


def _normaliser_image(valeur):
    if not valeur:
        return None

    valeur = str(valeur).strip()
    if valeur.startswith("//"):
        valeur = "https:" + valeur
    elif valeur.startswith("images.asos-media.com/"):
        valeur = "https://" + valeur
    if valeur.startswith("http://"):
        valeur = "https://" + valeur[len("http://"):]

    base = valeur.split("?", 1)[0].rstrip("/")
    if "images.asos-media.com/" in base and "/products/" in base:
        return base + _IMAGE_TAILLE
    return valeur


def _images_depuis_etat(html):
    return {
        pid: _normaliser_image(image)
        for pid, image in _ETAT_IMG_RE.findall(html or "")
    }


def _pid_depuis_lien(lien):
    match = re.search(r"/prd/(\d+)", lien or "")
    return match.group(1) if match else None


def _normaliser_lien(href):
    href = html_lib.unescape(str(href or "")).strip()
    if not href or "/prd/" not in href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = urljoin(BASE_URL, href)
    elif not href.lower().startswith(("http://", "https://")):
        href = urljoin(BASE_URL + "/", href)
    if "asos.com" not in href.lower():
        return None
    return href.split("#", 1)[0].split("?", 1)[0]


class _ASOSAnchorParser(HTMLParser):
    """Capture le contenu complet d'une ancre produit ASOS.

    En production, le titre peut être dans aria-label, title, le alt de l'image
    ou le texte enfant selon la version du front. Le parseur V2.6 ne lisait que
    l'ouverture de l'ancre, ce qui expliquait les centaines de cartes mais zéro
    correspondance forte observées dans les logs live.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self._depth = 0
        self._card = None

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs}
        tag = tag.lower()
        if tag == "a" and self._card is None:
            href = attrs.get("href") or ""
            if "/prd/" in href:
                self._card = {
                    "href": href,
                    "aria": attrs.get("aria-label") or "",
                    "title_attr": attrs.get("title") or "",
                    "text": [],
                    "img_alt": [],
                    "image": None,
                }
                self._depth = 1
                return
        elif self._card is not None:
            if tag == "a":
                self._depth += 1
            if tag == "img":
                alt = attrs.get("alt") or ""
                if alt:
                    self._card["img_alt"].append(alt)
                self._card["image"] = (
                    attrs.get("src") or attrs.get("data-src") or
                    attrs.get("data-lazy-src") or self._card.get("image")
                )

    def handle_data(self, data):
        if self._card is not None and data.strip():
            self._card["text"].append(data)

    def handle_endtag(self, tag):
        if self._card is None or tag.lower() != "a":
            return
        self._depth -= 1
        if self._depth > 0:
            return
        self._card["text"] = " ".join(" ".join(self._card["text"]).split())
        self._card["img_alt"] = " ".join(" ".join(self._card["img_alt"]).split())
        self.cards.append(self._card)
        self._card = None
        self._depth = 0


def _extraire_cartes_riches(html):
    parser = _ASOSAnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        pass

    cartes = []
    vus = set()
    for card in parser.cards:
        lien = _normaliser_lien(card.get("href"))
        if not lien or lien in vus:
            continue
        vus.add(lien)
        fragments = [
            card.get("aria"), card.get("title_attr"), card.get("img_alt"), card.get("text")
        ]
        label = " | ".join(str(x).strip() for x in fragments if str(x or "").strip())
        cartes.append({**card, "lien": lien, "label": label})

    # Fallback historique si le HTML est trop atypique pour HTMLParser.
    if not cartes:
        for tag in _ANCHOR_TAG_RE.findall(html or ""):
            href_match = _HREF_ATTR_RE.search(tag)
            label_match = _ARIA_ATTR_RE.search(tag)
            if not href_match or not label_match:
                continue
            lien = _normaliser_lien(href_match.group(2))
            if lien and lien not in vus:
                vus.add(lien)
                label = html_lib.unescape(label_match.group(2)).strip()
                cartes.append({
                    "href": href_match.group(2), "lien": lien, "aria": label,
                    "title_attr": "", "img_alt": "", "text": "",
                    "image": None, "label": label,
                })
    return cartes


def _extraire_cartes(html):
    """API historique utilisée par les tests : liste de (lien, label)."""
    return [(card["lien"], card["label"]) for card in _extraire_cartes_riches(html)]


def _meilleur_titre_carte(card, query):
    candidats = []
    for key in ("img_alt", "title_attr", "aria", "text"):
        value = html_lib.unescape(str(card.get(key) or ""))
        value = " ".join(value.split())
        if not value:
            continue
        # aria/text peuvent inclure le prix ; on coupe proprement.
        titre = _titre_depuis_label(value)
        if not titre or len(_normaliser_texte(titre)) < 5:
            continue
        if _normaliser_texte(titre) in {"voir le produit", "voir produit", "image produit"}:
            continue
        score, fort = _score_pertinence_titre(titre, query)
        candidats.append((1 if fort else 0, score, len(titre), titre))
    if not candidats:
        return None
    candidats.sort(reverse=True)
    return candidats[0][3]


def _titre_depuis_label(label):
    label = html_lib.unescape(str(label or ""))
    titre = re.split(
        r",\s*(?:Original price|Price|current price|Now|Prix initial|Prix d'origine|Prix actuel|Prix|Maintenant)",
        label,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    return titre


def _extraire_images_page(html):
    image_par_lien = {}

    # Découpe historique, mais accepte désormais les liens relatifs et la locale /fr/.
    decoupes = re.split(
        r'<a\s+[^>]*href=["\']((?:https?://www\.asos\.com)?/[^"\']*/prd/[0-9]+)[^"\']*["\']',
        html or "",
        flags=re.IGNORECASE,
    )
    for index in range(1, len(decoupes), 2):
        lien = _normaliser_lien(decoupes[index])
        if not lien:
            continue
        bloc = decoupes[index + 1] if index + 1 < len(decoupes) else ""
        match = _IMG_RE.search(bloc)
        if match:
            image_par_lien[lien] = _normaliser_image(match.group(1).strip())

    return image_par_lien, _images_depuis_etat(html)


def _nombre_pages(env_name, default):
    valeur = _safe_int(os.environ.get(env_name, str(default)), default)
    return max(1, min(valeur, _MAX_PAGES))


_TYPE_QUERY_CONFIG = {
    "tshirt": {
        "aliases": ("t shirt", "tshirt", "tee shirt", "teeshirt", "tee"),
        "search_terms": ("t-shirt", "tee", "running t-shirt", "dri-fit t-shirt"),
    },
    "pantalon": {
        "aliases": ("pantalon", "pantalons", "pants", "trousers", "jogger", "joggers", "jogging"),
        "search_terms": ("pants", "running pants", "trousers", "joggers"),
    },
    "short": {
        "aliases": ("short", "shorts", "bermuda"),
        "search_terms": ("shorts", "running shorts", "trail shorts"),
    },
    "veste": {
        "aliases": ("veste", "jacket", "coat", "blouson", "coupe vent", "windbreaker", "anorak"),
        "search_terms": ("jacket", "running jacket", "windbreaker", "trail jacket"),
    },
    "sweat": {
        "aliases": ("sweat", "sweatshirt", "hoodie", "sweat a capuche"),
        "search_terms": ("sweatshirt", "hoodie", "running sweatshirt"),
    },
    "chaussures": {
        "aliases": ("chaussure", "chaussures", "shoes", "basket", "baskets", "sneakers"),
        "search_terms": ("shoes", "running shoes", "trail shoes", "trainers"),
    },
    "ensemble": {
        "aliases": (
            "ensemble", "ensemble complet", "set", "set complet", "tracksuit",
            "track suit", "survetement", "survêtement", "co ord", "co-ord",
            "coord", "two piece", "two-piece", "2 piece", "2-piece",
            "matching set", "jogging set", "sweat set", "hoodie set",
            "lot de deux", "haut et bas",
            "two piece set", "2 piece set", "2 pcs", "2pcs", "2 pieces", "2 pièces",
            "ensemble 2 pieces", "ensemble 2 pièces", "set 2 pieces", "set 2 pièces",
            "hoodie and joggers", "hoodie joggers", "hoodie and pants", "hoodie pants",
            "hoodie sweatpants", "sweatshirt and joggers", "sweatshirt joggers",
            "sweat et pantalon", "sweat pantalon", "top and bottom", "top bottom set",
        ),
        "search_terms": ("ensemble", "set", "tracksuit", "matching set", "co-ord"),
    },
}


def _detecter_type_recherche(query):
    qn = _normaliser_texte(query)
    if not qn:
        return None, None
    # Expressions longues d'abord pour éviter qu'un alias court ne gagne.
    candidats = []
    for type_nom, config in _TYPE_QUERY_CONFIG.items():
        for alias in config["aliases"]:
            alias_n = _normaliser_texte(alias)
            candidats.append((len(alias_n), type_nom, alias_n))
    candidats.sort(reverse=True)
    for _, type_nom, alias_n in candidats:
        if re.search(rf"(?<![a-z0-9]){re.escape(alias_n)}(?![a-z0-9])", qn):
            return type_nom, alias_n
    return None, None


def _titre_compatible_type(titre, type_nom):
    if not type_nom:
        return True
    tn = _normaliser_texte(titre)
    if type_nom == "ensemble":
        # ``set`` tout seul est trop vague (gift set, skincare set, brush set...).
        non_mode = (
            "skincare", "skin care", "beauty", "brush", "brushes", "makeup",
            "cosmetic", "cosmetics", "shampoo", "conditioner", "haircare",
            "hair care", "body wash", "shower", "fragrance", "perfume",
            "parfum", "cologne", "nail", "manicure", "candle", "gift set",
            "giftset", "toiletry", "toiletries", "serum", "cleanser",
            "moisturiser", "moisturizer", "cream", "lotion",
        )
        mode = (
            "hoodie", "sweat", "sweatshirt", "crewneck", "veste", "jacket",
            "coat", "top", "t shirt", "tshirt", "tee", "shirt", "chemise",
            "pantalon", "pants", "trouser", "trousers", "sweatpant",
            "sweatpants", "jogger", "joggers", "short", "shorts", "legging",
            "leggings", "skirt", "jupe", "dress", "robe", "pyjama", "pajama",
            "loungewear", "tracksuit", "track suit", "survetement", "survêtement",
            "co ord", "coord", "co-ord", "activewear", "sportswear",
        )
        fortes = (
            "tracksuit", "track suit", "survetement", "survêtement", "co ord",
            "coord", "co-ord", "matching set", "jogging set", "sweat set",
            "hoodie set", "hoodie and joggers", "hoodie joggers",
            "hoodie and pants", "hoodie pants", "hoodie sweatpants",
            "sweatshirt and joggers", "sweatshirt joggers", "sweat et pantalon",
            "sweat pantalon", "top and bottom", "top bottom set",
        )
        if any(marker in tn for marker in fortes):
            return True

        hauts = ("hoodie", "sweat", "sweatshirt", "veste", "jacket", "top", "shirt", "chemise")
        bas = ("pantalon", "pants", "sweatpant", "sweatpants", "jogger", "joggers", "short", "shorts", "legging", "leggings", "skirt", "jupe")
        if any(x in tn.split() for x in hauts) and any(x in tn.split() for x in bas):
            return True

        ambigus = (
            "ensemble", "ensemble complet", "set", "set complet", "two piece",
            "two-piece", "2 piece", "2-piece", "two piece set", "2 piece set",
            "2 pcs", "2pcs", "2 pieces", "2 pièces", "lot de deux",
        )
        a_ambigu = any(marker in tn for marker in ambigus)
        a_mode = any(marker in tn for marker in mode)
        a_non_mode = any(marker in tn for marker in non_mode)
        return bool(a_ambigu and a_mode and not a_non_mode)

    config = _TYPE_QUERY_CONFIG.get(type_nom) or {}
    for alias in config.get("aliases", ()):
        an = _normaliser_texte(alias)
        if an and re.search(rf"(?<![a-z0-9]){re.escape(an)}(?![a-z0-9])", tn):
            return True
    return False


def _base_sans_type(query, alias_type):
    qn = _normaliser_texte(query)
    if not alias_type:
        return " ".join(str(query or "").split())
    tokens_alias = set(alias_type.split())
    restants = [token for token in qn.split() if token not in tokens_alias]
    # Préserve les mots produit/marque ; la casse n'a aucun impact côté ASOS.
    return " ".join(restants).strip() or qn


def _variantes_recherche(query):
    """Variantes ASOS orientées intention, sans perdre le type de produit.

    Le défaut observé en V2.5 était qu'une recherche comme ``t shirt Nike Trail``
    lançait ensuite des variantes ``Nike Running Trail`` sans le mot t-shirt.
    ASOS ramenait donc beaucoup de shorts/vestes/pantalons, tous rejetés ensuite.

    Ici les variantes spécialisées conservent toujours l'intention (t-shirt,
    pantalon, veste, etc.). Le filtre universel du radar reste l'arbitre final.
    """
    query = " ".join(str(query or "").split())
    if not query:
        return []

    qn = _normaliser_texte(query)
    tokens = set(qn.split())
    type_nom, alias_type = _detecter_type_recherche(query)
    variantes = [query]

    if type_nom:
        base = _base_sans_type(query, alias_type)
        termes = _TYPE_QUERY_CONFIG[type_nom]["search_terms"]
        # La base sans type contient encore la marque/modèle : ex. "nike trail".
        for terme in termes:
            variantes.append(f"{base} {terme}")

        # Pour Nike Trail, ASOS utilise très souvent "Nike Running - Trail".
        if "nike" in tokens and "trail" in tokens:
            terme_principal = termes[0]
            variantes.extend([
                f"Nike Running Trail {terme_principal}",
                f"Nike Trail Running {terme_principal}",
            ])
    elif "nike" in tokens and "trail" in tokens:
        variantes.extend([
            "Nike Running Trail",
            "Nike Trail Running",
            "Nike Dri-FIT Trail",
        ])
    elif "trail" in tokens:
        variantes.extend([f"{query} running", f"running {query}"])

    # Fear of God ESSENTIALS : le marché secondaire utilise indifféremment
    # Essentials, Fear of God Essentials ou FOG Essentials. On génère quelques
    # formulations utiles sans transformer "Essentials" en mot générique.
    if "essentials" in tokens:
        if type_nom:
            terme = _TYPE_QUERY_CONFIG[type_nom]["search_terms"][0]
            variantes.extend([
                f"Fear of God Essentials {terme}",
                f"Essentials Fear of God {terme}",
                f"FOG Essentials {terme}",
            ])
        else:
            variantes.extend(["Fear of God Essentials", "Essentials Fear of God", "FOG Essentials"])

    resultat, vus = [], set()
    for variante in variantes:
        # Évite "trail trail jacket" quand la base finit déjà par "trail".
        variante = re.sub(r"\b([A-Za-z0-9]+)(?:\s+\1\b)+", r"\1", variante, flags=re.IGNORECASE)
        cle = _normaliser_texte(variante)
        if not cle or cle in vus:
            continue
        vus.add(cle)
        resultat.append(variante)
        if len(resultat) >= _MAX_QUERY_VARIANTS:
            break
    return resultat


def _telecharger_pages_variantes(search_url, headers, variantes, nombre_pages, locale):
    """Télécharge toutes les variantes/pages en parallèle, sans doublonner le code."""
    taches = [(variante, page) for variante in variantes for page in range(1, nombre_pages + 1)]
    pages = []
    if not taches:
        return pages

    with ThreadPoolExecutor(max_workers=min(len(taches), 8)) as executor:
        futurs = {
            executor.submit(_telecharger_page, search_url, headers, variante, page): (variante, page)
            for variante, page in taches
        }
        for futur in as_completed(futurs):
            variante, page = futurs[futur]
            info = futur.result()
            info["query_variant"] = variante
            if info.get("status") != 200:
                detail = info.get("error") or f"HTTP {info.get('status')}"
                print(f"[ASOS][{locale}] '{variante}' page {page} indisponible : {detail}")
            pages.append(info)

    pages.sort(key=lambda item: (str(item.get("query_variant") or ""), item.get("page", 0)))
    return pages


def _telecharger_page(search_url, headers, query, page):
    session = construire_session(headers)
    debut = time.perf_counter()
    try:
        response = session.get(
            search_url,
            params={"q": query, "page": page},
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )
        return {
            "page": page,
            "status": response.status_code,
            "html": response.text if response.status_code == 200 else "",
            "url": response.url,
            "elapsed": time.perf_counter() - debut,
        }
    except requests.RequestException as e:
        return {
            "page": page,
            "status": None,
            "html": "",
            "url": search_url,
            "elapsed": time.perf_counter() - debut,
            "error": str(e),
        }
    finally:
        session.close()


def _telecharger_pages(search_url, headers, query, nombre_pages, locale):
    pages = []
    with ThreadPoolExecutor(max_workers=min(nombre_pages, 3)) as executor:
        futurs = {
            executor.submit(_telecharger_page, search_url, headers, query, page): page
            for page in range(1, nombre_pages + 1)
        }
        for futur in as_completed(futurs):
            info = futur.result()
            if info.get("status") != 200:
                detail = info.get("error") or f"HTTP {info.get('status')}"
                print(f"[ASOS][{locale}] page {info['page']} indisponible : {detail}")
            pages.append(info)

    pages.sort(key=lambda item: item["page"])
    return pages


def _construire_candidats(pages, query, price_max, locale):
    candidats = []
    produits_vus = set()
    stats = {
        "pages_ok": 0,
        "cartes": 0,
        "doublons": 0,
        "prix_invalides": 0,
        "hors_budget": 0,
        "forts": 0,
        "partiels": 0,
        "forts_budget": 0,
        "partiels_budget": 0,
    }
    taux_gbp_eur = None

    for page in pages:
        html = page.get("html") or ""
        if not html:
            continue
        stats["pages_ok"] += 1
        cartes = _extraire_cartes_riches(html)
        stats["cartes"] += len(cartes)
        image_par_lien, image_par_pid = _extraire_images_page(html)

        for card in cartes:
            lien = card["lien"]
            label = card.get("label") or ""
            if lien in produits_vus:
                stats["doublons"] += 1
                continue
            produits_vus.add(lien)

            titre = _meilleur_titre_carte(card, query) or _titre_depuis_label(label)
            if not titre:
                continue

            pertinence, fort = _score_pertinence_titre(titre, query)
            if fort:
                stats["forts"] += 1
            else:
                stats["partiels"] += 1

            prix_original, devise_originale = _prix_et_devise_depuis_label(label)
            if prix_original is None or devise_originale is None:
                stats["prix_invalides"] += 1
                continue

            if devise_originale == "GBP":
                if taux_gbp_eur is None:
                    taux_gbp_eur = obtenir_taux_gbp_eur()
                prix_eur = round(prix_original * taux_gbp_eur, 2)
            elif devise_originale == "EUR":
                prix_eur = round(prix_original, 2)
            else:
                stats["prix_invalides"] += 1
                continue

            if price_max is not None and prix_eur > price_max:
                stats["hors_budget"] += 1
                continue

            if fort:
                stats["forts_budget"] += 1
            else:
                stats["partiels_budget"] += 1

            image = (
                _normaliser_image(card.get("image"))
                or image_par_lien.get(lien)
                or image_par_pid.get(_pid_depuis_lien(lien))
            )

            candidats.append(
                {
                    "marketplace": "ASOS",
                    "titre": titre,
                    "prix": prix_eur,
                    "prix_original": prix_original,
                    "prix_compare_original": None,
                    "devise_originale": devise_originale,
                    "devise": "EUR",
                    "lien": lien,
                    "image": image,
                    "modele": None,
                    "reference": None,
                    "vendor": "ASOS",
                    "type_produit_site": None,
                    "disponible": True,
                    "reduction_pourcent": None,
                    "categorie": "A VERIFIER",
                    "score": 75,
                    "score_match": max(70, pertinence),
                    "score_confiance": 62 if locale == "FR" else 60,
                    "score_affaire": 55,
                    "site_relevance": pertinence,
                    "source_locale": locale,
                    "match_requete_fort": fort,
                    "alertes": (
                        [] if devise_originale == "EUR"
                        else ["Prix converti de GBP vers EUR"]
                    ),
                    "raisons": [
                        "Données produit récupérées depuis la recherche publique ASOS",
                        f"Recherche ASOS locale {locale}",
                        (
                            "Tous les mots importants de la requête sont présents dans le titre ASOS"
                            if fort
                            else "Résultat proposé par la recherche ASOS, à revalider par le filtre global"
                        ),
                    ] + (
                        [] if devise_originale == "EUR"
                        else ["Prix converti de GBP vers EUR"]
                    ),
                }
            )

    return candidats, stats


class ASOSConnector(MarketplaceConnector):
    name = "ASOS"
    display_name = "ASOS"
    enabled = True
    base_url = BASE_URL
    currency = "EUR"

    def search(self, query, price_max=None, limit=20):
        query = str(query or "").strip()
        if not query:
            return []

        limit = max(1, min(_safe_int(limit, 20), _MAX_ITEMS))

        if price_max is not None:
            price_max = _safe_float(price_max)
            if price_max is not None and price_max <= 0:
                return []

        print(f"[ASOS] Recherche : {query}")

        variantes = _variantes_recherche(query)
        type_nom, _ = _detecter_type_recherche(query)
        print(
            f"[ASOS][DIAG] intention={type_nom or 'generique'} | "
            f"variantes: {', '.join(variantes)}"
        )

        # Avec une intention produit précise, mieux vaut 6 requêtes ciblées x 1
        # page que 4 requêtes vagues x 2 pages : même ordre de grandeur réseau,
        # mais beaucoup moins de bruit et de rejets au filtre global.
        fr_pages_defaut = 1 if type_nom else _DEFAULT_FR_PAGES
        gb_pages_defaut = 1 if type_nom else _DEFAULT_GB_FALLBACK_PAGES

        pages_fr = _telecharger_pages_variantes(
            SEARCH_URL_FR,
            REQUEST_HEADERS_FR,
            variantes,
            _nombre_pages("LUXE_RADAR_ASOS_FR_PAGES", fr_pages_defaut),
            "FR",
        )
        candidats_fr, stats_fr = _construire_candidats(
            pages_fr,
            query=query,
            price_max=price_max,
            locale="FR",
        )

        forts_fr = sum(1 for item in candidats_fr if item.get("match_requete_fort"))
        candidats = list(candidats_fr)
        stats_gb = None

        # Le site français est prioritaire. Le site global en-GB n'est interrogé
        # qu'en complément si la couverture exacte reste faible.
        if forts_fr < min(_MIN_STRONG_MATCHES_BEFORE_GB_FALLBACK, limit):
            print(
                "[ASOS][DIAG] extension en-GB activée : "
                f"{forts_fr} correspondance(s) forte(s) FR"
            )
            pages_gb = _telecharger_pages_variantes(
                SEARCH_URL_GB,
                REQUEST_HEADERS_GB,
                variantes,
                _nombre_pages(
                    "LUXE_RADAR_ASOS_GB_PAGES",
                    gb_pages_defaut,
                ),
                "GB",
            )
            candidats_gb, stats_gb = _construire_candidats(
                pages_gb,
                query=query,
                price_max=price_max,
                locale="GB",
            )
            candidats.extend(candidats_gb)

        # Dédoublonnage inter-locale par ID produit/URL.
        uniques = []
        vus = set()
        for item in candidats:
            pid = _pid_depuis_lien(item.get("lien"))
            cle = pid or item.get("lien")
            if cle in vus:
                continue
            vus.add(cle)
            uniques.append(item)

        # Les correspondances fortes passent toujours avant les simples résultats
        # sémantiques d'ASOS. Cela évite que 100 produits bon marché mais vagues
        # chassent les vrais "Nike Trail" du lot transmis au filtre universel.
        uniques.sort(
            key=lambda item: (
                0 if item.get("match_requete_fort") else 1,
                -_safe_int(item.get("site_relevance"), 0),
                _safe_float(item.get("prix"), 999999),
                str(item.get("titre") or ""),
            )
        )

        def _diag(locale, stats):
            if not stats:
                return
            print(
                f"[ASOS][DIAG][{locale}] "
                f"pages_ok={stats['pages_ok']} | cartes={stats['cartes']} | "
                f"forts={stats['forts']} (budget={stats['forts_budget']}) | "
                f"partiels={stats['partiels']} (budget={stats['partiels_budget']}) | "
                f"prix_invalides={stats['prix_invalides']} | "
                f"hors_budget={stats['hors_budget']} | doublons={stats['doublons']}"
            )

        _diag("FR", stats_fr)
        _diag("GB", stats_gb)

        retenus = uniques[:limit]
        forts_retenus = sum(1 for item in retenus if item.get("match_requete_fort"))
        if retenus and forts_retenus == 0:
            apercu = " | ".join(
                f"{item.get('titre')} [{item.get('site_relevance', 0)}]"
                for item in retenus[:8]
            )[:1400]
            print(f"[ASOS][SAMPLE] {apercu}")
        print(
            f"[ASOS] {len(retenus)} resultats retenus "
            f"({forts_retenus} correspondance(s) forte(s))"
        )
        if forts_retenus:
            apercu = " | ".join(
                f"{item.get('titre')} @ {item.get('prix')}€"
                for item in retenus
                if item.get("match_requete_fort")
            )[:900]
            print(f"[ASOS][FORTS] {apercu}")
        return retenus
