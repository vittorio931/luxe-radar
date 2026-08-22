from pathlib import Path
root=Path(__file__).resolve().parent
html=(root/'templates/index.html').read_text(encoding='utf-8')
css=(root/'static/app.css').read_text(encoding='utf-8')
js=(root/'static/app.js').read_text(encoding='utf-8')
app=(root/'app_web.py').read_text(encoding='utf-8')
sw=(root/'static/sw.js').read_text(encoding='utf-8')
assert 'APP_VERSION = "3.8.1"' in app
assert 'ASSET_VERSION = "20260822-381"' in app
assert 'luxe-radar-shell-v371' in sw
for token in ['market-header','global-search-form','home-search-form','Recherches populaires','Comparer les prix','Alertes prix','home-recent-searches','category-grid','source-drawer']:
    assert token in html, token
for token in ['V3.3.0 — SHOPPING-FIRST UI','shopping-hero','market-tabs','[data-theme="dark"]','results-grid']:
    assert token in css, token
for token in ['prepareSimpleSearch','global-search-form','home-search-form','uiVersion:360']:
    assert token in js, token
assert "theme:'light'" in js and "accent:'blue'" in js and "uiVersion:360" in js
assert "data-precision-mode=\"precise\"" in html and "data-precision-mode=\"strict\"" in html and "data-precision-mode=\"explore\"" in html
assert not (root/'.env').exists()
print('OK - V3.7.0 shopping-first UI, visible navigation, simple search and dark/light appearance validated.')
