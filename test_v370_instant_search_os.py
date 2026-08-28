from pathlib import Path
import tempfile

import index_engine
from marketplaces.connectors.quality_filters import filter_results

root = Path(__file__).resolve().parent
app = (root / 'app_web.py').read_text(encoding='utf-8')
tpl = (root / 'templates' / 'index.html').read_text(encoding='utf-8')
js = (root / 'static' / 'app.js').read_text(encoding='utf-8')
css = (root / 'static' / 'app.css').read_text(encoding='utf-8')
sw = (root / 'static' / 'sw.js').read_text(encoding='utf-8')
quality = (root / 'marketplaces' / 'connectors' / 'quality_filters.py').read_text(encoding='utf-8')
retail = (root / 'marketplaces' / 'connectors' / 'retail_public.py').read_text(encoding='utf-8')

assert 'APP_VERSION = "3.8.1"' in app and 'ASSET_VERSION = "20260825-382"' in app
assert 'index_mode = index_hit_count > 0' in app
assert "Le HTML n'attend plus eBay ni aucun autre marchand" in app
assert '_progressive_source_order' in app and '_RUNNING_BRANDS' in app and '_LUXURY_FASHION_BRANDS' in app
assert 'indexed_suggestions = index_engine.suggest(query, limit=8)' in app
assert '"catalog_total": int(indexed.total)' in app
assert 'luxe-radar-shell-v371' in sw and '20260821-380' in sw

# A shopping search no longer requires a max budget.
price_fragment = tpl.split('id="prix"', 1)[1].split('>', 1)[0]
assert 'required' not in price_fragment
assert 'Prix maximum <span class="muted">(optionnel)</span>' in tpl

# Search assistance is available in header, home and the detailed radar.
for element_id in ('global-query-suggestions', 'home-query-suggestions', 'query-suggestions'):
    assert element_id in tpl
for selector in ("#global-search-input", "#home-search-input", "#marque"):
    assert selector in js
assert "setTimeout(()=>$('#search-form')?.requestSubmit(),30)" in js
assert 'query-suggestion-summary' in css
assert 'uiVersion:371' in js

# Safe retail redirects: malformed JS placeholders are never followed as DNS names.
assert '_safe_public_get' in retail and '_same_public_host' in retail
assert 'redirection publique invalide/hors domaine' in retail

# Brand/model mismatches are now rejected even while recall mode is enabled.
assert 'marque demandée absente' in quality
sample = [
    {'titre': 'Stone Island overshirt jacket', 'prix': 250},
    {'titre': 'River Island boxy fit t-shirt in light stone', 'prix': 20},
]
kept = filter_results(sample, query='Stone Island', marketplace='test')
assert [x['titre'] for x in kept] == ['Stone Island overshirt jacket']

cloud = [
    {'titre': 'ON Cloud 5 Waterproof', 'prix': 120},
    {'titre': 'ON Cloud 6 sneakers', 'prix': 100},
    {'titre': 'ON Cloud X 5', 'prix': 110},
]
assert [x['titre'] for x in filter_results(cloud, query='On Cloud 5', marketplace='test')] == ['ON Cloud 5 Waterproof']

# Global catalogue reuse: a broad warmed query answers refinements/partial input.
def offer(i, title, query='Stone Island'):
    return {
        'marketplace': 'TestShop',
        'titre': title,
        'prix': 100 + i,
        'prix_total': 100 + i,
        'devise': 'EUR',
        'lien': f'https://example.test/{query.replace(" ", "-")}/{i}',
        'niveau_identite': 'possible',
        'score_identite': 62,
        'score': 75,
        'score_confiance': 80,
        'risque_contrefacon': 'faible',
    }

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / 'v370.sqlite3'
    rows = [offer(i, f'Stone Island veste jacket modèle {i}') for i in range(80)]
    # Exact-query diagnostics may retain it, reusable global catalogue must not.
    rows.append(offer(999, 'River Island t-shirt in stone'))
    index_engine.upsert_results(rows, 'Stone Island', path=db)
    state = index_engine.stats(path=db)
    assert state['offers'] == 81 and state['catalog_offers'] == 80

    refined = index_engine.search('Stone Island veste', path=db, identity='confirmed', limit=5000)
    assert refined.total == 80 and len(refined.results) == 80
    assert all('Stone Island' in x['titre'] for x in refined.results)

    partial = index_engine.search('Stone Isl', path=db, identity='confirmed', limit=5000)
    assert partial.total == 80

    suggestions = index_engine.suggest('Stone Isl', path=db, limit=8)
    assert suggestions

    cloud_rows = [
        offer(2001, 'ON Cloud 5 Waterproof', query='On Cloud 5'),
        offer(2002, 'ON Cloud 6 sneakers', query='On Cloud 5'),
    ]
    index_engine.upsert_results(cloud_rows, 'On Cloud 5', path=db)
    global_cloud = index_engine.search('On Cloud 5 chaussures', path=db, identity='confirmed', limit=100)
    assert global_cloud.total == 1 and 'Cloud 5' in global_cloud.results[0]['titre']

assert index_engine.min_instant_results() == 1
assert not (root / '.env').exists()
print('OK - V3.7.0 Instant Search OS: immediate HTML, reusable global catalogue, smart assistance, optional budget and quality gates validated.')
