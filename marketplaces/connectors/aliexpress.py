"""Connecteur AliExpress basé sur la page de recherche publique.

La page `https://www.aliexpress.com/w/wholesale-<requete>.html` contient un
bloc JavaScript `window._dida_config_._init_data_` entre les marqueurs
`init-data-start` et `init-data-end`. Le JSON y est sérialisé avec une clé
racine sans guillemets (`{ data: {...} }`) ; on le normalise avant parsing.

Aucun compte, CAPTCHA ou mur anti-bot n'est contourné.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.aliexpress.com"

SEARCH_TEMPLATE = (
    f"{BASE_URL}/w/"
    "wholesale-{slug}.html"
)

PRODUCT_URL_TEMPLATE = (
    f"{BASE_URL}/item/"
    "{product_id}.html"
)

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
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
}

IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
)
HTTP_CONNECT_TIMEOUT = 2.5 if IS_RENDER else 4
HTTP_READ_TIMEOUT = 6 if IS_RENDER else 15
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)

# AliExpress demande une page racine avant la recherche pour éviter les 403
# intermittents (même comportement que DHgate).
WARMUP_URL = f"{BASE_URL}/"

_MAX_ITEMS = 200


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
    texte = re.sub(r"[^a-z0-9\s']", " ", texte)
    texte = re.sub(r"\s+", " ", texte)

    return texte.strip()


def construire_session():
    retry_budget = 0 if IS_RENDER else 2
    retry = Retry(
        total=retry_budget,
        connect=retry_budget,
        read=retry_budget,
        status=retry_budget,
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


def _slug_requete(query):
    return quote(
        str(query or "").strip().lower().replace(" ", "-")
    )


def _extraire_init_data(texte_html):
    if not texte_html:
        return None

    debut = texte_html.find("init-data-start")

    if debut < 0:
        return None

    debut = texte_html.find("{", debut)

    if debut < 0:
        return None

    profondeur = 0
    entre_guillemets = False
    echappe = False

    for index in range(debut, len(texte_html)):
        caractere = texte_html[index]

        if entre_guillemets:
            if echappe:
                echappe = False
            elif caractere == "\\":
                echappe = True
            elif caractere == '"':
                entre_guillemets = False
            continue

        if caractere == '"':
            entre_guillemets = True
        elif caractere == "{":
            profondeur += 1
        elif caractere == "}":
            profondeur -= 1

            if profondeur == 0:
                brut = texte_html[debut : index + 1]

                # La racine utilise `{ data: ... }` sans guillemets.
                if brut.startswith("{ data:"):
                    brut = brut.replace(
                        "{ data:",
                        '{"data":',
                        1,
                    )

                return brut

    return None


def _normaliser_image(valeur):
    if not valeur:
        return None

    valeur = str(valeur).strip()

    if valeur.startswith("//"):
        valeur = "https:" + valeur

    return valeur


def _extraire_items(texte_html):
    brut = _extraire_init_data(texte_html)

    if not brut:
        return None

    try:
        donnees = json.loads(brut)
    except (ValueError, TypeError):
        return None

    try:
        contenu = (
            donnees["data"]["data"]["root"]["fields"]
            ["mods"]["itemList"]["content"]
        )
    except (KeyError, TypeError):
        return None

    if not isinstance(contenu, list):
        return None

    return contenu


def _prix_minimum(item):
    try:
        return _safe_float(
            item["prices"]["salePrice"]["minPrice"]
        )
    except (KeyError, TypeError):
        return None


def _devise_item(item):
    try:
        return str(
            item["prices"]["salePrice"].get("currencyCode")
            or ""
        ).upper().strip()
    except (KeyError, TypeError):
        return ""


def _produit_depuis_item(item):
    if not isinstance(item, dict):
        return None

    titre = " ".join(
        str(
            item.get("title", {}).get("displayTitle") or ""
        ).split()
    )

    if not titre:
        return None

    prix = _prix_minimum(item)

    if prix is None or prix <= 0:
        return None

    produit_id = str(
        item.get("productId") or ""
    ).strip()

    if not produit_id:
        return None

    image = _normaliser_image(
        item.get("image", {}).get("imgUrl")
    )

    vendus = item.get("trade", {}).get("tradeDesc")

    return {
        "titre": titre,
        "prix": round(prix, 2),
        "devise_originale": _devise_item(item) or "EUR",
        "image": image,
        "lien": PRODUCT_URL_TEMPLATE.format(
            product_id=produit_id
        ),
        "reference": None,
        "vendus": vendus,
        "disponible": True,
    }


class AliExpressConnector(MarketplaceConnector):
    name = "AliExpress"
    display_name = "AliExpress"
    enabled = True
    currency = "EUR"

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
            "[AliExpress] "
            f"Recherche : {query}"
        )

        session = construire_session()

        try:
            try:
                session.get(
                    WARMUP_URL,
                    timeout=HTTP_TIMEOUT,
                )
            except requests.RequestException:
                pass

            url = SEARCH_TEMPLATE.format(
                slug=_slug_requete(query)
            )

            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code != 200:
                print(
                    "[AliExpress] "
                    f"HTTP {response.status_code}"
                )
                return []

            items = _extraire_items(
                response.text
            ) or []

            resultats = []
            produits_vus = set()

            for item in items[:_MAX_ITEMS]:
                produit = _produit_depuis_item(
                    item
                )

                if not produit:
                    continue

                prix_eur = produit["prix"]

                if (
                    price_max is not None
                    and prix_eur > price_max
                ):
                    continue

                cle = (
                    normaliser_texte(
                        produit["titre"]
                    ),
                    round(prix_eur, 2),
                )

                if cle in produits_vus:
                    continue

                produits_vus.add(cle)

                alertes = [
                    "Prix affiché hors éventuels frais de livraison, taxes ou import"
                ]

                raisons = [
                    "Données produit récupérées depuis la recherche publique",
                ]

                if produit["devise_originale"] != "EUR":
                    raisons.append(
                        "Prix converti vers EUR"
                    )

                vendus = produit.get("vendus")

                if vendus:
                    raisons.append(
                        f"Ventes affichées : {vendus}"
                    )

                resultats.append(
                    {
                        "marketplace": self.name,
                        "titre": produit["titre"],
                        "prix": prix_eur,
                        "prix_original": prix_eur,
                        "prix_compare_original": None,
                        "devise_originale": produit[
                            "devise_originale"
                        ],
                        "devise": "EUR",
                        "lien": produit["lien"],
                        "image": produit["image"],
                        "modele": None,
                        "reference": produit["reference"],
                        "vendor": None,
                        "type_produit_site": None,
                        "disponible": produit["disponible"],
                        "reduction_pourcent": None,
                        "categorie": "A VERIFIER",
                        "score": 75,
                        "score_match": 85,
                        "score_confiance": 60,
                        "score_affaire": 55,
                        "alertes": alertes,
                        "raisons": raisons,
                    }
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
                    normaliser_texte(
                        item.get("titre")
                    ),
                )
            )

            print(
                "[AliExpress] "
                f"{len(resultats)} resultats retenus"
            )

            return resultats[:limit]

        except requests.RequestException as e:
            print(
                "[AliExpress] "
                f"Erreur globale : {e}"
            )
            return []

        finally:
            session.close()
