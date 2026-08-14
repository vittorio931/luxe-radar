from __future__ import annotations

from .base import MarketplaceConnector


class HacooConnector(MarketplaceConnector):
    """Connecteur réservé à une future recherche produit publique Hacoo.

    Hacoo expose actuellement surtout un parcours d'achat via son application.
    LUXE RADAR n'utilise ni API privée, ni session connectée, ni contournement
    d'un contrôle d'accès. Le site reste donc catalogué comme expérimental sans
    être présenté comme source active tant qu'une recherche web publique et
    stable n'est pas disponible.
    """

    name = "Hacoo"
    display_name = "Hacoo"
    enabled = False
    currency = "EUR"

    def search(self, query, price_max=None, limit=20):
        return []
