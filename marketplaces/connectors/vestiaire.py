from .base import MarketplaceConnector


class VestiaireConnector(MarketplaceConnector):
    """
    Connecteur Vestiaire Collective pour LUXE RADAR.

    Etat actuel :
    - Connecteur integre au projet
    - Recherche desactivee tant qu'aucune methode d'acces
      compatible n'est configuree
    - Ne fait pas planter la recherche multi-marketplaces
    """

    name = "Vestiaire Collective"
    display_name = "Vestiaire Collective"
    enabled = False
    currency = "EUR"

    def __init__(self):
        self.enabled = False

    def is_available(self):
        """
        Indique si le connecteur peut actuellement
        effectuer des recherches.
        """
        return self.enabled

    def search(self, query, price_max=None, limit=20):
        """
        Recherche des annonces Vestiaire Collective.

        Tant que le connecteur n'est pas configure,
        retourne simplement une liste vide.
        """

        if not query or not str(query).strip():
            return []

        if limit <= 0:
            return []

        if not self.enabled:
            return []

        # FUTURE IMPLEMENTATION
        #
        # On ajoutera ici la methode de recherche
        # lorsqu'un acces compatible sera configure.
        #
        # Les resultats devront contenir :
        # - titre
        # - prix
        # - image
        # - lien
        # - marque
        # - modele
        # - etat
        # - vendeur

        return []

    def normalize_result(
        self,
        title,
        price,
        url,
        image=None,
        brand=None,
        model=None,
        condition=None,
        seller=None,
    ):
        """
        Normalise un resultat Vestiaire Collective
        pour LUXE RADAR.
        """

        return {
            "marketplace": self.name,
            "titre": title,
            "prix": price,
            "devise": self.currency,
            "lien": url,
            "image": image,
            "marque": brand,
            "modele": model,
            "etat": condition,
            "vendeur": seller,
        }