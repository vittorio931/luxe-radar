from __future__ import annotations

import json
import os
import re
import unicodedata
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector

BASE_URL = "https://www.ssense.com"
SEARCH_URL = f"{BASE_URL}/en-fr/men"

# La recherche publique de SSENSE est bien sensible à la requête
# (vérifié : "nike trail" ne renvoie que des Nike trail, "arc'teryx"
# uniquement des Arc'teryx). Les données produits sont lues depuis le
# balisage structuré JSON-LD présent dans la page HTML.
IS_RENDER = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
)
HTTP_CONNECT_TIMEOUT = 2.5 if IS_RENDER else 4
HTTP_READ_TIMEOUT = 5 if IS_RENDER else 12
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}

# Les images JSON-LD de SSENSE contiennent un placeholder "__IMAGE_PARAMS__".
# On le remplace par des paramètres Cloudinary réels (vérifié : HTTP 200).
IMAGE_PARAMS = "f_auto,q_auto,w_900"

# Mots trop génériques pour être exigés dans le titre d'un résultat.
MOTS_GENERIQUES = {
    "a", "an", "the", "de", "du", "des", "le", "la", "les", "un", "une",
    "pour", "avec", "et", "for", "with", "homme", "hommes", "femme",
    "femmes", "men", "mens", "man", "women", "womens", "woman", "unisex",
    "unisexe", "taille", "size", "s", "x", "p",
}


def _normaliser_texte(texte):
    texte = "" if texte is None else str(texte)
    texte = texte.lower().strip()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(caractere for caractere in texte if not unicodedata.combining(caractere))
    texte = texte.replace("-", " ").replace("_", " ").replace("’", "'")
    texte = re.sub(r"[^a-z0-9\s']", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


def _contient_expression(texte, expression):
    texte_n = _normaliser_texte(texte)
    expression_n = _normaliser_texte(expression)
    if not texte_n or not expression_n:
        return False
    return re.search(
        r"(?<![a-z0-9])" + re.escape(expression_n) + r"(?![a-z0-9])",
        texte_n,
    ) is not None


def _tokens(texte):
    return re.findall(r"[a-z0-9]+", _normaliser_texte(texte))


def _mots_importants(query):
    mots = []
    vus = set()
    for token in _tokens(query):
        if token in MOTS_GENERIQUES:
            continue
        if len(token) < 3:
            continue
        if token in vus:
            continue
        vus.add(token)
        mots.append(token)
    return mots


def _titre_correspond(titre, query):
    mots = _mots_importants(query)
    if not mots:
        return True
    return all(_contient_expression(titre, mot) for mot in mots)


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
        pool_connections=10,
        pool_maxsize=10,
    )
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(REQUEST_HEADERS)
    return session


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
    if "__IMAGE_PARAMS__" in valeur:
        valeur = valeur.replace("__IMAGE_PARAMS__", IMAGE_PARAMS)
    if valeur.startswith("//"):
        valeur = "https:" + valeur
    return valeur


def extraire_produits_jsonld(texte_html):
    """Lit les blocs Product du balisage structuré JSON-LD de la page."""
    if not texte_html:
        return []

    produits = []

    for bloc in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        texte_html,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(bloc.strip())
        except Exception:
            continue

        elements = data if isinstance(data, list) else [data]

        for element in elements:
            if not isinstance(element, dict):
                continue
            if element.get("@type") != "Product":
                continue

            nom = " ".join(str(element.get("name") or "").split())
            if not nom:
                continue

            marque = ""
            marque_data = element.get("brand")
            if isinstance(marque_data, dict):
                marque = str(marque_data.get("name") or "").strip()

            offre = element.get("offers") or {}
            if isinstance(offre, list):
                offre = offre[0] if offre else {}
            if not isinstance(offre, dict):
                offre = {}

            prix = _safe_float(offre.get("price"))
            if prix is None or prix <= 0:
                continue

            produits.append({
                "nom": nom,
                "marque": marque,
                "reference": str(element.get("sku") or "").strip(),
                "prix": round(prix, 2),
                "devise": str(offre.get("priceCurrency") or "").upper(),
                "lien": str(element.get("url") or "").strip(),
                "image": normaliser_url_image(element.get("image")),
            })

    return produits


class SSENSEConnector(MarketplaceConnector):
    name = "SSENSE"
    display_name = "SSENSE"
    enabled = True
    currency = "EUR"

    def search(self, query, price_max=None, limit=20):
        query = str(query or "").strip()
        if not query:
            return []

        limit = max(1, _safe_int(limit, 20))
        if price_max is not None:
            price_max = _safe_float(price_max)
            if price_max is not None and price_max <= 0:
                return []

        print(f"[SSENSE] Recherche : {query}")

        url = f"{SEARCH_URL}?q={quote(query)}"
        session = construire_session()

        try:
            response = session.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
        except Exception as e:
            print(f"[SSENSE] Recherche indisponible : {e}")
            return []
        finally:
            session.close()

        produits = extraire_produits_jsonld(response.text)

        if not produits:
            print("[SSENSE] Aucun produit JSON-LD exploitable")
            return []

        resultats = []
        vus = set()

        for produit in produits:
            marque = produit.get("marque") or ""
            nom = produit.get("nom") or ""

            # Les noms SSENSE n'incluent pas la marque : on la préfixe pour
            # que la pertinence globale du moteur (marque + modèle) fonctionne.
            nom_n = _normaliser_texte(nom)
            marque_n = _normaliser_texte(marque)
            if marque_n and not nom_n.startswith(marque_n):
                titre = f"{marque} {nom}"
            else:
                titre = nom

            if not _titre_correspond(titre, query):
                continue

            prix = produit.get("prix")
            if price_max is not None and prix > price_max:
                continue

            lien = produit.get("lien")
            if not lien:
                continue

            cle = lien
            if cle in vus:
                continue
            vus.add(cle)

            prix_reference = produit.get("reference")

            resultat = {
                "marketplace": self.name,
                "titre": " ".join(titre.split()),
                "prix": prix,
                "devise": "EUR",
                "devise_originale": produit.get("devise") or "EUR",
                "lien": lien,
                "image": produit.get("image"),
                "modele": None,
                "reference": prix_reference or None,
                "vendor": marque or None,
                "categorie": "A VERIFIER",
                "score": 62,
                "score_match": 82,
                "score_confiance": 60,
                "score_affaire": 50,
                "alertes": [
                    "Prix neuf boutique ; frais de livraison et disponibilité à vérifier sur SSENSE"
                ],
                "raisons": [
                    "Données produits extraites du balisage structuré JSON-LD public de SSENSE",
                ],
            }

            if prix_reference:
                resultat["raisons"].append(
                    f"Référence produit : {prix_reference}"
                )

            resultats.append(resultat)

        resultats.sort(
            key=lambda item: (
                -_safe_float(item.get("score"), 0),
                _safe_float(item.get("prix"), 999999),
                _normaliser_texte(item.get("titre")),
            )
        )

        print(f"[SSENSE] {len(resultats)} résultats retenus")

        return resultats[:limit]
