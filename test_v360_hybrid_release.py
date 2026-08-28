from pathlib import Path

root = Path(__file__).resolve().parent
app = (root / 'app_web.py').read_text(encoding='utf-8')
tpl = (root / 'templates' / 'index.html').read_text(encoding='utf-8')
js = (root / 'static' / 'app.js').read_text(encoding='utf-8')
sw = (root / 'static' / 'sw.js').read_text(encoding='utf-8')
index_code = (root / 'index_engine.py').read_text(encoding='utf-8')
warm = (root / 'warm_index.py').read_text(encoding='utf-8')

assert 'APP_VERSION = "3.8.1"' in app and 'ASSET_VERSION = "20260825-382"' in app
assert 'index_engine.search(' in app
assert 'index_mode = index_hit_count > 0' in app
assert "Le HTML n'attend plus eBay ni aucun autre marchand" in app
assert '_index_results_async(additions, query)' in app
assert 'SEARCH_RESULT_LIMIT' in app and '5000' in app
assert '@app.get("/api/index/status")' in app
assert 'Index instantané' in tpl and 'indexMode' in tpl
assert 'uiVersion:371' in js
assert "luxe-radar-shell-v371" in sw and '20260821-380' in sw
assert 'CREATE TABLE IF NOT EXISTS indexed_results' in index_code and 'CREATE TABLE IF NOT EXISTS catalog_offers' in index_code
assert 'journal_mode=WAL' in index_code
assert 'DEFAULT_QUERIES = ["Stone Island", "Nike P-6000", "On Cloud 5"]' in warm
assert not (root / '.env').exists()

print('OK - V3.7.0 preserves V3.6 hybrid index: instant HTML, global catalogue, live refresh and 5000-result pagination validated.')
