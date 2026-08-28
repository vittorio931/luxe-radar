import json

from connector_registry import get_available_connectors, get_connector
from marketplaces.connectors.retail_public import parse_hydrogen_products


def test_registry_imports_without_package_exports():
    connectors = get_available_connectors()
    assert isinstance(connectors, dict)
    assert "eBay" in connectors
    assert "The Outnet" in connectors
    assert get_connector("eBay") is not None
    assert get_connector("The Outnet") is not None


def test_the_outnet_public_hydrogen_state_is_decoded():
    pool = [
        {"_1": 2, "_3": 4, "_5": 6},
        "title", "Balenciaga Tailored Pants",
        "handle", "balenciaga-tailored-pants",
        "priceRange", {"_7": 8},
        "minVariantPrice", {"_9": 10, "_11": 12},
        "amount", "300.0", "currencyCode", "EUR",
    ]
    payload = json.dumps(pool, separators=(",", ":"))
    html = (
        "<script>window.__reactRouterContext.streamController.enqueue("
        + json.dumps(payload)
        + ")</script>"
    )
    results = parse_hydrogen_products(html, "https://www.theoutnet.com")
    assert len(results) == 1
    assert results[0]["titre"] == "Balenciaga Tailored Pants"
    assert results[0]["prix"] == 300.0
    assert results[0]["devise_originale"] == "EUR"
    assert results[0]["lien"].endswith("/products/balenciaga-tailored-pants")


if __name__ == "__main__":
    test_registry_imports_without_package_exports()
    test_the_outnet_public_hydrogen_state_is_decoded()
    print("OK V3.0.2 connector registry")
