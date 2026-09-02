import app_web
from unittest.mock import patch


def offer(source, index, score=90):
    return {
        "titre": f"On Cloud 5 {source} {index}",
        "marketplace": source,
        "prix": 100 + index,
        "score": score,
        "score_identite": 90,
        "niveau_identite": "fort",
        "lien": f"https://{source.lower()}.example/{index}",
    }


ranked = [offer("eBay", index, 100) for index in range(80)]
ranked += [offer("Vinted", index) for index in range(20)]
ranked += [offer("Grailed", index) for index in range(12)]
ranked += [offer("AliExpress", index) for index in range(4)]

head = app_web._marketplace_coverage_head(ranked)[:50]
counts = app_web._marketplace_counts(head)
assert counts["Vinted"] >= 8, counts
assert counts["Grailed"] >= 8, counts
assert counts["AliExpress"] == 4, counts
assert counts["eBay"] >= 8, counts
assert len(head) == 50 and len({item["lien"] for item in head}) == 50

active = set(app_web._RENDER_HTTP_SEARCH_SOURCES)
with patch.object(app_web, "IS_RENDER_RUNTIME", True):
    background = app_web._render_background_sources(250, "Toutes", active)
assert len(background) == 6, background
assert background[0:2] == ["ASOS", "Zalando"], background
assert "eBay" not in background, "eBay ne doit pas chasser les enrichissements minoritaires"

variants = app_web._background_query_variants("OnCloud5")
assert variants == [
    "OnCloud5", "On Running Cloud 5", "On Cloud5",
    "Cloud 5 On Running", "On Cloud 5 Waterproof",
], variants

print(f"OK - top 50 multi-sources {counts}; enrichissement HTTP {background}")
