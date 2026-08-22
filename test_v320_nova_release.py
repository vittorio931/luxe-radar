from pathlib import Path
import json
root=Path(__file__).resolve().parent
app=(root/'app_web.py').read_text(encoding='utf-8')
engine=(root/'radar_engine.py').read_text(encoding='utf-8')
html=(root/'templates'/'index.html').read_text(encoding='utf-8')
css=(root/'static'/'app.css').read_text(encoding='utf-8')
js=(root/'static'/'app.js').read_text(encoding='utf-8')
sw=(root/'static'/'sw.js').read_text(encoding='utf-8')
assert 'APP_VERSION = "3.8.1"' in app and 'ASSET_VERSION = "20260822-381"' in app
assert 'luxe-radar-shell-v371' in sw and '20260821-380' in sw
assert 'identity = request.args.get("identity", "confirmed")' in app
assert 'identity_order = {"fort": 0, "possible": 1, "rejet": 2}' in app
assert "Le HTML n'attend plus eBay ni aucun autre marchand" in app
order=['"Vinted"','"Zalando"','"ASOS"','"SSENSE"','"AliExpress"','"DHgate"']
start=app.index('PROGRESSIVE_BACKGROUND_SOURCES')
positions=[app.index(x,start) for x in order]
assert positions==sorted(positions)
assert 'explication_pertinence' in app and 'conflit_pertinence' in app
assert 'explication_pertinence' in engine and 'conflit_pertinence' in engine
for token in ['market-header','data-precision-mode="precise"','id="compare-dock"','id="restore-hidden-results"']:
    assert token in html
for token in ['V3.3.0 — SHOPPING-FIRST UI','shopping-hero','precision-deck','compare-dock','product-card.user-hidden']:
    assert token in css
for token in ['hiddenResults','renderCompareDock','syncPrecisionDeck','data-precision-mode']:
    assert token in js
for accent in ('red','gold','mint'):
    assert f'data-accent-choice="{accent}"' in html
    assert f'html[data-accent="{accent}"]' in css
for token in ('scheduleAutoLoad','scrollCycleBusy','expansionNextAt','expansionBackoffMs'):
    assert token in js
assert not (root/'.env').exists()
print('OK - V3.7.0 shopping UI: radical UI, precise relevance, hidden-result feedback, live comparison and infinite-scroll guards validated.')
