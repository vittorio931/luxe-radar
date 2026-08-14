"""Connecteur DHgate basé sur la page de recherche publique.

La page `https://www.dhgate.com/wholesale/search.do?searchkey=<requete>`
contient un bloc `<script id="__NEXT_DATA__">` JSON avec les produits dans
`props.pageProps.data.totalProducts`. Les prix sont fournis sous forme de
texte affichable (ex. « 42,50 - 56,38 € ») ; on garde la valeur minimale.

Le connecteur est techniquement fonctionnel mais reste désactivé : pour une
recherche générique type « Nike Trail », la pertinence renvoyée par DHgate est
faible (titres réécrits, souvent sans la marque demandée) et les résultats ne
passent pas le filtre de mots du moteur.

Aucun CAPTCHA, 403, mur de connexion ou contrôle anti-bot n'est contourné.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
import unicodedata
from urllib.parse import quote_plus, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.dhgate.com"
SEARCH_URL = f"{BASE_URL}/wholesale/search.do"

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

HTTP_CONNECT_TIMEOUT = 4
HTTP_READ_TIMEOUT = 15
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)

# DHgate renvoie parfois des 403 intermittents : une requête sur la page
# d'accueil avant la recherche stabilise la session.
WARMUP_URL = f"{BASE_URL}/"
WARMUP_PAUSE = 1.2

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


def _prix_minimum(texte):
    """Extrait le prix minimum d'un texte affichable du type « 42,50 - 56,38 € »."""
    if not texte:
        return None

    valeurs = []

    for valeur in re.findall(
        r"[0-9][0-9 ,.]*",
        str(texte),
    ):
        valeur_nettoyee = valeur.replace(
            "\xa0",
            "",
        ).replace(
            " ",
            "",
        )

        if "," in valeur_nettoyee:
            valeur_nettoyee = valeur_nettoyee.replace(
                ".",
                "",
            ).replace(
                ",",
                ".",
            )
        elif "." in valeur_nettoyee:
            valeur_nettoyee = valeur_nettoyee.replace(
                ",",
                "",
            )

        prix = _safe_float(valeur_nettoyee)

        if prix is not None and prix > 0:
            valeurs.append(prix)

    if not valeurs:
        return None

    return min(valeurs)


def _extraire_produits_next_data(texte_html):
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">'
        r"(.*?)</script>",
        texte_html,
        flags=re.DOTALL,
    )

    if not match:
        return []

    try:
        donnees = json.loads(
            html_lib.unescape(
                match.group(1)
            )
        )
    except (ValueError, TypeError):
        return []

    try:
        produits = (
            donnees["props"]["pageProps"]["data"]
            ["totalProducts"]
        )
    except (KeyError, TypeError):
        return []

    if not isinstance(produits, list):
        return []

    return produits


def _produit_depuis_next(produit):
    titre = " ".join(
        str(
            produit.get("productname") or ""
        ).split()
    )

    if not titre:
        return None

    url_brute = produit.get(
        "productDetailUrl"
    ) or produit.get("productUrl")

    if not url_brute:
        return None

    lien = urljoin(
        BASE_URL,
        str(url_brute),
    )

    prix = _prix_minimum(
        produit.get("price")
    )

    if prix is None:
        return None

    image = str(
        produit.get("bigimagepath")
        or produit.get("seo300ImagePath")
        or ""
    ).strip()

    if image.startswith("//"):
        image = "https:" + image

    return {
        "titre": titre,
        "prix": round(prix, 2),
        "devise_originale": "EUR",
        "image": image,
        "lien": lien,
        "reference": None,
        "disponible": True,
        "recentlysold": produit.get("recentlysold"),
        "freeshipping": produit.get("freeshipping"),
        "minorder": produit.get("minOrder"),
        "vendeur": produit.get("storeName"),
    }


class DHgateConnector(MarketplaceConnector):
    name = "DHgate"
    display_name = "DHgate"
    enabled = False
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
            "[DHgate] "
            f"Recherche : {query}"
        )

        session = construire_session()

        try:
            try:
                session.get(
                    WARMUP_URL,
                    timeout=HTTP_TIMEOUT,
                )
                time.sleep(WARMUP_PAUSE)
            except requests.RequestException:
                pass

            url = (
                f"{SEARCH_URL}"
                f"?searchkey={quote_plus(query)}"
            )

            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code != 200:
                print(
                    "[DHgate] "
                    f"HTTP {response.status_code}"
                )
                return []

            produits = (
                _extraire_produits_next_data(
                    response.text
                )
                or []
            )

            resultats = []
            produits_vus = set()

            for produit in produits[:_MAX_ITEMS]:
                detail = _produit_depuis_next(
                    produit
                )

                if not detail:
                    continue

                prix_eur = detail["prix"]

                if (
                    price_max is not None
                    and prix_eur > price_max
                ):
                    continue

                cle = (
                    normaliser_texte(detail["titre"]),
                    round(prix_eur, 2),
                )

                if cle in produits_vus:
                    continue

                produits_vus.add(cle)

                raisons = [
                    "Données produit récupérées depuis la recherche publique",
                ]

                if detail.get("freeshipping"):
                    raisons.append(
                        "Livraison gratuite affichée"
                    )

                if detail.get("recentlysold"):
                    raisons.append(
                        f"Ventes récentes : {detail['recentlysold']}"
                    )

                resultats.append(
                    {
                        "marketplace": self.name,
                        "titre": detail["titre"],
                        "prix": prix_eur,
                        "prix_original": prix_eur,
                        "prix_compare_original": None,
                        "devise_originale": detail[
                            "devise_originale"
                        ],
                        "devise": "EUR",
                        "lien": detail["lien"],
                        "image": detail["image"],
                        "modele": None,
                        "reference": detail["reference"],
                        "vendor": detail.get("vendeur"),
                        "type_produit_site": None,
                        "disponible": detail["disponible"],
                        "reduction_pourcent": None,
                        "categorie": "A VERIFIER",
                        "score": 70,
                        "score_match": 80,
                        "score_confiance": 55,
                        "score_affaire": 55,
                        "alertes": [
                            "Prix affiché hors éventuels frais de livraison, taxes ou import",
                            "Pertinence de la recherche DHgate non garantie",
                        ],
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
                "[DHgate] "
                f"{len(resultats)} resultats retenus"
            )

            return resultats[:limit]

        except requests.RequestException as e:
            print(
                "[DHgate] "
                f"Erreur globale : {e}"
            )
            return []

        finally:
            session.close()
