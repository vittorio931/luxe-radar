from time import perf_counter, sleep
from unittest.mock import patch

import collector
import connector_registry
from marketplaces.connectors.ebay import EbayConnector
from search_intent import parse_search_intent


assert EbayConnector.expansion_page_size == 200
assert EbayConnector.max_pages == 25
assert EbayConnector.expansion_recall_cap == 5000
compact = parse_search_intent("OnCloud5")
assert compact.brand == "On" and compact.model == "Cloud 5", compact


class SlowSource:
    supports_pagination = False
    max_pages = 1
    expansion_page_size = 10
    empty_pages_threshold = 1
    cooldown_seconds = 0

    def __init__(self, name):
        self.name = name

    def search(self, query, price_max=None, limit=20):
        sleep(0.20)
        return []


sources = {f"Source {index}": SlowSource(f"Source {index}") for index in range(4)}
started = perf_counter()
with patch.object(collector, "get_available_connectors", return_value=sources), \
     patch.object(collector, "COLLECTOR_SOURCE_WORKERS", 4):
    summary = collector.collect_seed("On Cloud 5", sources=list(sources), dry_run=True)
elapsed = perf_counter() - started
assert len(summary["sources"]) == 4
assert elapsed < 0.55, elapsed


connector_registry.invalidate_connector_cache()
with patch.object(connector_registry, "_native_connectors", return_value={"Test": SlowSource("Test")}) as native, \
     patch.object(connector_registry, "load_configured_connectors", return_value=[]):
    assert "Test" in connector_registry.get_available_connectors()
    assert "Test" in connector_registry.get_available_connectors()
    assert native.call_count == 1
connector_registry.invalidate_connector_cache()

print(f"OK - registre cache, collecte 4 sources en {elapsed:.3f}s, eBay profond 5000 candidats.")
