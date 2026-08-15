# Interface commune pour les connecteurs marketplace.
#
# Chaque plateforme devra implémenter cette interface avant d'être
# activée dans la collecte réelle.
#
# Le modèle de capacités (V4) décrit, pour chaque source :
#   - supports_pagination      : le connecteur sait parcourir plusieurs pages
#                                de recherche de façon fiable ;
#   - search_page              : collecte une page précise de résultats ;
#   - expansion_page_size      : nombre d'annonces demandées par page lors du
#                                deep search progressif ;
#   - expansion_recall_cap     : plafond d'annonces à collecter par source
#                                (évite les boucles infinies) ;
#   - max_pages                : pages max à parcourir (0 = illimité) ;
#   - empty_pages_threshold    : pages consécutives sans nouveau résultat
#                                avant d'arrêter la progression ;
#   - cooldown_seconds         : pause entre deux requêtes consécutives
#                                (respect des sites, jamais de contournement) ;
#   - health                   : état de configuration de la source.
#
# Ces valeurs sont des réglages par défaut ; chaque connecteur peut les
# surcharger. Elles complètent les constantes EXPAND_* de app_web.py sans
# les remplacer (le moteur préfère la capacité du connecteur quand elle
# existe, et retombe sur les constantes sinon).

class MarketplaceConnector:
    name = "Marketplace"
    enabled = True

    # Capacités de pagination / deep search (V4)
    supports_pagination = False
    expansion_page_size = 50
    expansion_recall_cap = 100
    max_pages = 0
    empty_pages_threshold = 2
    cooldown_seconds = 0

    def search(self, query, price_max, limit=20):
        raise NotImplementedError(
            f"Le connecteur {self.name} n'est pas encore implémenté."
        )

    def search_page(self, query, price_max=None, limit=20, page=1):
        # Défaut : pas de pagination native -> la page 1 est recherchée.
        return self.search(query, price_max=price_max, limit=limit)

    def health(self):
        # État de configuration (pas une mesure réseau). Les connecteurs qui
        # peuvent vérifier leur accessibilité réelle peuvent la surcharger.
        return {
            "ok": bool(getattr(self, "enabled", True)),
            "reachable": None,
            "supports_pagination": bool(getattr(self, "supports_pagination", False)),
        }

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
