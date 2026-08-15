"""Tests hors réseau du connecteur Cdiscount V2.6."""

from marketplaces.connectors import cdiscount


HTML = r'''
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "ItemList",
  "itemListElement": [
    {
      "@type": "Product",
      "name": "Tee-shirt Homme Nike M NK DF TEE TRAIL LOGO - FQ3914-013",
      "url": "https://www.cdiscount.com/le-sport/x/f-121030403-aacna35988.html",
      "image": "https://example.test/nike-trail.jpg",
      "offers": {"@type": "AggregateOffer", "lowPrice": "32.90", "priceCurrency": "EUR"}
    }
  ]
}
</script>
</head><body>
<a href="/pret-a-porter/x/f-113010217-abc123.html" title="T-shirt Nike Trail Dri-FIT noir">
  <img src="https://example.test/tee.jpg" alt="T-shirt Nike Trail Dri-FIT noir">
  T-shirt Nike Trail Dri-FIT noir Disponible en plusieurs tailles Prix de comparaison 39,99 € à partir de 24,90 € Voir
</a>
<a href="/pret-a-porter/x/f-113010217-expensive.html" aria-label="T-shirt Nike Trail premium">
  T-shirt Nike Trail premium à partir de 89,90 € Voir
</a>
<a href="/pret-a-porter/x/f-113010217-wrong.html" title="T-shirt Puma Trail noir">
  T-shirt Puma Trail noir à partir de 19,90 € Voir
</a>
</body></html>
'''


SPLIT_PRICE_HTML = r"""
<html><body>
<div class="product-card">
  <a href="/le-sport/x/f-121030403-live1.html" title="Tee-shirt Homme Nike M NK DF TEE TRAIL LOGO - FQ3914-013">Voir</a>
  <div class="price">Prix de comparaison 39,99 € à partir de 32,90 €</div>
</div>
<div class="product-card">
  <a href="/pret-a-porter/x/f-113010217-live2.html" title="Fear of God Essentials ensemble hoodie pantalon">Voir</a>
  <div class="price">49,90 €</div>
</div>
<div class="product-card">
  <a href="/pret-a-porter/x/f-113010217-live3.html" title="adidas Originals Essentials ensemble">Voir</a>
  <div class="price">39,90 €</div>
</div>
</body></html>
"""


def main():
    assert cdiscount._extract_price("Prix de comparaison 39,99 € à partir de 24,90 €") == 24.9
    assert cdiscount._detect_type("t shirt Nike Trail") == "tshirt"
    variants = cdiscount._query_variants("t shirt Nike Trail")
    assert variants[0] == "t shirt Nike Trail"
    assert any("tee shirt" in cdiscount._norm(v) for v in variants)

    # Les routes publiques live de Cdiscount utilisent + encodé en %2B.
    assert "r-nike%2Btrail.html" in cdiscount._candidate_routes("Nike Trail")[0]

    results, stats = cdiscount._parse_page(HTML, query="t shirt Nike Trail", price_max=50)
    assert stats["jsonld"] == 1
    assert stats["ancres"] == 3
    assert stats["hors_budget"] == 1
    assert any(r["prix"] == 32.9 and r["match_requete_fort"] for r in results)
    assert any(r["prix"] == 24.9 and r["match_requete_fort"] for r in results)

    # Le faux positif Puma peut être extrait par le connecteur, mais ne doit pas
    # être considéré comme correspondance forte et sera rejeté par le filtre global.
    puma = next(r for r in results if "Puma" in r["titre"])
    assert puma["match_requete_fort"] is False

    # V2.7 : le prix peut être rendu hors de l'ancre produit.
    split, split_stats = cdiscount._parse_page(
        SPLIT_PRICE_HTML, query="t shirt Nike Trail", price_max=50
    )
    assert split_stats["blocs"] >= 3
    assert any(r["prix"] == 32.9 and r["match_requete_fort"] for r in split)

    fog_split, _ = cdiscount._parse_page(
        SPLIT_PRICE_HTML, query="ensemble Essentials", price_max=60
    )
    fog = next(r for r in fog_split if "Fear of God" in r["titre"])
    assert fog["match_requete_fort"] is True
    adidas = next(r for r in fog_split if "adidas" in r["titre"].lower())
    assert adidas["match_requete_fort"] is False
    assert any(
        "fear of god essentials" in cdiscount._norm(v)
        for v in cdiscount._query_variants("ensemble Essantials")
    )

    original_download = cdiscount._download
    try:
        cdiscount._download = lambda url: {
            "url": url,
            "status": 200,
            "html": HTML,
            "elapsed": 0.01,
        }
        found = cdiscount.CdiscountConnector().search(
            query="t shirt Nike Trail",
            price_max=50,
            limit=20,
        )
    finally:
        cdiscount._download = original_download

    assert found
    assert found[0]["marketplace"] == "Cdiscount"
    assert all(item["prix"] <= 50 for item in found)
    assert len({item["lien"] for item in found}) == len(found)
    assert sum(1 for item in found if item.get("match_requete_fort")) >= 2
    print("OK - Cdiscount V2.7 routes %2B, prix hors ancre, ensembles, ESSENTIALS, budget et dédoublonnage validés.")


if __name__ == "__main__":
    main()
