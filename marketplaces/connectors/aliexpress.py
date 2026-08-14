"""Connecteur AliExpress basé sur la page de recherche publique.

La page `https://www.aliexpress.com/w/wholesale-<requete>.html` contient un
bloc JavaScript `window._dida_config_._init_data_` entre les marqueurs
`init-data-start` et `init-data-end`. Le JSON y est sérialisé avec une clé
racine sans guillemets (`{ data: {...} }`) ; on le normalise avant parsing.

Aucun compte, CAPTCHA ou mur anti-bot n'est contourné.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from urllib.parse import quote, quote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import MarketplaceConnector
from product_recognition import recognize as recognize_product

BASE_URL = "https://www.aliexpress.com"

SEARCH_TEMPLATE = (
    f"{BASE_URL}/w/"
    "wholesale-{slug}.html"
)

# Route publique alternative. Depuis août 2026, la route /w/wholesale-...
# peut répondre HTTP 200 tout en servant un flux générique sans rapport avec
# SearchText. On privilégie donc la route historique /wholesale?SearchText=...
# et on garde /w/... uniquement comme repli si aucun candidat pertinent n'est
# présent dans la première réponse.
SEARCH_QUERY_TEMPLATE = (
    f"{BASE_URL}/wholesale?SearchText={{query}}"
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

HTTP_CONNECT_TIMEOUT = 4
HTTP_READ_TIMEOUT = 15
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


def _slug_requete(query):
    return quote(
        str(query or "").strip().lower().replace(" ", "-")
    )


def _requete_normalisee(query):
    qn = normaliser_texte(query)
    return re.sub(
        r"\b(?:essantials|essencials|essensials|essentails)\b",
        "essentials",
        qn,
    )


def _recherche_ensemble(query):
    qn = _requete_normalisee(query)
    return any(
        marqueur in qn
        for marqueur in (
            "ensemble", "tracksuit", "matching set", "co ord",
            "two piece", "2 piece", "2pcs", "2 pcs", "set complet",
            "sweatsuit", "outfit",
        )
    )


def _signal_ensemble_vetement(titre):
    t = normaliser_texte(titre)

    signaux_directs = (
        "tracksuit", "track suit", "matching set", "co ord", "coord",
        "two piece", "2 piece", "2pcs", "2 pcs", "sweatsuit",
        "hoodie pants", "hoodie jogger", "hoodie joggers",
        "hoodie sweatpants", "sweatshirt pants", "sweatshirt jogger",
        "top pants", "top trousers", "set complet", "ensemble",
    )
    if any(signal in t for signal in signaux_directs):
        return True

    # ``set`` seul est trop ambigu (gift set, skincare set...). Il n'est
    # accepté qu'avec au moins un vrai indice vêtement.
    if " set" in f" {t}" or t.endswith("set"):
        vetement = (
            "hoodie", "jogger", "joggers", "sweatpants", "pants",
            "trousers", "sweatshirt", "tracksuit", "shorts", "tee",
            "t shirt", "tshirt", "top", "clothing", "sportswear",
        )
        if any(mot in t for mot in vetement):
            return True

    # Formulations fréquentes sans le mot set : hoodie + pantalon/jogger.
    haut = any(mot in t for mot in ("hoodie", "sweatshirt", "top", "tee", "t shirt", "tshirt"))
    bas = any(mot in t for mot in ("jogger", "joggers", "sweatpants", "pants", "trousers", "shorts"))
    return haut and bas


def _titre_pertinent_pour_requete(titre, query):
    """Préfiltre AliExpress avec le même moteur que le catalogue global.

    AliExpress peut renvoyer un flux générique malgré HTTP 200. On ne garde
    ici que les cartes dont l'identité produit est au moins plausible, sans
    inventer de règles séparées qui divergeraient du reste du radar.
    """
    analyse = recognize_product(
        title=titre,
        query=query,
        marketplace="AliExpress",
    )
    return bool(analyse.accepted)


def _url_recherche_principale(variante):
    return SEARCH_QUERY_TEMPLATE.format(query=quote_plus(str(variante or "").strip()))


def _url_recherche_repli(variante):
    return SEARCH_TEMPLATE.format(slug=_slug_requete(variante))


def _query_variants(query):
    """Génère quelques recherches ciblées sans élargir vers des produits hors sujet.

    AliExpress peut interpréter une formulation française littérale comme
    ``ensemble Essentials`` de façon très large. Deux variantes anglaises
    supplémentaires donnent au moteur public davantage de chances de renvoyer
    des cartes dont le titre contient réellement le type et la marque.
    """
    q = " ".join(str(query or "").split())
    if not q:
        return []

    qn = _requete_normalisee(q)

    variants = [q]
    ensemble = any(
        marker in qn
        for marker in (
            "ensemble", "tracksuit", "matching set", "co ord", "co-ord",
            "two piece", "2 piece", "2pcs", "2 pcs", "set complet",
        )
    )

    if "essentials" in qn:
        if ensemble:
            variants.extend(
                [
                    "Fear of God Essentials tracksuit",
                    "FOG Essentials matching set",
                ]
            )
        else:
            variants.extend(["Fear of God Essentials", "FOG Essentials"])
    elif ensemble:
        base = re.sub(
            r"\b(?:ensemble|tracksuit|matching set|co ord|two piece|2 piece|2pcs|2 pcs|set complet)\b",
            " ",
            qn,
        )
        base = " ".join(base.split())
        if base:
            variants.extend([f"{base} tracksuit", f"{base} matching set"])

    return _dedupe(value for value in variants if value)[:3]


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


def _liste_produits_recursive(objet):
    """Trouve une liste produit même si AliExpress déplace itemList dans son état JSON."""
    if isinstance(objet, list):
        candidats = [
            item for item in objet
            if isinstance(item, dict)
            and (item.get("productId") or item.get("itemId"))
            and (item.get("title") or item.get("productTitle"))
        ]
        if len(candidats) >= 3:
            return objet
        for valeur in objet:
            trouve = _liste_produits_recursive(valeur)
            if trouve:
                return trouve
        return None

    if isinstance(objet, dict):
        # Chemins fréquents en priorité pour éviter de parcourir tout l'état.
        for cle in ("content", "items", "itemList", "products", "resultList"):
            if cle in objet:
                trouve = _liste_produits_recursive(objet.get(cle))
                if trouve:
                    return trouve
        for valeur in objet.values():
            trouve = _liste_produits_recursive(valeur)
            if trouve:
                return trouve

    return None


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
        contenu = None

    if isinstance(contenu, list) and contenu:
        return contenu

    # Fallback : l'arborescence AliExpress change régulièrement, mais les
    # objets produit conservent généralement productId/title.
    return _liste_produits_recursive(donnees)


def _prix_minimum(item):
    chemins = (
        ("prices", "salePrice", "minPrice"),
        ("prices", "salePrice", "price"),
        ("salePrice", "minPrice"),
        ("salePrice", "price"),
        ("price", "minPrice"),
        ("price", "value"),
        ("price",),
    )

    for chemin in chemins:
        valeur = item
        try:
            for cle in chemin:
                valeur = valeur[cle] if isinstance(valeur, dict) else None
        except (KeyError, TypeError):
            valeur = None
        if isinstance(valeur, dict):
            valeur = valeur.get("value") or valeur.get("minPrice") or valeur.get("price")
        prix = _safe_float(valeur)
        if prix is not None and prix > 0:
            return prix

    return None


def _devise_item(item):
    candidats = []
    try:
        candidats.append(item["prices"]["salePrice"].get("currencyCode"))
    except (KeyError, TypeError, AttributeError):
        pass
    for cle in ("currencyCode", "currency", "currencySymbol"):
        candidats.append(item.get(cle))
    for valeur in candidats:
        if valeur:
            return str(valeur).upper().strip()
    return ""


def _produit_depuis_item(item):
    if not isinstance(item, dict):
        return None

    titre_obj = item.get("title")
    if isinstance(titre_obj, dict):
        titre_brut = titre_obj.get("displayTitle") or titre_obj.get("title")
    else:
        titre_brut = titre_obj
    titre_brut = titre_brut or item.get("productTitle") or item.get("displayTitle") or ""
    titre = " ".join(str(titre_brut).split())

    if not titre:
        return None

    prix = _prix_minimum(item)

    if prix is None or prix <= 0:
        return None

    produit_id = str(
        item.get("productId") or item.get("itemId") or ""
    ).strip()

    if not produit_id:
        return None

    image_obj = item.get("image")
    if isinstance(image_obj, dict):
        image_value = (
            image_obj.get("imgUrl")
            or image_obj.get("url")
            or image_obj.get("imageUrl")
        )
    else:
        image_value = image_obj or item.get("imageUrl") or item.get("imgUrl")
    image = _normaliser_image(image_value)

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

            items = []
            pages_ok = 0
            diagnostics_variantes = []

            for variante in _query_variants(query):
                candidats_variante = []
                pertinents_variante = []
                route_utilisee = "SearchText"

                # 1) Route publique avec paramètre SearchText.
                response = session.get(
                    _url_recherche_principale(variante),
                    timeout=HTTP_TIMEOUT,
                )

                if response.status_code == 200:
                    pages_ok += 1
                    candidats_variante = _extraire_items(response.text) or []
                    pertinents_variante = [
                        item for item in candidats_variante
                        if _titre_pertinent_pour_requete(
                            _produit_depuis_item(item).get("titre")
                            if _produit_depuis_item(item) else "",
                            query,
                        )
                    ]
                else:
                    print(
                        "[AliExpress] "
                        f"HTTP {response.status_code} sur SearchText: {variante}"
                    )

                # 2) Si AliExpress a répondu avec un flux générique, on essaie
                # l'ancienne route /w/... une seule fois pour cette variante.
                if not pertinents_variante:
                    route_utilisee = "w-fallback"
                    response_repli = session.get(
                        _url_recherche_repli(variante),
                        timeout=HTTP_TIMEOUT,
                    )
                    if response_repli.status_code == 200:
                        pages_ok += 1
                        candidats_repli = _extraire_items(response_repli.text) or []
                        pertinents_repli = [
                            item for item in candidats_repli
                            if _titre_pertinent_pour_requete(
                                _produit_depuis_item(item).get("titre")
                                if _produit_depuis_item(item) else "",
                                query,
                            )
                        ]
                        if pertinents_repli:
                            candidats_variante = candidats_repli
                            pertinents_variante = pertinents_repli
                    else:
                        print(
                            "[AliExpress] "
                            f"HTTP {response_repli.status_code} sur fallback: {variante}"
                        )

                diagnostics_variantes.append(
                    f"{variante}={len(pertinents_variante)}/{len(candidats_variante)}[{route_utilisee}]"
                )
                items.extend(pertinents_variante)

            if pages_ok == 0:
                return []

            print(
                "[AliExpress][REQUETES] "
                + " | ".join(diagnostics_variantes)
            )

            resultats = []
            produits_vus = set()
            invalides = 0
            hors_budget = 0
            devise_non_eur = 0
            doublons = 0

            if not items:
                print(
                    "[AliExpress][DIAG] aucun produit structuré détecté "
                    "dans les réponses HTML publiques"
                )

            for item in items[:_MAX_ITEMS]:
                produit = _produit_depuis_item(
                    item
                )

                if not produit:
                    invalides += 1
                    continue

                # Le connecteur compare au budget en EUR. On ne transforme jamais
                # silencieusement un prix USD/GBP/CNY en EUR sans taux fiable.
                devise_originale = str(produit.get("devise_originale") or "EUR").upper()
                if devise_originale not in {"", "EUR", "€"}:
                    devise_non_eur += 1
                    continue

                prix_eur = produit["prix"]

                if (
                    price_max is not None
                    and prix_eur > price_max
                ):
                    hors_budget += 1
                    continue

                cle = (
                    normaliser_texte(
                        produit["titre"]
                    ),
                    round(prix_eur, 2),
                )

                if cle in produits_vus:
                    doublons += 1
                    continue

                produits_vus.add(cle)

                alertes = [
                    "Prix affiché hors éventuels frais de livraison, taxes ou import"
                ]

                raisons = [
                    "Données produit récupérées depuis la recherche publique",
                ]

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
                "[AliExpress][DIAG] "
                f"pages_ok={pages_ok} | {len(items)} candidat(s) structurés | "
                f"invalides={invalides} | hors_budget={hors_budget} | "
                f"devise_non_eur={devise_non_eur} | doublons={doublons}"
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
