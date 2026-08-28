import json
from pathlib import Path

from marketplaces.catalog import get_definition, get_definitions, invalidate_catalog_cache
from marketplaces.source_health import SourceHealthRegistry
from marketplaces.source_scheduler import MAX_GLOBAL_SOURCE_JOBS, plan_sources
from marketplaces.connectors.retail_public import parse_jsonld_products


data = json.loads(Path("marketplaces/sites.json").read_text(encoding="utf-8"))
assert isinstance(data.get("sites"), list) and len(data["sites"]) >= 1200
invalidate_catalog_cache()
definitions = get_definitions()
assert len(definitions) == len({item.id for item in definitions})
assert all(item.tier in {1, 2, 3, 4} for item in definitions)
assert all(item.max_concurrency in {1, 2, 3, 4} for item in definitions)
assert all(item.domain for item in definitions)
assert 1 <= MAX_GLOBAL_SOURCE_JOBS <= 32

for name in ("Mytheresa", "Luisaviaroma", "MR PORTER", "24S"):
    definition = get_definition(name)
    assert definition and not definition.enabled and definition.tier == 4


class Fake:
    def __init__(self, name): self.name = name


available = {name: Fake(name) for name in ("SSENSE", "Kith", "i-Run", "eBay")}
luxury = plan_sources("Stone Island jacket", available)
running = plan_sources("Nike Trail", available)
assert len(luxury) <= 40 and len(running) <= 40
assert {item.name for item in luxury} == set(available)

health = SourceHealthRegistry()
health.record_outcome("SSENSE", 20, 10, network_elapsed=0.2)
snap = health.snapshot(["SSENSE"])["SSENSE"]
assert snap["health_state"] == "DEGRADED" and not health.eligible_for_activation("SSENSE")
health.record_outcome("SSENSE", 20, 10, network_elapsed=0.2)
health.record_outcome("SSENSE", 20, 10, network_elapsed=0.2)
snap = health.snapshot(["SSENSE"])["SSENSE"]
assert snap["health_state"] == "HEALTHY" and health.eligible_for_activation("SSENSE")
assert snap["success_rate"] == 1.0 and snap["results_rate"] == 0.5
assert health.summary(["SSENSE"])["healthy"] == 1

jsonld = '''<script type="application/ld+json">{"@type":"Product","name":"Stone Island jacket","brand":{"@type":"Brand","name":"Stone Island"},"sku":"SI-123","color":"Black","category":"Jackets","image":"https://shop.test/a.jpg","offers":{"@type":"Offer","price":"595","priceCurrency":"GBP","url":"https://shop.test/p/si-123","availability":"https://schema.org/InStock"}}</script>'''
parsed = parse_jsonld_products(jsonld, "https://shop.test")
assert len(parsed) == 1 and parsed[0]["brand"] == "Stone Island"
assert parsed[0]["devise_originale"] == "GBP" and parsed[0]["reference"] == "SI-123"

print(f"OK - marketplace scaling: {len(definitions)} definitions, tiers, health, bounded planning")
