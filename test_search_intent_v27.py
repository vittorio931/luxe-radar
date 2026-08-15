"""Tests hors réseau V2.7 : ensembles + Fear of God ESSENTIALS."""

from radar_engine import _detecter_type_multi, _titre_correspond_multi
from marketplaces.connectors import asos, cdiscount, ebay, grailed


def main():
    # Moteur global : variantes FR/EN et faute courante.
    for query in (
        "ensemble Essentials",
        "ensemble Essantials",
        "Essentials tracksuit",
        "Fear of God Essentials set",
        "Essentials co-ord",
    ):
        assert _detecter_type_multi(query) == "ensemble", query

    assert _titre_correspond_multi(
        "Fear of God Essentials matching set hoodie sweatpants",
        "ensemble Essentials",
        "ensemble",
    )
    # Les vendeurs n'écrivent pas toujours « ensemble » : deux pièces
    # explicites (haut + bas) doivent tout de même être reconnues.
    assert _titre_correspond_multi(
        "Fear of God Essentials hoodie + joggers beige",
        "ensemble Essentials",
        "ensemble",
    )
    assert _titre_correspond_multi(
        "ESSENTIALS 2PCS hoodie sweatpants grey",
        "ensemble Essantials",
        "ensemble",
    )
    assert not _titre_correspond_multi(
        "adidas Originals Essentials tracksuit",
        "ensemble Essentials",
        "ensemble",
    )

    # ASOS : intention + titre réel issu d'un alt d'image.
    assert asos._detecter_type_recherche("ensemble Essantials")[0] == "ensemble"
    assert asos._score_pertinence_titre(
        "Fear of God Essentials - Ensemble hoodie et pantalon",
        "ensemble Essentials",
    )[1]
    assert not asos._score_pertinence_titre(
        "ASOS DESIGN Essentials - Ensemble sweat et pantalon",
        "ensemble Essentials",
    )[1]
    assert asos._score_pertinence_titre(
        "Fear of God Essentials hoodie & joggers",
        "ensemble Essentials",
    )[1]

    # Cdiscount : syntaxe réellement utilisée par les pages publiques indexées.
    assert "r-nike%2Btrail.html" in cdiscount._candidate_routes("Nike Trail")[0]
    assert any(
        "fear of god essentials" in cdiscount._norm(v)
        for v in cdiscount._query_variants("ensemble Essantials")
    )
    assert cdiscount._score_title(
        "Fear of God Essentials hoodie + joggers 2 pièces",
        "ensemble Essentials",
    )[1]
    assert not cdiscount._score_title(
        "adidas Essentials hoodie + joggers",
        "ensemble Essentials",
    )[1]

    # eBay : requête large possible, filtre local strict sur le type + la marque.
    ebay_type = ebay.detecter_type_recherche("ensemble Essentials")
    assert ebay_type == "ensemble"
    assert ebay.titre_correspond_recherche(
        "Fear of God Essentials Tracksuit Set",
        "ensemble Essentials",
        ebay_type,
    )
    assert ebay.titre_correspond_recherche(
        "Fear of God Essentials hoodie + joggers",
        "ensemble Essentials",
        ebay_type,
    )
    assert not ebay.titre_correspond_recherche(
        "adidas Essentials Tracksuit",
        "ensemble Essentials",
        ebay_type,
    )

    # Grailed : slugs ciblés anglais utiles pour les vendeurs internationaux.
    grailed_type = grailed.detecter_type_recherche("ensemble Essantials")
    assert grailed_type == "ensemble"
    slugs = grailed.generer_slugs_browse("ensemble Essantials")
    assert "essentials-tracksuit" in slugs or "essentials-set" in slugs
    assert grailed.titre_correspond_recherche(
        "Fear Of God Essentials Matching Set Hoodie Sweatpants",
        "ensemble Essentials",
        grailed_type,
    )
    assert grailed.titre_correspond_recherche(
        "Fear Of God Essentials hoodie + joggers",
        "ensemble Essentials",
        grailed_type,
    )

    print("OK - V2.7 ensembles + ESSENTIALS cohérents sur moteur, ASOS, Cdiscount, eBay et Grailed.")


if __name__ == "__main__":
    main()
