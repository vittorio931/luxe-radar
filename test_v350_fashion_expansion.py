from pathlib import Path
import json

root = Path(__file__).resolve().parent
app = (root / 'app_web.py').read_text(encoding='utf-8')
retail = (root / 'marketplaces' / 'connectors' / 'retail_public.py').read_text(encoding='utf-8')
quality = (root / 'marketplaces' / 'connectors' / 'quality_filters.py').read_text(encoding='utf-8')
html = (root / 'templates' / 'index.html').read_text(encoding='utf-8')
css = (root / 'static' / 'app.css').read_text(encoding='utf-8')
sites = json.loads((root / 'marketplaces' / 'sites.json').read_text(encoding='utf-8'))['sites']

assert 'APP_VERSION = "3.7.1"' in app
assert 'ASSET_VERSION = "20260816-371"' in app

new_sources = [
    'i-Run','Direct Running','Alltricks','Deporvillage','Running Point',
    'Hardloop','Ekosport','Courir','21RUN','MisterRunning'
]
for source in new_sources:
    assert f'"{source}"' in app, source
    assert source in html, source

for cls in [
    'IRunConnector','DirectRunningConnector','AlltricksConnector','DeporvillageConnector',
    'RunningPointConnector','HardloopConnector','EkosportConnector','CourirConnector',
    'TwentyOneRunConnector','MisterRunningConnector'
]:
    assert f'class {cls}' in retail, cls

active = {x['name'] for x in sites if x.get('status') == 'active'}
for source in new_sources:
    assert source in active, source
assert len(active) >= 24
assert len(sites) >= 1233

assert 'build_search_urls' in retail and 'route_budget = 2 if IS_RENDER else 3' in retail
assert 'aucune route publique exploitable -> pause temporaire' in retail
assert '_explicit_model_anchor_mismatch' in quality
assert 'variante de modèle incompatible' in quality
assert 'merchant-chip-grid' in html and '.merchant-chip-grid' in css
assert 'COMPARATEUR MODE, SNEAKERS & RUNNING' in html

from marketplaces.connectors.quality_filters import filter_results
sample = [
    {'titre':'ON Cloud 5 Waterproof','prix':120},
    {'titre':'ON Cloud 6 sneakers','prix':100},
    {'titre':'ON Cloud X 5','prix':110},
]
kept = filter_results(sample, query='On Cloud 5', marketplace='test')
assert [x['titre'] for x in kept] == ['ON Cloud 5 Waterproof']

from connector_registry import get_available_connectors
connectors = get_available_connectors()
for source in new_sources:
    assert source in connectors, source
assert len(connectors) >= 24

assert not (root / '.env').exists()
print('OK - V3.7.0 fashion expansion: 10 new specialist sources, 24+ active connectors, model guard and fashion-first UI validated.')
