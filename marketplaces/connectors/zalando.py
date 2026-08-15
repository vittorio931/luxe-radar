"""Connecteur Zalando basé sur la page de catalogue publique.

La page `https://www.zalando.fr/catalogue/?q=<requete>` est rendue côté serveur
avec des cartes produits `<article class="z5x6ht ...">`. Chaque carte contient
un lien produit, un titre (marque + modèle), le prix courant en EUR et l'image.

Le connecteur est techniquement fonctionnel mais reste désactivé : pour
« Nike Trail », les titres visibles des cartes omettent souvent « trail »
(ex. « COSMIC RUNNER ») et les résultats sont donc écartés par le filtre de
mots du moteur. Il reste disponible en tant que to_test.

Aucun CAPTCHA, 403, mur de connexion ou contrôle anti-bot n'est contourné.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from threading import Lock
from time import monotonic
from urllib.parse import quote_plus, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.zalando.fr"
SEARCH_URL = f"{BASE_URL}/catalogue/"

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

HTTP_CONNECT_TIMEOUT = 2.5
HTTP_READ_TIMEOUT = 6
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)

_MAX_ITEMS = 200

# V3.0.2 : circuit breaker de stabilité. Si Zalando refuse ou ne répond pas,
# on ne martèle pas la source et on laisse les autres marketplaces continuer.
_CIRCUIT_LOCK = Lock()
_CIRCUIT_BLOCKED_UNTIL = 0.0
_CIRCUIT_REASON = ""
_CIRCUIT_TIMEOUT_SECONDS = 120
_CIRCUIT_BLOCK_SECONDS = 600

# Une carte Zalando : <article ...> ... <a href="...sluq.html" ...> ...
_ARTICLE_RE = re.compile(
    r"<article[^>]*>(.*?)</article>",
    flags=re.DOTALL,
)

_TITRE_H3_RE = re.compile(
    r"<h3[^>]*>(.*?)</h3>",
    flags=re.DOTALL,
)

_LIEN_RE = re.compile(
    r'href="(https://www\.zalando\.(?:fr|be|nl|de|at|ch|lu|dk|fi|no|se|pl|cz|it|es)[^"]*\.html)"',
    flags=re.IGNORECASE,
)

_PRIX_RE = re.compile(
    r"([0-9]+,[0-9]{2})\s*€",
)

_IMAGE_RE = re.compile(
    r'<img[^>]+src="(https://img[^"]+\.jpg[^"]*)"',
    flags=re.IGNORECASE,
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
    # Pas de retry automatique : avec un read timeout à 15 s + retries, une
    # seule page Zalando pouvait immobiliser une expansion plus de 30 s.
    # Une panne de cette source doit échouer vite et laisser le radar avancer.
    retry = Retry(
        total=0,
        connect=0,
        read=0,
        status=0,
        redirect=0,
        allowed_methods=frozenset({"GET"}),
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


def _prix_eur(texte):
    for valeur in _PRIX_RE.findall(texte):
        return round(
            _safe_float(
                valeur.replace(",", "."),
                0,
            )
            or 0,
            2,
        )

    return None


def _produit_depuis_carte(carte):
    match_lien = _LIEN_RE.search(carte)

    if not match_lien:
        return None

    lien = html_lib.unescape(
        match_lien.group(1)
    )

    match_h3 = _TITRE_H3_RE.search(carte)

    if not match_h3:
        return None

    titre = " ".join(
        re.sub(
            r"<[^>]+>",
            " ",
            match_h3.group(1),
        ).split()
    )

    if not titre:
        return None

    prix = _prix_eur(carte)

    if prix is None or prix <= 0:
        return None

    match_image = _IMAGE_RE.search(carte)

    image = (
        html_lib.unescape(
            match_image.group(1)
        )
        if match_image
        else None
    )

    if image:
        image = image.split("?")[0]

    return {
        "titre": titre,
        "prix": prix,
        "devise_originale": "EUR",
        "image": image,
        "lien": urljoin(BASE_URL, lien),
        "reference": None,
        "disponible": True,
    }


def _circuit_remaining():
    with _CIRCUIT_LOCK:
        remaining = _CIRCUIT_BLOCKED_UNTIL - monotonic()
        reason = _CIRCUIT_REASON
    return max(0.0, remaining), reason


def _trip_circuit(seconds, reason):
    global _CIRCUIT_BLOCKED_UNTIL, _CIRCUIT_REASON
    until = monotonic() + max(1, int(seconds))
    with _CIRCUIT_LOCK:
        if until > _CIRCUIT_BLOCKED_UNTIL:
            _CIRCUIT_BLOCKED_UNTIL = until
            _CIRCUIT_REASON = str(reason or "indisponible")[:80]


class ZalandoConnector(MarketplaceConnector):
    name = "Zalando"
    display_name = "Zalando"
    enabled = True
    currency = "EUR"

    def search(
        self,
        query,
        price_max=None,
        limit=20,
        page=1,
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

        try:
            page = max(1, min(int(page or 1), 100))
        except (TypeError, ValueError):
            page = 1

        remaining, circuit_reason = _circuit_remaining()
        if remaining > 0:
            print(
                "[Zalando] "
                f"pause rapide {remaining:.0f}s ({circuit_reason}) -> source ignorée"
            )
            return []

        print(
            "[Zalando] "
            f"Recherche : {query} | page={page}"
        )

        url = f"{SEARCH_URL}?q={quote_plus(query)}&p={page}"

        session = construire_session()

        try:
            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code != 200:
                print(
                    "[Zalando] "
                    f"HTTP {response.status_code}"
                )
                if response.status_code in {403, 429}:
                    _trip_circuit(
                        _CIRCUIT_BLOCK_SECONDS,
                        f"HTTP {response.status_code}",
                    )
                    print(
                        "[Zalando] blocage détecté -> pause temporaire, aucun contournement"
                    )
                return []

            cartes = _ARTICLE_RE.findall(
                response.text
            )

            resultats = []
            produits_vus = set()

            for carte in cartes[:_MAX_ITEMS]:
                detail = _produit_depuis_carte(
                    carte
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
                        "vendor": None,
                        "type_produit_site": None,
                        "disponible": detail["disponible"],
                        "reduction_pourcent": None,
                        "categorie": "A VERIFIER",
                        "score": 75,
                        "score_match": 85,
                        "score_confiance": 60,
                        "score_affaire": 55,
                        "alertes": [
                            "Prix affiché hors éventuels frais de livraison"
                        ],
                        "raisons": [
                            "Données produit récupérées depuis la recherche publique",
                        ],
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
                "[Zalando] "
                f"{len(resultats)} resultats retenus"
            )

            return resultats[:limit]

        except requests.RequestException as e:
            _trip_circuit(_CIRCUIT_TIMEOUT_SECONDS, "timeout/réseau")
            print(
                "[Zalando] "
                f"Erreur rapide : {e}"
            )
            return []

        finally:
            session.close()

    def search_page(self, query, price_max=None, limit=20, page=1):
        return self.search(query=query, price_max=price_max, limit=limit, page=page)
