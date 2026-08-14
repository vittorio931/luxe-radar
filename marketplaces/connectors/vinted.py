from marketplaces.connectors.base import MarketplaceConnector


class VintedConnector(MarketplaceConnector):
    name = "Vinted"

    def search(self, query, price_max, limit=20):
        from radar_engine import rechercher_vinted

        annonces = rechercher_vinted(
            query,
            price_max,
            limite=limit,
            headless=True,
        )

        resultats = []

        for annonce in annonces or []:
            item = dict(annonce)

            # On conserve EXACTEMENT les champs du moteur Vinted.
            item["marketplace"] = "Vinted"
            item["plateforme"] = "Vinted"

            # Compatibilité avec l'interface commune.
            item.setdefault("title", item.get("titre", ""))
            item.setdefault("price", item.get("prix"))
            item.setdefault("url", item.get("lien"))
            item.setdefault("image", item.get("photo"))
            item.setdefault("brand", item.get("marque"))
            item.setdefault("seller", item.get("vendeur"))

            resultats.append(item)

        return resultats