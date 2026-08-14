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

MOTS_EXCLUS_FORTS = {
    "replica", "replique", "réplique", "fake", "faux", "fausse",
    "counterfeit", "contrefacon", "contrefaçon", "1:1",
    "mirror quality", "super fake", "aaa quality",
}

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
    "comme neuf", "very good condition",
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

    texte = texte.replace("-", " ")
    texte = texte.replace("_", " ")

    texte = re.sub(
        r"[^\w€\s./]",
        " ",
        texte
    )

    texte = re.sub(
        r"\s+",
        " ",
        texte
    )

    return texte.strip()


def contient_mot(texte, mot):
    """
    Recherche par mot complet afin d'éviter certains faux positifs.
    Exemple : 'ua' ne doit pas être détecté dans un autre mot.
    """

    texte = nettoyer_texte(texte)
    mot = nettoyer_texte(mot)

    if not texte or not mot:
        return False

    pattern = (
        r"(?<!\w)"
        + re.escape(mot)
        + r"(?!\w)"
    )

    return re.search(
        pattern,
        texte,
        flags=re.IGNORECASE
    ) is not None


def trouver_termes(texte, dictionnaire):
    return [
        mot
        for mot in dictionnaire
        if contient_mot(texte, mot)
    ]


# ============================================================
# PRIX
# ============================================================

def extraire_prix(texte):
    if not texte:
        return None

    texte = str(texte)

    motifs = [
        r"(\d{1,4}(?:[,.]\d{1,2})?)\s*€",
        r"€\s*(\d{1,4}(?:[,.]\d{1,2})?)",
    ]

    prix_trouves = []

    for motif in motifs:
        prix_trouves.extend(
            re.findall(motif, texte)
        )

    for valeur in prix_trouves:
        try:
            prix = float(
                valeur.replace(",", ".")
            )

            if 0 < prix < 100000:
                return prix

        except (ValueError, TypeError):
            continue

    return None


# ============================================================
# MODELES
# ============================================================

def trouver_modele(marque, texte):
    texte = nettoyer_texte(texte)

    modeles = MARQUES_MODELES.get(
        marque,
        {}
    )

    candidats = []

    for modele, variantes in modeles.items():

        for variante in variantes:

            variante_propre = nettoyer_texte(
                variante
            )

            if variante_propre:
                candidats.append(
                    (
                        len(variante_propre),
                        modele,
                        variante_propre
                    )
                )

    candidats.sort(
        reverse=True
    )

    for _, modele, variante_propre in candidats:

        if contient_mot(
            texte,
            variante_propre
        ):
            return modele

    return None


def marque_presente(marque, texte):
    return contient_mot(
        texte,
        marque
    )


# ============================================================
# SCORE DE CONFIANCE
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
    Score de confiance de l'annonce.

    0-39   = très douteux
    40-59  = faible
    60-74  = moyen
    75-89  = bon
    90-100 = très bon

    IMPORTANT :
    Un prix bas ne fait PAS automatiquement monter
    la confiance.
    """

    titre_n = nettoyer_texte(
        titre
    )

    texte_n = nettoyer_texte(
        texte
    )

    tout = (
        f"{titre_n} {texte_n}"
    ).strip()

    confiance = 55

    alertes = []
    raisons = []

    # --------------------------------------------------------
    # MARQUE
    # --------------------------------------------------------

    if marque_presente(
        marque,
        titre_n
    ):
        confiance += 15

        raisons.append(
            "Marque clairement présente dans le titre"
        )

    elif marque_presente(
        marque,
        texte_n
    ):
        confiance += 7

        raisons.append(
            "Marque détectée dans l'annonce"
        )

    else:
        confiance -= 20

        alertes.append(
            "Marque non clairement identifiable"
        )

    # --------------------------------------------------------
    # MODELE
    # --------------------------------------------------------

    if modele:

        confiance += 12

        raisons.append(
            f"Modèle détecté : {modele}"
        )

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    mots_description = len(
        texte_n.split()
    )

    if mots_description >= 25:

        confiance += 7

        raisons.append(
            "Description suffisamment détaillée"
        )

    elif mots_description >= 10:

        confiance += 3

    elif mots_description <= 3:

        confiance -= 8

        alertes.append(
            "Description très pauvre"
        )

    # --------------------------------------------------------
    # PRIX TRES BAS
    # --------------------------------------------------------

    if prix is not None and prix <= 10:

        confiance -= 4

        alertes.append(
            "Prix exceptionnellement bas"
        )

    # --------------------------------------------------------
    # PHOTO
    # --------------------------------------------------------

    if image:

        confiance += 5

        raisons.append(
            "Photo disponible"
        )

    else:

        confiance -= 8

        alertes.append(
            "Aucune photo détectée"
        )

    # --------------------------------------------------------
    # ETAT
    # --------------------------------------------------------

    if trouver_termes(
        tout,
        MOTS_ETAT
    ):

        confiance += 4

        raisons.append(
            "État renseigné"
        )

    # --------------------------------------------------------
    # SIGNAUX SUSPECTS FORTS
    # --------------------------------------------------------

    forts = trouver_termes(
        tout,
        MOTS_DOUTEUX_FORTS
    )

    moyens = trouver_termes(
        tout,
        MOTS_DOUTEUX_MOYENS
    )

    if forts:

        malus = min(
            65,
            32 + (len(forts) - 1) * 10
        )

        confiance -= malus

        for mot in forts:

            alertes.append(
                f"Signal suspect fort : {mot}"
            )

    # --------------------------------------------------------
    # SIGNAUX AMBIGUS
    # --------------------------------------------------------

    if moyens:

        confiance -= min(
            25,
            len(moyens) * 8
        )

        for mot in moyens:

            alertes.append(
                f"Signal ambigu : {mot}"
            )

    # --------------------------------------------------------
    # SUSPECT DIRECTEMENT DANS LE TITRE
    # --------------------------------------------------------

    forts_titre = trouver_termes(
        titre_n,
        MOTS_DOUTEUX_FORTS
    )

    if forts_titre:

        confiance -= 10

        alertes.append(
            "Terme suspect présent directement dans le titre"
        )

    confiance = max(
        0,
        min(
            int(confiance),
            100
        )
    )

    return (
        confiance,
        raisons,
        alertes
    )


# ============================================================
# SCORE D'AFFAIRE
# ============================================================

def calculer_score_affaire(
    prix,
    prix_max
):
    """
    Score uniquement basé sur le prix.

    Ce score ne dit PAS si l'annonce est fiable.
    """

    if prix is None:

        return (
            0,
            "Prix non disponible"
        )

    if prix_max is None or prix_max <= 0:

        return (
            50,
            "Prix disponible"
        )

    ratio = prix / float(
        prix_max
    )

    if ratio <= 0.35:

        return (
            100,
            "Prix exceptionnellement bas"
        )

    if ratio <= 0.50:

        return (
            92,
            "Prix très intéressant"
        )

    if ratio <= 0.65:

        return (
            82,
            "Très bon prix"
        )

    if ratio <= 0.80:

        return (
            70,
            "Bon prix"
        )

    if ratio <= 0.95:

        return (
            58,
            "Prix intéressant"
        )

    return (
        45,
        "Proche du plafond"
    )


# ============================================================
# ANALYSE COMPLETE D'UNE ANNONCE
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

    titre_n = nettoyer_texte(
        titre
    )

    texte_n = nettoyer_texte(
        texte
    )

    tout = (
        f"{titre_n} {texte_n}"
    ).strip()

    raisons = []
    alertes = []

    # ========================================================
    # EXCLUSIONS
    # ========================================================

    exclus = trouver_termes(
        titre_n,
        MOTS_EXCLUS
    )

    if exclus:

        alertes.extend(
            [
                f"Article/accessoire exclu : {mot}"
                for mot in exclus
            ]
        )

    # ========================================================
    # VETO CONTREFAÇON
    # ========================================================

    veto = trouver_termes(
        tout,
        MOTS_VETO
    )

    # ========================================================
    # SCORE DE MATCH
    # ========================================================

    match = 45

    if marque_presente(
        marque,
        titre_n
    ):

        match += 25

        raisons.append(
            "Marque présente dans le titre"
        )

    elif marque_presente(
        marque,
        texte_n
    ):

        match += 12

        raisons.append(
            "Marque détectée dans la description"
        )

    else:

        match -= 20

        alertes.append(
            "Marque absente ou non confirmée"
        )

    # --------------------------------------------------------
    # MODELE
    # --------------------------------------------------------

    if modele:

        match += 20

        raisons.append(
            f"Modèle correspondant : {modele}"
        )

    # --------------------------------------------------------
    # TYPE ARTICLE
    # --------------------------------------------------------

    if trouver_termes(
        titre_n,
        MOTS_VETEMENT
    ):

        match += 5

        raisons.append(
            "Type de vêtement identifiable"
        )

    match = max(
        0,
        min(
            match,
            100
        )
    )

    # ========================================================
    # SCORE CONFIANCE
    # ========================================================

    (
        confiance,
        raisons_confiance,
        alertes_confiance
    ) = calculer_confiance(
        marque=marque,
        titre=titre,
        texte=texte,
        prix=prix,
        modele=modele,
        image=image,
    )

    raisons.extend(
        raisons_confiance
    )

    alertes.extend(
        alertes_confiance
    )

    # ========================================================
    # SCORE PRIX
    # ========================================================

    (
        score_affaire,
        raison_prix
    ) = calculer_score_affaire(
        prix,
        prix_max
    )

    raisons.append(
        raison_prix
    )

    # ========================================================
    # SCORE FINAL
    # ========================================================

    # Pertinence : 45 %
    # Confiance  : 35 %
    # Prix       : 20 %

    score = (
        match * 0.45
        + confiance * 0.35
        + score_affaire * 0.20
    )

    # ========================================================
    # VETO
    # ========================================================

    if veto:

        score = min(
            score,
            39
        )

        alertes.append(
            "VETO : signal explicite de contrefaçon/réplique"
        )

    # ========================================================
    # ARTICLE EXCLU
    # ========================================================

    if exclus:

        score = min(
            score,
            20
        )

    # ========================================================
    # CONFIANCE TROP FAIBLE
    # ========================================================

    if confiance < 45:

        score = min(
            score,
            49
        )

    score = max(
        0,
        min(
            round(score),
            100
        )
    )

    # ========================================================
    # CATEGORIE
    # ========================================================

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

    # ========================================================
    # NETTOYAGE
    # ========================================================

    raisons = list(
        dict.fromkeys(raisons)
    )

    alertes = list(
        dict.fromkeys(alertes)
    )

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

            page.wait_for_timeout(
                5000
            )

            liens = page.locator(
                'a[href*="/items/"]'
            )

            nombre_liens = liens.count()

            print(
                f"{nombre_liens} liens analyses"
            )

            for i in range(
                nombre_liens
            ):

                lien = liens.nth(i)

                try:

                    href = lien.get_attribute(
                        "href"
                    )

                except Exception:

                    continue

                if not href:
                    continue

                if "/items/" not in href:
                    continue

                if href.startswith("/"):

                    href = (
                        "https://www.vinted.fr"
                        + href
                    )

                href = href.split(
                    "?",
                    1
                )[0]

                if href in vus:
                    continue

                vus.add(href)

                # =================================================
                # BLOC ANNONCE
                # =================================================

                try:

                    bloc = lien.locator(
                        "xpath=../../.."
                    )

                    texte_annonce = (
                        bloc.inner_text(
                            timeout=2000
                        )
                    )

                except Exception:

                    bloc = lien

                    texte_annonce = ""

                texte_annonce = " ".join(
                    str(
                        texte_annonce
                    ).split()
                )

                # =================================================
                # TITRE
                # =================================================

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

                titre = titre.replace(
                    "-",
                    " "
                )

                titre = " ".join(
                    titre.split()
                )

                titre_lower = nettoyer_texte(
                    titre
                )

                texte_lower = nettoyer_texte(
                    texte_annonce
                )

                # =================================================
                # MARQUE
                # =================================================

                if not (
                    marque_presente(
                        marque,
                        titre_lower
                    )
                    or
                    marque_presente(
                        marque,
                        texte_lower
                    )
                ):

                    continue

                # =================================================
                # EXCLUSIONS RAPIDES
                # =================================================

                if trouver_termes(
                    titre_lower,
                    MOTS_EXCLUS
                ):

                    continue

                # =================================================
                # PRIX
                # =================================================

                prix = extraire_prix(
                    texte_annonce
                )

                if prix is None:
                    continue

                if prix > float(
                    prix_max
                ):

                    continue

                # =================================================
                # MODELE
                # =================================================

                modele = trouver_modele(
                    marque,
                    f"{titre_lower} {texte_lower}"
                )

                # =================================================
                # IMAGE
                # =================================================

                image = None

                try:

                    img = bloc.locator(
                        "img"
                    ).first

                    if img.count() > 0:

                        image = (
                            img.get_attribute(
                                "src"
                            )
                            or
                            img.get_attribute(
                                "data-src"
                            )
                            or
                            img.get_attribute(
                                "srcset"
                            )
                        )

                except Exception:

                    image = None

                # =================================================
                # ANALYSE
                # =================================================

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

                if (
                    modele
                    and
                    modele.lower()
                    not in titre_lower
                ):

                    titre_affiche = (
                        f"{titre} - {modele}"
                    )

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
                    f"confiance "
                    f"{analyse['score_confiance']}/100"
                )

                # On collecte plusieurs annonces avant
                # de sélectionner les meilleures.
                if len(annonces) >= max(
                    limite * 5,
                    30
                ):

                    break

        except Exception as e:

            print(
                f"Erreur : {e}"
            )

        finally:

            browser.close()

    # ============================================================
    # CLASSEMENT FINAL
    # ============================================================

    # Les annonces douteuses sont volontairement
    # repoussées derrière les annonces fiables.

    annonces.sort(
        key=lambda x: (
            x["categorie"]
            in {
                "DOUTEUSE",
                "A IGNORER"
            },
            -x["score"],
            -x["score_confiance"],
            x["prix"],
        )
    )

    return annonces[:limite]