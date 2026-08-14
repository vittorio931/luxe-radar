from playwright.sync_api import sync_playwright
from urllib.parse import quote
import re
import unicodedata

from modeles import MARQUES_MODELES


# ============================================================
# CONFIGURATION
# ============================================================

MOTS_EXCLUS = {
    "badge", "patch", "sticker", "autocollant", "coque", "case",
    "accessoire", "logo", "ecusson", "porte cle", "porte-cle",
    "lacets", "shoelace", "boite vide", "box only", "dust bag",
}

# Mots pouvant indiquer une annonce qui ne correspond pas à l'article.
MOTS_EXCLUS_FORTS = {
    "replica", "replique", "réplique", "fake", "faux", "fausse",
    "counterfeit", "contrefacon", "contrefaçon", "1:1",
    "mirror quality", "super fake", "aaa quality",
}

# Red flags très forts : une annonce contenant ces termes ne doit
# pas pouvoir être classée comme une bonne affaire.
MOTS_VETO = {
    "replica", "replique", "réplique", "fake", "faux", "fausse",
    "counterfeit", "contrefacon", "contrefaçon", "1:1",
    "super fake", "aaa quality", "unauthorized authentic",
}

MOTS_DOUTEUX_FORTS = {
    "replica", "replique", "réplique", "fake", "faux", "fausse",
    "counterfeit", "contrefacon", "contrefaçon", "1:1",
    "mirror quality", "super fake", "aaa quality",
    "ua", "unauthorized authentic",
}

MOTS_DOUTEUX_MOYENS = {
    "style", "inspire", "inspired", "inspiré", "look", "type",
    "dupe", "similaire", "inspired by", "comme",
}

MOTS_VETEMENT = {
    "t shirt", "tee shirt", "teeshirt", "tee", "polo", "pull",
    "sweat", "hoodie", "veste", "blouson", "manteau", "chemise",
    "shirt", "pantalon", "jean", "short", "jogging", "survetement",
    "doudoune", "gilet", "knit", "cardigan", "maillot", "cargo",
}

MOTS_ETAT = {
    "neuf", "new", "jamais porte", "jamais porté", "excellent état",
    "tres bon etat", "très bon état", "bon etat", "bon état",
    "comme neuf", "comme neuf", "very good condition",
}

MOTS_TAILLE = {
    "xs", "s", "m", "l", "xl", "xxl",
    "36", "37", "38", "39", "40", "41", "42", "43", "44", "45", "46",
}

# ============================================================
# OUTILS TEXTE
# ============================================================

def nettoyer_texte(texte):
    if texte is None:
        return ""

    texte = str(texte).lower().strip()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        c for c in texte
        if not unicodedata.combining(c)
    )

    # Conserver les chiffres/lettres et séparer la ponctuation.
    texte = texte.replace("-", " ")
    texte = texte.replace("_", " ")
    texte = re.sub(r"[^\w€\s./]", " ", texte)
    texte = re.sub(r"\s+", " ", texte)

    return texte.strip()


def contient_mot(texte, mot):
    """
    Recherche par mot/phrase plutôt que par simple 'mot in texte'.
    Cela évite par exemple de détecter 'ua' dans 'chaussure'.
    """
    texte = nettoyer_texte(texte)
    mot = nettoyer_texte(mot)

    if not texte or not mot:
        return False

    pattern = r"(?<!\w)" + re.escape(mot) + r"(?!\w)"
    return re.search(pattern, texte, flags=re.IGNORECASE) is not None


def trouver_termes(texte, dictionnaire):
    return [
        mot for mot in dictionnaire
        if contient_mot(texte, mot)
    ]


# ============================================================
# PRIX
# ============================================================

def extraire_prix(texte):
    if not texte:
        return None

    texte = str(texte)

    # Formats : 49 €, 49€, 49.99 €, 49,99 €, €49
    motifs = [
        r"(\d{1,4}(?:[,.]\d{1,2})?)\s*€",
        r"€\s*(\d{1,4}(?:[,.]\d{1,2})?)",
    ]

    prix_trouves = []

    for motif in motifs:
        prix_trouves.extend(re.findall(motif, texte))

    for valeur in prix_trouves:
        try:
            prix = float(valeur.replace(",", "."))
            if 0 < prix < 100000:
                return prix
        except (ValueError, TypeError):
            continue

    return None


# ============================================================
# MODELE / PERTINENCE
# ============================================================

def trouver_modele(marque, texte):
    texte = nettoyer_texte(texte)
    marque_norm = nettoyer_texte(marque)

    modeles = MARQUES_MODELES.get(marque, {})

    # On essaie d'abord les variantes les plus longues.
    candidats = []

    for modele, variantes in modeles.items():
        for variante in variantes:
            variante_propre = nettoyer_texte(variante)
            if variante_propre:
                candidats.append((len(variante_propre), modele, variante_propre))

    candidats.sort(reverse=True)

    for _, modele, variante_propre in candidats:
        if contient_mot(texte, variante_propre):
            return modele

    return None


def marque_presente(marque, texte):
    return contient_mot(texte, marque)


# ============================================================
# ANALYSE DE CONFIANCE
# ============================================================

def calculer_confiance(
    marque,
    titre,
    texte,
    prix,
    modele,
    image,
):
    """
    Score de confiance séparé du score d'affaire.

    0-39  : très douteux
    40-59 : faible
    60-74 : moyen
    75-89 : bon
    90-100: très bon

    Le but est d'éviter qu'un prix très bas fasse monter
    artificiellement une annonce suspecte.
    """
    titre_n = nettoyer_texte(titre)
    texte_n = nettoyer_texte(texte)
    tout = f"{titre_n} {texte_n}".strip()

    confiance = 55
    alertes = []
    raisons = []

    # Marque
    if marque_presente(marque, titre_n):
        confiance += 15
        raisons.append("Marque clairement présente dans le titre")
    elif marque_presente(marque, texte_n):
        confiance += 7
        raisons.append("Marque détectée dans l'annonce")
    else:
        confiance -= 20
        alertes.append("Marque non clairement identifiable")

    # Modèle
    if modele:
        confiance += 12
        raisons.append(f"Modèle détecté : {modele}")

    # Description
    mots_description = len(texte_n.split())
    if mots_description >= 25:
        confiance += 7
        raisons.append("Description suffisamment détaillée")
    elif mots_description >= 10:
        confiance += 3
    elif mots_description <= 3:
        confiance -= 8
        alertes.append("Description très pauvre")

    # Prix : un prix extrêmement bas n'est PAS un signal de confiance.
    # On le traite comme un éventuel signal de risque.
    if prix is not None and prix <= 10:
        confiance -= 4
        alertes.append("Prix exceptionnellement bas")

    # Photo
    if image:
        confiance += 5
        raisons.append("Photo disponible")
    else:
        confiance -= 8
        alertes.append("Aucune photo détectée")

    # Etat
    if trouver_termes(tout, MOTS_ETAT):
        confiance += 4
        raisons.append("État renseigné")

    # Red flags
    forts = trouver_termes(tout, MOTS_DOUTEUX_FORTS)
    moyens = trouver_termes(tout, MOTS_DOUTEUX_MOYENS)

    if forts:
        # Chaque signal fort compte, mais on plafonne le malus.
        malus = min(65, 32 + (len(forts) - 1) * 10)
        confiance -= malus
        for mot in forts:
            alertes.append(f"Signal suspect fort : {mot}")

    if moyens:
        confiance -= min(25, len(moyens) * 8)
        for mot in moyens:
            alertes.append(f"Signal ambigu : {mot}")

    # Si un terme de suspicion est dans le titre, c'est plus grave.
    forts_titre = trouver_termes(titre_n, MOTS_DOUTEUX_FORTS)
    if forts_titre:
        confiance -= 10
        alertes.append("Terme suspect présent directement dans le titre")

    return max(0, min(int(confiance), 100)), raisons, alertes


# ============================================================
# SCORE D'AFFAIRE
# ============================================================

def calculer_score_affaire(prix, prix_max):
    """
    Score basé sur la position du prix dans le budget.
    Ce score ne dit PAS si l'annonce est fiable.
    """
    if prix is None:
        return 0, "Prix non disponible"

    if prix_max is None or prix_max <= 0:
        # Sans plafond, on évite de considérer automatiquement
        # un prix bas comme une excellente affaire.
        return 50, "Prix disponible"

    ratio = prix / float(prix_max)

    if ratio <= 0.35:
        return 100, "Prix exceptionnellement bas"
    if ratio <= 0.50:
        return 92, "Prix très intéressant"
    if ratio <= 0.65:
        return 82, "Très bon prix"
    if ratio <= 0.80:
        return 70, "Bon prix"
    if ratio <= 0.95:
        return 58, "Prix intéressant"
    return 45, "Proche du plafond"


# ============================================================
# ANALYSE PRINCIPALE
# ============================================================

def analyser_annonce(
    marque,
    titre,
    texte,
    prix,
    modele,
    image,
    prix_max=None,
):
    titre_n = nettoyer_texte(titre)
    texte_n = nettoyer_texte(texte)
    tout = f"{titre_n} {texte_n}".strip()

    raisons = []
    alertes = []

    # --------------------------------------------------------
    # 1. EXCLUSION / VETO
    # --------------------------------------------------------
    exclus = trouver_termes(titre_n, MOTS_EXCLUS)
    if exclus:
        alertes.extend(
            [f"Article/accessoire exclu : {mot}" for mot in exclus]
        )

    veto = trouver_termes(tout, MOTS_VETO)

    # --------------------------------------------------------
    # 2. MATCH
    # --------------------------------------------------------
    match = 45

    if marque_presente(marque, titre_n):
        match += 25
        raisons.append("Marque présente dans le titre")
    elif marque_presente(marque, texte_n):
        match += 12
        raisons.append("Marque détectée dans la description")
    else:
        match -= 20
        alertes.append("Marque absente ou non confirmée")

    if modele:
        match += 20
        raisons.append(f"Modèle correspondant : {modele}")

    if trouver_termes(titre_n, MOTS_VETEMENT):
        match += 5
        raisons.append("Type de vêtement identifiable")

    match = max(0, min(match, 100))

    # --------------------------------------------------------
    # 3. CONFIANCE
    # --------------------------------------------------------
    confiance, raisons_confiance, alertes_confiance = calculer_confiance(
        marque=marque,
        titre=titre,
        texte=texte,
        prix=prix,
        modele=modele,
        image=image,
    )

    raisons.extend(raisons_confiance)
    alertes.extend(alertes_confiance)

    # --------------------------------------------------------
    # 4. AFFAIRE
    # --------------------------------------------------------
    score_affaire, raison_prix = calculer_score_affaire(
        prix,
        prix_max,
    )
    raisons.append(raison_prix)

    # --------------------------------------------------------
    # 5. SCORE FINAL
    # --------------------------------------------------------
    # Le prix ne peut plus écraser un manque de confiance.
    #
    # 45% pertinence
    # 35% confiance
    # 20% intérêt prix
    score = (
        match * 0.45
        + confiance * 0.35
        + score_affaire * 0.20
    )

    # Veto : une annonce explicitement suspecte ne peut jamais
    # devenir une bonne/excellente affaire.
    if veto:
        score = min(score, 39)
        alertes.append(
            "VETO : signal explicite de contrefaçon/réplique"
        )

    if exclus:
        score = min(score, 20)

    # Si confiance très faible, on bloque les catégories positives.
    if confiance < 45:
        score = min(score, 49)

    score = max(0, min(round(score), 100))

    # --------------------------------------------------------
    # 6. CATEGORIE
    # --------------------------------------------------------
    if veto:
        categorie = "DOUTEUSE"
    elif exclus:
        categorie = "A IGNORER"
    elif confiance < 45:
        categorie = "DOUTEUSE"
    elif confiance < 60:
        categorie = "A VERIFIER"
    elif score >= 85:
        categorie = "EXCELLENTE AFFAIRE"
    elif score >= 70:
        categorie = "BONNE AFFAIRE"
    elif score >= 50:
        categorie = "INTERESSANTE"
    else:
        categorie = "A VERIFIER"

    # Déduplication propre des raisons/alertes.
    raisons = list(dict.fromkeys(raisons))
    alertes = list(dict.fromkeys(alertes))

    return {
        "score": score,
        "categorie": categorie,
        "raisons": raisons,
        "alertes": alertes,
        "score_match": match,
        "score_confiance": confiance,
        "score_affaire": score_affaire,
    }


# ============================================================
# RECHERCHE VINTED
# ============================================================

def rechercher_vinted(
    marque,
    prix_max,
    limite=10,
    headless=False,
):
    url = (
        "https://www.vinted.fr/catalog"
        f"?search_text={quote(str(marque))}"
        f"&price_to={quote(str(prix_max))}"
        "&currency=EUR"
        "&page=1"
    )

    annonces = []
    vus = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless
        )

        page = browser.new_page(
            viewport={
                "width": 1400,
                "height": 900
            },
            locale="fr-FR",
        )

        try:
            print(
                f"Recherche {marque} <= {prix_max} EUR..."
            )

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            page.wait_for_timeout(5000)

            liens = page.locator(
                'a[href*="/items/"]'
            )

            nombre_liens = liens.count()

            print(
                f"{nombre_liens} liens analyses"
            )

            for i in range(nombre_liens):
                lien = liens.nth(i)

                try:
                    href = lien.get_attribute("href")
                except Exception:
                    continue

                if not href or "/items/" not in href:
                    continue

                if href.startswith("/"):
                    href = "https://www.vinted.fr" + href

                # Nettoyage éventuel des paramètres.
                href = href.split("?", 1)[0]

                if href in vus:
                    continue

                vus.add(href)

                # ------------------------------------------------
                # BLOC ANNONCE
                # ------------------------------------------------
                try:
                    bloc = lien.locator("xpath=../../..")
                    texte_annonce = bloc.inner_text(timeout=2000)
                except Exception:
                    bloc = lien
                    texte_annonce = ""

                texte_annonce = " ".join(
                    str(texte_annonce).split()
                )

                # ------------------------------------------------
                # TITRE
                # ------------------------------------------------
                partie = href.split(
                    "/items/",
                    1
                )[-1]

                titre = partie.split(
                    "?",
                    1
                )[0]

                titre = re.sub(
                    r"^\d+-",
                    "",
                    titre
                )

                titre = titre.replace("-", " ")
                titre = " ".join(titre.split())

                titre_lower = nettoyer_texte(titre)
                texte_lower = nettoyer_texte(texte_annonce)

                # ------------------------------------------------
                # MARQUE
                # ------------------------------------------------
                if not (
                    marque_presente(marque, titre_lower)
                    or marque_presente(marque, texte_lower)
                ):
                    continue

                # ------------------------------------------------
                # EXCLUSIONS
                # ------------------------------------------------
                if trouver_termes(titre_lower, MOTS_EXCLUS):
                    continue

                # ------------------------------------------------
                # PRIX
                # ------------------------------------------------
                prix = extraire_prix(texte_annonce)

                if prix is None:
                    continue

                if prix > float(prix_max):
                    continue

                # ------------------------------------------------
                # MODELE
                # ------------------------------------------------
                modele = trouver_modele(
                    marque,
                    f"{titre_lower} {texte_lower}",
                )

                # ------------------------------------------------
                # IMAGE
                # ------------------------------------------------
                image = None

                try:
                    img = bloc.locator("img").first

                    if img.count() > 0:
                        image = (
                            img.get_attribute("src")
                            or img.get_attribute("data-src")
                            or img.get_attribute("srcset")
                        )
                except Exception:
                    image = None

                # ------------------------------------------------
                # ANALYSE
                # ------------------------------------------------
                analyse = analyser_annonce(
                    marque=marque,
                    titre=titre,
                    texte=texte_annonce,
                    prix=prix,
                    modele=modele,
                    image=image,
                    prix_max=prix_max,
                )

                titre_affiche = titre

                if modele and modele.lower() not in titre_lower:
                    titre_affiche = f"{titre} - {modele}"

                annonces.append(
                    {
                        "titre": titre_affiche,
                        "prix": prix,
                        "score": analyse["score"],
                        "categorie": analyse["categorie"],
                        "raisons": analyse["raisons"],
                        "alertes": analyse["alertes"],
                        "score_match": analyse["score_match"],
                        "score_confiance": analyse["score_confiance"],
                        "score_affaire": analyse["score_affaire"],
                        "lien": href,
                        "image": image,
                        "modele": modele,
                    }
                )

                print(
                    f"{titre} | "
                    f"{prix:.2f} EUR | "
                    f"{analyse['categorie']} | "
                    f"{analyse['score']}/100 | "
                    f"confiance {analyse['score_confiance']}/100"
                )

                # On collecte davantage que la limite finale pour
                # permettre au ranking de choisir les meilleures.
                if len(annonces) >= max(limite * 5, 30):
                    break

        except Exception as e:
            print(f"Erreur : {e}")

        finally:
            browser.close()

    # ------------------------------------------------------------
    # RANKING
    # ------------------------------------------------------------
    #
    # On ne trie plus uniquement par score :
    # - les annonces douteuses passent après les annonces fiables ;
    # - à score équivalent, le prix bas est favorisé ;
    # - les signaux de confiance restent prioritaires.
    #
    annonces.sort(
        key=lambda x: (
            x["categorie"] in {"DOUTEUSE", "A IGNORER"},
            -x["score"],
            -x["score_confiance"],
            x["prix"],
        )
    )

    return annonces[:limite]
# ============================================================
# CLASSEMENT UNIVERSEL MULTI-MARKETPLACES
# ============================================================

_TYPES_RECHERCHE_MULTI = {
    "tshirt": {
        "aliases": [
            "t shirt", "t-shirt", "tshirt",
            "tee", "tee shirt", "teeshirt",
        ],
        "titres": [
            "t shirt", "tshirt",
            "tee", "tee shirt", "teeshirt",
        ],
    },

    "pantalon": {
        "aliases": [
            "pantalon", "pantalons",
            "pants", "trousers",
            "jogger", "joggers",
            "jogging", "cargo",
        ],
        "titres": [
            "pantalon", "pants",
            "trousers", "jogger",
            "joggers", "jogging",
            "track pants", "cargo",
        ],
    },

    "short": {
        "aliases": [
            "short", "shorts",
        ],
        "titres": [
            "short", "shorts",
        ],
    },

    "veste": {
        "aliases": [
            "veste", "vestes",
            "jacket", "windbreaker",
            "coupe vent", "coupe-vent",
        ],
        "titres": [
            "veste", "jacket",
            "windbreaker", "coupe vent",
            "track jacket",
        ],
    },

    "sweat": {
        "aliases": [
            "sweat", "sweatshirt", "hoodie",
        ],
        "titres": [
            "sweat", "sweatshirt",
            "hoodie", "hooded",
        ],
    },

    "pull": {
        "aliases": [
            "pull", "pullover",
            "sweater", "jumper", "knit",
        ],
        "titres": [
            "pull", "pullover",
            "sweater", "jumper", "knit",
        ],
    },

    "chaussures": {
        "aliases": [
            "chaussure", "chaussures",
            "basket", "baskets",
            "shoe", "shoes",
            "sneaker", "sneakers",
            "trainer", "trainers",
        ],
        "titres": [
            "chaussure", "chaussures",
            "basket", "baskets",
            "shoe", "shoes",
            "sneaker", "sneakers",
            "trainer", "trainers",
        ],
    },

    "polo": {
        "aliases": [
            "polo",
        ],
        "titres": [
            "polo",
        ],
    },

    "chemise": {
        "aliases": [
            "chemise",
            "button shirt",
            "dress shirt",
        ],
        "titres": [
            "chemise",
            "button shirt",
            "dress shirt",
        ],
    },
}


_MOTS_RECHERCHE_IGNORES_MULTI = {
    "a", "an", "the",
    "le", "la", "les",
    "un", "une",
    "de", "du", "des",
    "pour", "avec", "et", "and",

    "homme", "hommes",
    "femme", "femmes",
    "men", "mens",
    "women", "womens",

    "unisex", "unisexe",
    "taille", "size",
}


# ============================================================
# SIGNAUX DANGEREUX
# ============================================================

_MOTS_VETO_MULTI = {
    "replica",
    "replique",
    "réplique",
    "fake",
    "faux",
    "fausse",
    "counterfeit",
    "contrefacon",
    "contrefaçon",
    "1:1",
    "super fake",
    "aaa quality",
    "mirror quality",
    "unauthorized authentic",
}


_MOTS_A_IGNORER_MULTI = {
    "ne pas acheter",
    "ne pas achete",
    "n achetez pas",
    "n achete pas",
    "don't buy",
    "do not buy",
    "arnaque",
    "scam",
    "annonce test",
    "test annonce",
}


_PRIORITE_CATEGORIE_MULTI = {
    "EXCELLENTE AFFAIRE": 0,
    "BONNE AFFAIRE": 1,
    "INTERESSANTE": 2,
    "A VERIFIER": 3,
    "DOUTEUSE": 4,
    "A IGNORER": 5,
}


# ============================================================
# TEXTE
# ============================================================

def _normaliser_multi(texte):
    return nettoyer_texte(texte)


def _contient_expression_multi(
    texte,
    expression,
):
    return contient_mot(
        _normaliser_multi(texte),
        _normaliser_multi(expression),
    )


# ============================================================
# TYPE DE PRODUIT
# ============================================================

def _detecter_type_multi(query):
    query_n = _normaliser_multi(
        query
    )

    for (
        type_produit,
        config,
    ) in _TYPES_RECHERCHE_MULTI.items():

        for alias in config["aliases"]:

            if _contient_expression_multi(
                query_n,
                alias,
            ):
                return type_produit

    return None


# ============================================================
# MOTS IMPORTANTS
# ============================================================

def _mots_importants_multi(
    query,
    type_recherche=None,
):
    query_n = _normaliser_multi(
        query
    )

    ignores = set(
        _MOTS_RECHERCHE_IGNORES_MULTI
    )

    if type_recherche:

        for alias in (
            _TYPES_RECHERCHE_MULTI[
                type_recherche
            ]["aliases"]
        ):

            ignores.update(
                _normaliser_multi(
                    alias
                ).split()
            )

    mots = re.findall(
        r"[a-z0-9]+",
        query_n,
    )

    return [
        mot
        for mot in mots
        if mot not in ignores
    ]


# ============================================================
# PERTINENCE
# ============================================================

def _titre_correspond_multi(
    titre,
    query,
    type_recherche=None,
):
    titre_n = _normaliser_multi(
        titre
    )

    if not titre_n:
        return False

    # --------------------------------------------------------
    # TYPE
    # --------------------------------------------------------

    if type_recherche:
        expressions = (
            _TYPES_RECHERCHE_MULTI[
                type_recherche
            ]["titres"]
        )

        if not any(
            _contient_expression_multi(
                titre_n,
                expression,
            )
            for expression in expressions
        ):
            return False

    # --------------------------------------------------------
    # MOTS IMPORTANTS
    # --------------------------------------------------------

    mots_importants = (
    _mots_importants_multi(
            query,
            type_recherche,
        )
    )

    if mots_importants:

        # Tous les mots importants doivent être présents.
        if not all(
            _contient_expression_multi(
                titre_n,
                mot,
            )
            for mot in mots_importants
        ):
            return False

        # Ils doivent également apparaître
        # dans le même ordre que la recherche.
        #
        # Nike Trail -> OK
        # Trail Blazers ... Nike -> rejeté
        if len(mots_importants) >= 2:
            tokens_titre = re.findall(
                r"[a-z0-9]+",
                titre_n,
            )

            derniere_position = -1

            for mot in mots_importants:
                position_trouvee = None

                for i in range(
                    derniere_position + 1,
                    len(tokens_titre),
                ):
                    if tokens_titre[i] == mot:
                        position_trouvee = i
                        break

                if position_trouvee is None:
                    return False

                derniere_position = position_trouvee

    return True


# ============================================================
# SCORE PRIX COMMUN
# ============================================================

def _calculer_score_affaire_multi(
    prix_reference,
    prix_max,
):
    try:
        prix = float(
            prix_reference
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0

    try:
        plafond = float(
            prix_max
        )

    except (
        TypeError,
        ValueError,
    ):
        plafond = None

    if (
        plafond is None
        or plafond <= 0
    ):
        return 50

    ratio = (
        prix
        / plafond
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


# ============================================================
# CONFIANCE DE BASE
# ============================================================

def _confiance_base_multi(
    annonce,
):
    marketplace = str(
        annonce.get(
            "marketplace"
        )
        or ""
    ).strip()

    try:

        confiance_source = int(
            round(
                float(
                    annonce.get(
                        "score_confiance",
                        55,
                    )
                )
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        confiance_source = 55

    confiance_source = max(
        0,
        min(
            confiance_source,
            100,
        ),
    )

    # --------------------------------------------------------
    # NORMALISATION ENTRE PLATEFORMES
    # --------------------------------------------------------

    # 67behaviour :
    # on ne transforme jamais son prix bas
    # en preuve de fiabilité.
    if marketplace == "67behaviour":

        return min(
            confiance_source,
            65,
        )

    # Vinted
    if marketplace == "Vinted":

        return min(
            confiance_source,
            85,
        )

    # eBay :
    # le connecteur possède des infos vendeur
    # plus détaillées.
    if marketplace == "eBay":

        return min(
            confiance_source,
            95,
        )

    return min(
        confiance_source,
        80,
    )


# ============================================================
# ANALYSE UNIVERSELLE D'UN RESULTAT
# ============================================================

def _analyser_resultat_multi(
    annonce,
    query,
    prix_max,
):
    resultat = dict(
        annonce
    )

    marketplace = str(
        resultat.get(
            "marketplace"
        )
        or "Inconnu"
    ).strip()

    titre = str(
        resultat.get(
            "titre"
        )
        or ""
    ).strip()

    # --------------------------------------------------------
    # TEXTE GLOBAL
    # --------------------------------------------------------

    texte_global = " ".join(
        [
            titre,

            " ".join(
                str(x)
                for x in (
                    resultat.get(
                        "raisons"
                    )
                    or []
                )
            ),

            " ".join(
                str(x)
                for x in (
                    resultat.get(
                        "alertes"
                    )
                    or []
                )
            ),
        ]
    )

    tout_n = _normaliser_multi(
        texte_global
    )

    raisons = list(
        resultat.get(
            "raisons"
        )
        or []
    )

    alertes = list(
        resultat.get(
            "alertes"
        )
        or []
    )

    # ========================================================
    # 1. PERTINENCE
    # ========================================================

    type_recherche = (
        _detecter_type_multi(
            query
        )
    )

    if not _titre_correspond_multi(
        titre,
        query,
        type_recherche,
    ):
        return None

    mots_importants = (
        _mots_importants_multi(
            query,
            type_recherche,
        )
    )

    score_match = 90

    if type_recherche:

        score_match += 5

        raisons.append(
            "Type de produit correspondant"
        )

    if mots_importants:

        score_match += 5

        raisons.append(
            "Tous les mots importants sont présents dans le titre"
        )

    score_match = min(
        score_match,
        100,
    )

    # ========================================================
    # 2. PRIX
    # ========================================================

    prix = resultat.get(
        "prix"
    )

    prix_total = resultat.get(
        "prix_total"
    )

    try:

        prix_float = float(
            prix
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    try:

        prix_total_float = float(
            prix_total
        )

    except (
        TypeError,
        ValueError,
    ):

        prix_total_float = (
            prix_float
        )

    # Pour eBay, si les frais de port sont connus,
    # on classe sur le prix total.
    if marketplace == "eBay":

        prix_reference = (
            prix_total_float
        )

    else:

        prix_reference = (
            prix_float
        )

    # ========================================================
    # 3. CONFIANCE
    # ========================================================

    score_confiance = (
        _confiance_base_multi(
            resultat
        )
    )

    # --------------------------------------------------------
    # ANNONCES A IGNORER
    # --------------------------------------------------------

    termes_ignorer = [
        mot
        for mot
        in _MOTS_A_IGNORER_MULTI

        if _contient_expression_multi(
            tout_n,
            mot,
        )
    ]

    # --------------------------------------------------------
    # CONTREFAÇON / REPLIQUE
    # --------------------------------------------------------

    termes_veto = [
        mot
        for mot
        in _MOTS_VETO_MULTI

        if _contient_expression_multi(
            tout_n,
            mot,
        )
    ]

    if termes_ignorer:

        score_confiance = min(
            score_confiance,
            10,
        )

        for mot in termes_ignorer:

            alertes.append(
                f"Annonce à ignorer : {mot}"
            )

    if termes_veto:

        score_confiance = min(
            score_confiance,
            20,
        )

        for mot in termes_veto:

            alertes.append(
                f"Signal explicite suspect : {mot}"
            )

    # ========================================================
    # 4. SCORE PRIX
    # ========================================================

    score_affaire = (
        _calculer_score_affaire_multi(
            prix_reference,
            prix_max,
        )
    )

    try:

        plafond = float(
            prix_max
        )

    except (
        TypeError,
        ValueError,
    ):

        plafond = None

    # --------------------------------------------------------
    # PRIX ANORMALEMENT BAS
    # --------------------------------------------------------

    if (
        plafond
        and plafond > 0
    ):

        ratio_prix = (
            prix_reference
            / plafond
        )

        # Pour Vinted/eBay :
        # prix quasi nul = signal de risque.
        if (
            marketplace
            in {
                "Vinted",
                "eBay",
            }
            and ratio_prix <= 0.08
        ):

            score_confiance = max(
                0,
                score_confiance - 15,
            )

            # Le prix ultra bas ne peut plus
            # donner automatiquement 95/100.
            score_affaire = min(
                score_affaire,
                55,
            )

            alertes.append(
                "Prix anormalement bas par rapport au budget"
            )

        elif (
            marketplace
            in {
                "Vinted",
                "eBay",
            }
            and ratio_prix <= 0.15
        ):

            score_confiance = max(
                0,
                score_confiance - 5,
            )

            alertes.append(
                "Prix très bas : vérification recommandée"
            )

    # --------------------------------------------------------
    # 67BEHAVIOUR
    # --------------------------------------------------------

    if marketplace == "67behaviour":

        alertes.append(
            "Prix affiché hors éventuels frais de livraison, taxes ou import"
        )

    # ========================================================
    # 5. SCORE FINAL COMMUN
    # ========================================================
    #
    # 50 % pertinence
    # 35 % confiance
    # 15 % prix
    #
    # LE PRIX NE DOMINE PLUS LE CLASSEMENT.

    score = round(
        score_match * 0.50
        + score_confiance * 0.35
        + score_affaire * 0.15
    )

    # ========================================================
    # 6. VETO / CATEGORIE
    # ========================================================

    if termes_ignorer:

        score = min(
            score,
            10,
        )

        categorie = (
            "A IGNORER"
        )

    elif termes_veto:

        score = min(
            score,
            25,
        )

        categorie = (
            "DOUTEUSE"
        )

    elif score_confiance < 45:

        score = min(
            score,
            49,
        )

        categorie = (
            "DOUTEUSE"
        )

    elif score_confiance < 60:

        categorie = (
            "A VERIFIER"
        )

    elif (
        score >= 88
        and score_confiance >= 85
    ):

        categorie = (
            "EXCELLENTE AFFAIRE"
        )

    elif (
        score >= 72
        and score_confiance >= 60
    ):

        categorie = (
            "BONNE AFFAIRE"
        )

    elif score >= 55:

        categorie = (
            "INTERESSANTE"
        )

    else:

        categorie = (
            "A VERIFIER"
        )

    # ========================================================
    # 7. RESULTAT
    # ========================================================

    resultat[
        "score"
    ] = max(
        0,
        min(
            score,
            100,
        ),
    )

    resultat[
        "categorie"
    ] = categorie

    resultat[
        "score_match"
    ] = score_match

    resultat[
        "score_confiance"
    ] = max(
        0,
        min(
            round(
                score_confiance
            ),
            100,
        ),
    )

    resultat[
        "score_affaire"
    ] = max(
        0,
        min(
            round(
                score_affaire
            ),
            100,
        ),
    )

    # Supprimer les doublons dans les raisons/alertes.
    resultat[
        "raisons"
    ] = list(
        dict.fromkeys(
            raisons
        )
    )

    resultat[
        "alertes"
    ] = list(
        dict.fromkeys(
            alertes
        )
    )

    return resultat


# ============================================================
# RECHERCHE MULTI-MARKETPLACES
# ============================================================

def rechercher_multi_marketplaces(
    marque,
    prix_max,
    plateformes=None,
    limite=10,
):
    from marketplaces.connectors import (
        get_available_connectors,
        get_connector,
    )

    query = str(
        marque or ""
    ).strip()

    if not query:
        return []

    # --------------------------------------------------------
    # LIMITE
    # --------------------------------------------------------

    try:

        limite = int(
            limite
        )

    except (
        TypeError,
        ValueError,
    ):

        limite = 10

    limite = max(
        1,
        limite,
    )

    # --------------------------------------------------------
    # PRIX MAXIMUM
    # --------------------------------------------------------

    try:

        prix_max_float = float(
            prix_max
        )

    except (
        TypeError,
        ValueError,
    ):

        return []

    if prix_max_float <= 0:
        return []

    # --------------------------------------------------------
    # PLATEFORMES
    # --------------------------------------------------------

    if plateformes is None:

        plateformes = list(
            get_available_connectors().keys()
        )

    resultats_bruts = []

    # On récupère volontairement plus de produits
    # avant de faire le classement final.
    limite_par_plateforme = max(
        limite * 3,
        30,
    )

    # ========================================================
    # RECHERCHE SUR CHAQUE PLATEFORME
    # ========================================================

    for plateforme in plateformes:

        try:

            # ------------------------------------------------
            # VINTED
            # ------------------------------------------------

            if plateforme == "Vinted":

                annonces = rechercher_vinted(
                    marque=query,
                    prix_max=prix_max_float,
                    limite=limite_par_plateforme,
                    headless=False,
                )

                for annonce in annonces:

                    annonce = dict(
                        annonce
                    )

                    annonce[
                        "marketplace"
                    ] = "Vinted"

                    resultats_bruts.append(
                        annonce
                    )

                continue

            # ------------------------------------------------
            # AUTRES CONNECTEURS
            # ------------------------------------------------

            connector = get_connector(
                plateforme
            )

            if connector is None:

                print(
                    "[MULTI] "
                    f"Connecteur inconnu : {plateforme}"
                )

                continue

            if not getattr(
                connector,
                "enabled",
                True,
            ):

                print(
                    "[MULTI] "
                    f"{plateforme} désactivé"
                )

                continue

            annonces = connector.search(
                query=query,
                price_max=prix_max_float,
                limit=limite_par_plateforme,
            )

            for annonce in annonces:

                annonce = dict(
                    annonce
                )

                annonce.setdefault(
                    "marketplace",
                    plateforme,
                )

                resultats_bruts.append(
                    annonce
                )

        except Exception as e:

            print(
                "[MULTI] "
                f"Erreur {plateforme} : {e}"
            )

    # ========================================================
    # ANALYSE UNIVERSELLE
    # ========================================================

    resultats = []

    for annonce in resultats_bruts:

        analyse = (
            _analyser_resultat_multi(
                annonce,
                query=query,
                prix_max=prix_max_float,
            )
        )

        # Résultat hors sujet.
        if analyse is None:
            continue

        resultats.append(
            analyse
        )

    # ========================================================
    # ANTI-DOUBLONS GLOBAL
    # ========================================================

    uniques = []

    vus = set()

    for annonce in resultats:

        marketplace = str(
            annonce.get(
                "marketplace"
            )
            or ""
        )

        titre_n = (
            _normaliser_multi(
                annonce.get(
                    "titre"
                )
            )
        )

        try:

            prix_cle = round(
                float(
                    annonce.get(
                        "prix"
                    )
                ),
                2,
            )

        except (
            TypeError,
            ValueError,
        ):

            prix_cle = None

        lien = str(
            annonce.get(
                "lien"
            )
            or ""
        ).split(
            "?",
            1,
        )[0]

        if lien:

            cle = (
                marketplace.lower(),
                lien,
            )

        else:

            cle = (
                marketplace.lower(),
                titre_n,
                prix_cle,
            )

        if cle in vus:
            continue

        vus.add(
            cle
        )

        uniques.append(
            annonce
        )

    # ========================================================
    # CLASSEMENT GLOBAL DIVERSIFIE
    # ========================================================

    # On garde les vrais scores.
    # La diversification ne modifie que l'ordre d'affichage.
    #
    # Plus une plateforme a déjà de résultats sélectionnés,
    # plus elle reçoit un petit malus temporaire.
    #
    # Il n'y a aucun quota obligatoire :
    # une plateforme avec de mauvais résultats ne sera
    # jamais ajoutée juste pour remplir des places.

    selectionnes = []

    restants = list(
        uniques
    )

    compteur_plateformes = {}

    def prix_pour_tri(
        annonce
    ):
        valeur = annonce.get(
            "prix_total",
            annonce.get(
                "prix",
                999999,
            ),
        )

        try:
            return float(
                valeur
            )

        except (
            TypeError,
            ValueError,
        ):
            return 999999

    while (
        restants
        and len(
            selectionnes
        ) < limite
    ):

        def cle_diversifiee(
            annonce
        ):
            marketplace = str(
                annonce.get(
                    "marketplace"
                )
                or "Inconnu"
            )

            nombre_deja_present = (
                compteur_plateformes.get(
                    marketplace,
                    0,
                )
            )

            # Les 2 premiers résultats d'une plateforme
            # ne subissent aucun malus.
            #
            # Ensuite : -3 points temporaires par résultat
            # supplémentaire, avec un plafond de -15.
            penalite_diversite = min(
                max(
                    nombre_deja_present - 1,
                    0,
                ) * 3,
                15,
            )

            score_original = annonce.get(
                "score",
                0,
            )

            try:
                score_original = float(
                    score_original
                )

            except (
                TypeError,
                ValueError,
            ):
                score_original = 0

            score_diversifie = (
                score_original
                - penalite_diversite
            )

            confiance = annonce.get(
                "score_confiance",
                0,
            )

            try:
                confiance = float(
                    confiance
                )

            except (
                TypeError,
                ValueError,
            ):
                confiance = 0

            return (
                # La catégorie reste prioritaire.
                _PRIORITE_CATEGORIE_MULTI.get(
                    annonce.get(
                        "categorie"
                    ),
                    99,
                ),

                # Puis score avec petit malus de diversité.
                -score_diversifie,

                # Puis vrai score.
                -score_original,

                # Puis confiance.
                -confiance,

                # Puis prix.
                prix_pour_tri(
                    annonce
                ),
            )

        meilleur = min(
            restants,
            key=cle_diversifiee,
        )

        selectionnes.append(
            meilleur
        )

        marketplace = str(
            meilleur.get(
                "marketplace"
            )
            or "Inconnu"
        )

        compteur_plateformes[
            marketplace
        ] = (
            compteur_plateformes.get(
                marketplace,
                0,
            )
            + 1
        )

        restants.remove(
            meilleur
        )

    print(
        "[MULTI] "
        f"{len(resultats_bruts)} résultats bruts -> "
        f"{len(uniques)} résultats classés"
    )

    print(
        "[MULTI] Répartition TOP : "
        f"{compteur_plateformes}"
    )

    return selectionnes