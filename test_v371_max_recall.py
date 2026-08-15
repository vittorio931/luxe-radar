from pathlib import Path

root = Path(__file__).resolve().parent
app = (root / 'app_web.py').read_text(encoding='utf-8')

page_sources = (
    'eBay', 'Zalando', 'Vinted', 'i-Run', 'Direct Running', 'Alltricks',
    'Deporvillage', 'Running Point', 'Hardloop', 'Ekosport', 'Courir',
    '21RUN', 'MisterRunning', 'Spartoo', 'Footshop', 'JD Sports',
)
recall_sources = ('SSENSE', 'ASOS', 'AliExpress', 'DHgate', 'Cdiscount', '67behaviour', '1688', 'Grailed')

assert 'EXPAND_PAGE_SOURCES = (' in app
assert 'EXPAND_RECALL_INITIAL_LIMITS = {' in app
assert 'EXPAND_ALL_SOURCES = EXPAND_PAGE_SOURCES + EXPAND_RECALL_SOURCES' in app
assert 'all(source in exhausted for source in EXPAND_ALL_SOURCES)' in app
assert '"recall_limit": dict(EXPAND_RECALL_INITIAL_LIMITS)' in app
assert 'per_site_limit=16 if IS_RENDER_RUNTIME else 24' in app
assert 'site_limit=8 if IS_RENDER_RUNTIME else 14' in app
for source in page_sources + recall_sources:
    assert f'"{source}"' in app, source
assert '"eBay": 100' in app
assert '"Grailed": 36' in app
assert 'next_recall_limit = min(100' in app
assert 'if empty[target] >= 1 or limits[target] >= 100' in app
print('OK - V3.7 MAX RECALL: all pageable sources rotate in infinite scroll; non-page sources widen safely to 100; public catalogue waves are deeper.')
