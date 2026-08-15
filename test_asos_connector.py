"""Tests hors réseau du connecteur ASOS V2.6."""

from marketplaces.connectors import asos


FR_HTML = r'''
<html><body>
<a aria-label="Nike Trail - T-shirt de course, Prix initial 59,99 € Prix actuel 34,50 €" href="/fr/nike-running/nike-trail-shirt/prd/206103842#colourWayId-1"></a>
<a href="/fr/nike-running/nike-trail-sweat/prd/206103797#colourWayId-2" aria-label="Nike Trail - Sweat de course, Maintenant 38,00 €"></a>
<a href="/fr/nike-running/nike-trail-expensive/prd/999999999" aria-label="Nike Trail - Veste premium, Prix actuel 95,00 €"></a>
<a href="/fr/nike-running/nike-miler/prd/111111111" aria-label="Nike Running - Miler - T-shirt, Prix actuel 29,99 €"></a>
</body></html>
'''

GB_HTML = r'''
<html><body>
<a href="https://www.asos.com/nike-running/nike-trail-shirt/prd/206103842#colourWayId-9" aria-label="Nike Trail shirt, current price £30.00"></a>
<a aria-label="Nike Trail Running Shield jacket, Original price £119.99 current price £40.00" href="https://www.asos.com/nike-running/shield/prd/24319060#colourWayId-2"></a>
<a href="https://www.asos.com/nike-running/general/prd/222222222" aria-label="Nike Running general tee, current price £20.00"></a>
</body></html>
'''


LIVEISH_HTML = r"""
<html><body>
<a href="/fr/nike-running/nike-trail-live/prd/333333333" aria-label="Prix actuel 34,50 €">
  <picture><img src="https://images.asos-media.com/products/nike/333.jpg" alt="Nike Trail - T-shirt de course en tissu Dri-FIT - Blanc"></picture>
  <span>Voir le produit</span>
</a>
<a href="/fr/adidas-originals/adidas-essentials/prd/444444444" aria-label="Prix actuel 39,99 €">
  <img alt="adidas Originals - Essentials - Ensemble - Noir">
</a>
<a href="/fr/fear-of-god/fog-essentials-set/prd/555555555" aria-label="Prix actuel 45,00 €">
  <img alt="Fear of God Essentials - Ensemble hoodie et pantalon - Gris">
</a>
</body></html>
"""


def main():
    assert asos._prix_et_devise_depuis_label(
        "Nike Trail, Prix initial 59,99 € Prix actuel 34,50 €"
    ) == (34.5, "EUR")
    assert asos._prix_et_devise_depuis_label(
        "Nike Trail, Original price £119.99 current price £48.00"
    ) == (48.0, "GBP")

    cartes = asos._extraire_cartes(FR_HTML)
    assert len(cartes) == 4
    assert cartes[0][0].endswith("/prd/206103842")

    variantes = asos._variantes_recherche("Nike Trail")
    assert variantes[0] == "Nike Trail"
    assert "Nike Running Trail" in variantes
    assert len(variantes) <= asos._MAX_QUERY_VARIANTS

    variantes_tshirt = asos._variantes_recherche("t shirt Nike Trail")
    assert variantes_tshirt[0] == "t shirt Nike Trail"
    # Régression V2.5 : les variantes perdaient le type t-shirt.
    assert any("t shirt" in asos._normaliser_texte(v) for v in variantes_tshirt[1:])
    assert all(
        ("shirt" in asos._normaliser_texte(v) or "tee" in asos._normaliser_texte(v))
        for v in variantes_tshirt[1:]
    ), variantes_tshirt
    assert any("nike running trail" in asos._normaliser_texte(v) for v in variantes_tshirt)

    # V2.7 : titre réel dans le alt de l'image, prix dans aria-label.
    cartes_live = asos._extraire_cartes_riches(LIVEISH_HTML)
    assert len(cartes_live) == 3
    titre_live = asos._meilleur_titre_carte(cartes_live[0], "t shirt Nike Trail")
    assert "Nike Trail" in titre_live and "T-shirt" in titre_live
    score_live, fort_live = asos._score_pertinence_titre(titre_live, "t shirt Nike Trail")
    assert fort_live and score_live >= 95

    # ESSENTIALS = Fear of God Essentials, pas les gammes homonymes adidas/ASOS.
    assert asos._score_pertinence_titre(
        "Fear of God Essentials - Ensemble hoodie et pantalon",
        "ensemble Essentials",
    )[1] is True
    assert asos._score_pertinence_titre(
        "adidas Originals - Essentials - Ensemble",
        "ensemble Essentials",
    )[1] is False
    assert asos._detecter_type_recherche("ensemble Essantials")[0] == "ensemble"
    variantes_ensemble = asos._variantes_recherche("ensemble Essantials")
    assert any("fear of god essentials" in asos._normaliser_texte(v) for v in variantes_ensemble)

    original_pages = asos._telecharger_pages_variantes
    original_fx = asos.obtenir_taux_gbp_eur

    def faux_pages(search_url, headers, variantes, nombre_pages, locale):
        del search_url, headers, variantes, nombre_pages
        html = FR_HTML if locale == "FR" else GB_HTML
        return [{"page": 1, "status": 200, "html": html, "url": "https://www.asos.com/", "elapsed": 0.01, "query_variant": "Nike Trail"}]

    try:
        asos._telecharger_pages_variantes = faux_pages
        asos.obtenir_taux_gbp_eur = lambda: 1.10
        resultats = asos.ASOSConnector().search(
            query="Nike Trail",
            price_max=50,
            limit=20,
        )
    finally:
        asos._telecharger_pages_variantes = original_pages
        asos.obtenir_taux_gbp_eur = original_fx

    # 2 exacts FR + 1 exact GB ; le doublon inter-locale est supprimé par PID.
    forts = [r for r in resultats if r.get("match_requete_fort")]
    assert len(forts) == 3, forts
    assert len({asos._pid_depuis_lien(r["lien"]) for r in resultats}) == len(resultats)
    assert all(r["prix"] <= 50 for r in resultats)
    assert resultats[0]["match_requete_fort"] is True
    assert any(r.get("source_locale") == "FR" for r in resultats)
    assert any(r.get("source_locale") == "GB" for r in resultats)

    print("OK - ASOS V2.7 titres live, ensembles, ESSENTIALS, variantes ciblées, prix EUR/GBP et dédoublonnage validés.")


if __name__ == "__main__":
    main()
