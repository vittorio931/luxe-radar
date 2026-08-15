from radar_engine import (
    _analyser_resultat_multi,
    _detecter_type_multi,
    _titre_correspond_multi,
)
from marketplaces.connectors.aliexpress import _extraire_items, _produit_depuis_item


def verifier(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    # 1) Requête normale
    verifier(
        _titre_correspond_multi(
            "Nike Dri-FIT Trail Running T-Shirt",
            "Nike Trail",
            None,
        ),
        "Nike Trail valide rejeté",
    )

    # 2) Faux positif NBA
    verifier(
        not _titre_correspond_multi(
            "Short Nike Portland Trail Blazers Statement Edition",
            "Nike Trail",
            None,
        ),
        "Trail Blazers devrait être rejeté",
    )

    # 3) Type demandé
    type_pantalon = _detecter_type_multi("Pantalon Nike Trail")
    verifier(
        type_pantalon == "pantalon",
        "Type pantalon non détecté",
    )

    verifier(
        not _titre_correspond_multi(
            "Nike Trail socks",
            "Pantalon Nike Trail",
            type_pantalon,
        ),
        "Des chaussettes ne doivent pas passer pour un pantalon",
    )

    # 4) Annonce à ignorer
    ignore = _analyser_resultat_multi(
        {
            "marketplace": "Vinted",
            "titre": "ne pas achete veste nike trail panda taille m",
            "prix": 1,
            "score_confiance": 78,
            "image": "https://example.com/image.jpg",
        },
        query="Nike Trail",
        prix_max=50,
    )

    verifier(
        ignore is not None and ignore["categorie"] == "A IGNORER",
        "L'annonce 'ne pas acheter' doit être A IGNORER",
    )

    # 5) Prix à 1 € : pas une bonne affaire automatique
    prix_extreme = _analyser_resultat_multi(
        {
            "marketplace": "Vinted",
            "titre": "veste nike trail noir",
            "prix": 1,
            "score_confiance": 78,
            "image": "https://example.com/image.jpg",
        },
        query="Nike Trail",
        prix_max=50,
    )

    verifier(
        prix_extreme is not None
        and prix_extreme["categorie"] == "A VERIFIER",
        "Une annonce Vinted à 1 € doit être A VERIFIER",
    )

    # 6) 67behaviour : frais potentiels visibles dans les alertes
    shop = _analyser_resultat_multi(
        {
            "marketplace": "67behaviour",
            "titre": "Nike Dri-FIT Trail Running T-Shirt",
            "prix": 7.98,
            "score_confiance": 65,
            "image": "https://example.com/image.jpg",
        },
        query="Nike Trail",
        prix_max=50,
    )

    verifier(
        shop is not None
        and any(
            "frais" in alerte.lower()
            for alerte in shop["alertes"]
        ),
        "L'avertissement frais/import 67behaviour manque",
    )


    # 7) Ensembles : plusieurs formulations doivent être reconnues.
    type_ensemble = _detecter_type_multi("ensemble Essantials")
    verifier(type_ensemble == "ensemble", "Type ensemble non détecté")
    verifier(
        _titre_correspond_multi(
            "Fear of God Essentials tracksuit hoodie sweatpants",
            "ensemble Essantials",
            type_ensemble,
        ),
        "Ensemble Fear of God Essentials valide rejeté",
    )
    verifier(
        not _titre_correspond_multi(
            "adidas Originals Essentials tracksuit",
            "ensemble Essentials",
            type_ensemble,
        ),
        "adidas Essentials ne doit pas passer pour Fear of God Essentials",
    )

    # 8) AliExpress : fallback JSON imbriqué + image sous forme de chaîne.
    html = 'init-data-start { data: {"moved":{"products":[{"productId":"123","productTitle":"Nike Trail test","price":{"value":"49.90"},"currencyCode":"EUR","image":"//ae01.alicdn.com/test.jpg"},{"productId":"124","productTitle":"Nike Trail test 2","price":{"value":"39.90"},"currencyCode":"EUR"},{"productId":"125","productTitle":"Nike Trail test 3","price":{"value":"29.90"},"currencyCode":"EUR"}]}}} init-data-end'
    ali_items = _extraire_items(html)
    verifier(ali_items is not None and len(ali_items) == 3, "Fallback AliExpress imbriqué cassé")
    ali_product = _produit_depuis_item(ali_items[0])
    verifier(ali_product is not None and ali_product["prix"] == 49.9, "Prix AliExpress fallback invalide")
    verifier(ali_product["image"] == "https://ae01.alicdn.com/test.jpg", "Image AliExpress chaîne invalide")

    print("OK - Tous les tests radar_engine sont passes.")


if __name__ == "__main__":
    main()
