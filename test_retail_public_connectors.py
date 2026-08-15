from marketplaces.connectors.retail_public import (
    FootshopConnector,
    JDSportsConnector,
    SpartooConnector,
    parse_html_cards,
    parse_jsonld_products,
)


def test_jsonld_parser():
    html = '''<script type="application/ld+json">{
      "@context":"https://schema.org","@type":"Product","name":"On Cloud 5 Waterproof",
      "image":"https://img.example/cloud.jpg","sku":"CLOUD5",
      "offers":{"@type":"Offer","price":"137.00","priceCurrency":"EUR","url":"/on-cloud-5","availability":"https://schema.org/InStock"}
    }</script>'''
    rows = parse_jsonld_products(html, "https://shop.example")
    assert len(rows) == 1
    assert rows[0]["titre"] == "On Cloud 5 Waterproof"
    assert rows[0]["prix"] == 137.0
    assert rows[0]["lien"] == "https://shop.example/on-cloud-5"


def test_html_parser_conservative():
    html = '''<article><a href="/product/on-cloud-5"><h3>On Cloud 5</h3><img src="/x.jpg" alt="On Cloud 5"><span>149,99 €</span></a></article>'''
    rows = parse_html_cards(html, "https://shop.example", ("/product/",))
    assert len(rows) == 1
    assert rows[0]["prix"] == 149.99
    assert rows[0]["lien"] == "https://shop.example/product/on-cloud-5"


def test_urls_are_public_and_paged():
    assert "keywords=On+Cloud+5" in SpartooConnector().build_search_url("On Cloud 5", 2)
    assert "page=2" in FootshopConnector().build_search_url("Nike P-6000", 2)
    assert "/search/Columbia-Tech-Wind/" in JDSportsConnector().build_search_url("Columbia Tech Wind", 3)


if __name__ == "__main__":
    test_jsonld_parser()
    test_html_parser_conservative()
    test_urls_are_public_and_paged()
    print("OK - retail public connectors parsers and URL builders")


def test_connector_search_without_network():
    import marketplaces.connectors.retail_public as rp
    sample = '''<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"Product","name":"Columbia Tech Wind Pants","image":"https://img.example/p.jpg","offers":{"@type":"Offer","price":"49.90","priceCurrency":"EUR","url":"/p/tech-wind"}}]}</script>'''
    class Response:
        status_code = 200
        text = sample
        headers = {"Content-Type":"text/html; charset=utf-8"}
    class Session:
        def get(self,*args,**kwargs): return Response()
        def close(self): pass
    old = rp._session
    rp._session = lambda: Session()
    try:
        rows = rp.SpartooConnector().search("Columbia Tech Wind", price_max=60, limit=10)
        assert len(rows) == 1
        assert rows[0]["marketplace"] == "Spartoo"
        assert rows[0]["prix"] == 49.9
        assert rows[0]["lien"].startswith("https://www.spartoo.com/")
        assert rp.SpartooConnector().search("Columbia Tech Wind", price_max=40, limit=10) == []
    finally:
        rp._session = old


if __name__ == "__main__":
    test_connector_search_without_network()
    print("OK - retail connector end-to-end search contract")
