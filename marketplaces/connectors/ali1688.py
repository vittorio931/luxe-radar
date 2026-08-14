from .base import MarketplaceConnector


class Ali1688Connector(MarketplaceConnector):
    """
    Connecteur pour la marketplace 1688.

    État actuel :
    - Connecteur enregistré dans LUXE RADAR
    - Recherche désactivée tant qu'aucun accès compatible
      à 1688 n'est configuré
    - Peut être activé plus tard sans modifier le reste du radar
    """

    name = "1688"
    display_name = "1688"
    enabled = False
    currency = "CNY"

    def __init__(self):
        self.enabled = False

    def is_available(self):
        """
        Indique au radar si le connecteur peut actuellement
        effectuer des recherches.
        """
        return self.enabled

    def search(self, query, price_max=None, limit=20):
        """
        Recherche des produits sur 1688.

        Cette méthode est volontairement inactive tant que
        l'accès nécessaire à 1688 n'est pas configuré.

        Parameters
        ----------
        query : str
            Produit, marque ou modèle recherché.

        price_max : float | None
            Prix maximum souhaité.

        limit : int
            Nombre maximum de résultats.

        Returns
        -------
        list
            Liste normalisée de résultats lorsque le connecteur
            sera activé.
        """

        if not query or not str(query).strip():
            return []

        if limit <= 0:
            return []

        if not self.enabled:
            return []

        # -------------------------------------------------
        # FUTURE IMPLEMENTATION 1688
        # -------------------------------------------------
        #
        # 1. Envoyer la recherche à 1688
        # 2. Récupérer les produits
        # 3. Extraire :
        #       - titre
        #       - prix
        #       - image
        #       - vendeur
        #       - lien
        #       - quantité minimale (MOQ)
        #
        # 4. Convertir chaque résultat vers le format
        #    commun utilisé par LUXE RADAR.
        #
        # -------------------------------------------------

        return []

    def normalize_result(
        self,
        title,
        price,
        url,
        image=None,
        seller=None,
        moq=None,
    ):
        """
        Prépare un résultat 1688 dans un format standardisé.
        """

        return {
            "marketplace": self.name,
            "titre": title,
            "prix": price,
            "devise": self.currency,
            "lien": url,
            "image": image,
            "vendeur": seller,
            "moq": moq,
        }