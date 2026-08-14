"""Connecteur ASOS basé sur la page de recherche publique rendue en HTML.

La page `https://www.asos.com/search/?q=<requete>` est rendue côté serveur :
chaque carte produit est une balise `<a href=".../prd/<id>#colourWayId-...">`
dont `aria-label` contient le titre et les prix en GBP
(ex. « ... , Original price £129.99 current price £77.99, Discount: -40% »).

Les prix ASOS étant affichés en GBP (locale en-GB), ils sont convertis vers
EUR avec un taux mis en cache. Aucun compte, CAPTCHA ou mur anti-bot n'est
contourné.
"""

from __future__ import annotations

import html as html_lib
import re
import threading
import time
from urllib.parse import quote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.asos.com"
SEARCH_URL = f"{BASE_URL}/search/"

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
    "Accept-Language": "en-GB,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "identity",
}

HTTP_CONNECT_TIMEOUT = 4
HTTP_READ_TIMEOUT = 15
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)

GBP_EUR_FALLBACK = 1.16
FX_CACHE_TTL = 6 * 60 * 60

_MAX_ITEMS = 200

_FX_CACHE = {
    "rate": None,
    "timestamp": 0.0,
}

# La carte produit ASOS est un <a> vers /prd/<id> avec aria-label descriptif.
_CARD_RE = re.compile(
    r'<a\s+href="(https://www\.asos\.com/[^"]*/prd/[0-9]+)[^"]*"'
    r'[^>]*aria-label="([^"]+)"',
    flags=re.IGNORECASE,
)

_IMG_RE = re.compile(
    r'<img[^>]+src="([^"]*images\.asos-media\.com/[^"]+)"',
    flags=re.IGNORECASE,
)

# L'état JSON embarqué (window.asos.plp) liste chaque produit avec son image
# principale : les cartes lazy n'ont aucun <img> dans le HTML, cette source
# couvre donc tous les produits de la page.
_ETAT_IMG_RE = re.compile(
    r'"id":(\d+),"productCode":\d+,"url":"[^"]*?","price":[0-9.]+,"description":"[^"]*?","image":"(images\.asos-media\.com/[^"]+)"',
    flags=re.IGNORECASE,
)

_IMAGE_TAILLE = "?$n_480w$&wid=476&fit=constrain"


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


def construire_session():
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
    session.headers.update(REQUEST_HEADERS)

    return session


def obtenir_taux_gbp_eur():
    maintenant = time.time()

    taux_cache = _FX_CACHE["rate"]
    age_cache = maintenant - _FX_CACHE["timestamp"]

    if (
        taux_cache is not None
        and age_cache < FX_CACHE_TTL
    ):
        return taux_cache

    try:
        session = construire_session()

        try:
            response = session.get(
                "https://api.frankfurter.dev/v2/rate/GBP/EUR",
                timeout=(3, 5),
            )
            response.raise_for_status()
            taux = float(
                response.json()["rate"]
            )
        finally:
            session.close()

        if taux <= 0:
            raise ValueError(
                "Taux de change invalide"
            )

        _FX_CACHE["rate"] = taux
        _FX_CACHE["timestamp"] = maintenant

        print(
            "[ASOS] Taux GBP->EUR : "
            f"{taux}"
        )

        return taux

    except Exception as e:
        print(
            "[ASOS] API de change indisponible, "
            "taux de secours utilise : "
            f"{GBP_EUR_FALLBACK} ({e})"
        )

        _FX_CACHE["rate"] = GBP_EUR_FALLBACK
        _FX_CACHE["timestamp"] = maintenant

        return GBP_EUR_FALLBACK


def _prix_depuis_label(label):
    prix_trouves = [
        _safe_float(valeur)
        for valeur in re.findall(
            r"£\s*([0-9]+(?:\.[0-9]{1,2})?)",
            label,
        )
        if _safe_float(valeur) is not None
    ]

    if not prix_trouves:
        return None

    return min(prix_trouves)


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

    if (
        "images.asos-media.com/" in base
        and "/products/" in base
    ):
        return base + _IMAGE_TAILLE

    return valeur


def _image_depuis_carte(carte):
    match = _IMG_RE.search(carte)

    if not match:
        return None

    return _normaliser_image(match.group(1).strip())


def _images_depuis_etat(html):
    return {
        pid: _normaliser_image(image)
        for pid, image in _ETAT_IMG_RE.findall(html)
    }


def _pid_depuis_lien(lien):
    match = re.search(r"/prd/(\d+)", lien or "")

    if not match:
        return None

    return match.group(1)


class ASOSConnector(MarketplaceConnector):
    name = "ASOS"
    display_name = "ASOS"
    enabled = True
    base_url = BASE_URL
    currency = "GBP"

    def search(
        self,
        query,
        price_max=None,
        limit=20,
    ):
        query = str(query or "").strip()

        if not query:
            return []

        limit = max(
            1,
            min(
                _safe_int(limit, 20),
                _MAX_ITEMS,
            ),
        )

        if price_max is not None:
            price_max = _safe_float(price_max)

            if (
                price_max is not None
                and price_max <= 0
            ):
                return []

        print(
            "[ASOS] "
            f"Recherche : {query}"
        )

        taux_gbp_eur = obtenir_taux_gbp_eur()

        url = f"{SEARCH_URL}?q={quote_plus(query)}"

        session = construire_session()

        try:
            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code != 200:
                print(
                    "[ASOS] "
                    f"HTTP {response.status_code}"
                )
                return []

            resultats = []
            produits_vus = set()

            for lien, label in _CARD_RE.findall(
                response.text
            ):
                label = html_lib.unescape(label)

                titre = re.split(
                    r",\s*(?:Original price|Price|current price)",
                    label,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()

                if not titre:
                    continue

                prix_gbp = _prix_depuis_label(
                    label
                )

                if prix_gbp is None:
                    continue

                prix_eur = round(
                    prix_gbp * taux_gbp_eur,
                    2,
                )

                if (
                    price_max is not None
                    and prix_eur > price_max
                ):
                    continue

                lien_propre = lien.split("#", 1)[0]

                if lien_propre in produits_vus:
                    continue

                produits_vus.add(lien_propre)

                resultats.append(
                    {
                        "marketplace": self.name,
                        "titre": titre,
                        "prix": prix_eur,
                        "prix_original": prix_gbp,
                        "prix_compare_original": None,
                        "devise_originale": "GBP",
                        "devise": "EUR",
                        "lien": lien_propre,
                        "image": None,
                        "modele": None,
                        "reference": None,
                        "vendor": None,
                        "type_produit_site": None,
                        "disponible": True,
                        "reduction_pourcent": None,
                        "categorie": "A VERIFIER",
                        "score": 75,
                        "score_match": 85,
                        "score_confiance": 60,
                        "score_affaire": 55,
                        "alertes": [
                            "Prix converti de GBP vers EUR"
                        ],
                        "raisons": [
                            "Données produit récupérées depuis la recherche publique",
                            "Prix converti de GBP vers EUR",
                        ],
                    }
                )

            # L'image est le premier <img> de la carte : on repère chaque
            # carte par son ancre produit pour associer l'image au bon lien.
            # La majorité des cartes étant lazy (aucun <img> côté serveur),
            # on complète avec l'image principale de l'état JSON embarqué.
            decoupes = re.split(
                r'<a\s+href="(https://www\.asos\.com/[^"]*/prd/[0-9]+)',
                response.text,
                flags=re.IGNORECASE,
            )

            image_par_lien = {}

            for index in range(1, len(decoupes), 2):
                lien = decoupes[index].split("#", 1)[0]
                image_par_lien[lien] = (
                    _image_depuis_carte(
                        decoupes[index + 1]
                    )
                )

            image_par_pid = _images_depuis_etat(
                response.text
            )

            for resultat in resultats:
                resultat["image"] = (
                    image_par_lien.get(
                        resultat["lien"]
                    )
                    or image_par_pid.get(
                        _pid_depuis_lien(
                            resultat["lien"]
                        )
                    )
                    or resultat.get("image")
                )

            resultats.sort(
                key=lambda item: (
                    -_safe_float(
                        item.get("score"),
                        0,
                    ),
                    _safe_float(
                        item.get("prix"),
                        999999,
                    ),
                    str(item.get("titre") or ""),
                )
            )

            print(
                "[ASOS] "
                f"{len(resultats)} resultats retenus"
            )

            return resultats[:limit]

        except requests.RequestException as e:
            print(
                "[ASOS] "
                f"Erreur globale : {e}"
            )
            return []

        finally:
            session.close()
