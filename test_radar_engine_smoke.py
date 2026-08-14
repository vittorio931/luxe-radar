from radar_engine import (
    _analyser_resultat_multi,
    _detecter_type_multi,
    _titre_correspond_multi,
)


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

    print("OK - Tous les tests radar_engine sont passes.")


if __name__ == "__main__":
    main()
