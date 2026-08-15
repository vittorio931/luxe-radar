"""Tests hors réseau V2.8.2 : pertinence source-aware + rapidité Grailed."""
from radar_engine import _titre_correspond_multi


def test_v282_source_aware():
    q = "ensemble Essentials"

    # ASOS / mode strict : les coffrets non vestimentaires restent rejetés.
    assert not _titre_correspond_multi(
        "Horace Skincare Essentials Gift Set", q, "ensemble", marketplace="ASOS"
    )
    assert not _titre_correspond_multi(
        "Real Techniques Artist Essentials Brush Set", q, "ensemble", marketplace="ASOS"
    )

    # eBay : le connecteur a déjà appliqué son filtre de type ; le filtre global
    # ne doit plus supprimer arbitrairement ces titres valides.
    assert _titre_correspond_multi(
        "Fear of God Essentials Men's Set Black", q, "ensemble", marketplace="eBay"
    )
    assert _titre_correspond_multi(
        "Essentials Men's Hoodie Jogger Set", q, "ensemble", marketplace="eBay"
    )
    assert not _titre_correspond_multi(
        "Essentials Skincare Gift Set", q, "ensemble", marketplace="eBay"
    )

    # AliExpress/DHgate : vocabulaire SEO fréquent.
    for source, title in (
        ("AliExpress", "Essentials Men Sportswear Outfit Hoodie Pants Streetwear"),
        ("AliExpress", "FG Essentials 2PC Hoodie Pants Men"),
        ("DHgate", "FOG Essentials Sweatsuit Hoodie Trousers"),
        ("DHgate", "Fear of God Essentials Hoodie Pants Outfit"),
    ):
        assert _titre_correspond_multi(title, q, "ensemble", marketplace=source), (source, title)

    # Marques concurrentes restent bloquées.
    assert not _titre_correspond_multi(
        "adidas Originals Essentials Tracksuit Set", q, "ensemble", marketplace="eBay"
    )


if __name__ == "__main__":
    test_v282_source_aware()
    print("OK - V2.8.2 pertinence source-aware validée.")
