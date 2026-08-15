from connector_registry import get_available_connectors, get_connector


def test_registry_imports_without_package_exports():
    connectors = get_available_connectors()
    assert isinstance(connectors, dict)
    assert "eBay" in connectors
    assert get_connector("eBay") is not None


if __name__ == "__main__":
    test_registry_imports_without_package_exports()
    print("OK V3.0.1 connector registry")
