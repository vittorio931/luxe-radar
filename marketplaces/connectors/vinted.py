from marketplaces.connectors.base import MarketplaceConnector


class VintedConnector(MarketplaceConnector):
    name = "Vinted"

    supports_pagination = True
    expansion_page_size = 50
    expansion_recall_cap = 150
    max_pages = 4
    empty_pages_threshold = 3
    cooldown_seconds = 0.5

    def search(self, query, price_max, limit=20, page=1):
        from radar_engine import rechercher_vinted

        annonces = rechercher_vinted(
            query,
            price_max,
            limite=limit,
            headless=True,
            page=page,
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

    def search_page(self, query, price_max=None, limit=20, page=1):
        return self.search(query=query, price_max=price_max, limit=limit, page=page)
