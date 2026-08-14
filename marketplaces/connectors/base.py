# Interface commune pour les connecteurs marketplace.
#
# Chaque plateforme devra implémenter cette interface avant d'être
# activée dans la collecte réelle.

class MarketplaceConnector:
    name = "Marketplace"

    def search(self, query, price_max, limit=20):
        raise NotImplementedError(
            f"Le connecteur {self.name} n'est pas encore implémenté."
        )

    @staticmethod
    def normalize(item):
        return {
            "marketplace": item.get("marketplace"),
            "title": item.get("title", ""),
            "brand": item.get("brand"),
            "model": item.get("model"),
            "price": item.get("price"),
            "shipping": item.get("shipping"),
            "condition": item.get("condition"),
            "size": item.get("size"),
            "image": item.get("image"),
            "url": item.get("url"),
            "seller": item.get("seller"),
            "seller_rating": item.get("seller_rating"),
        }
