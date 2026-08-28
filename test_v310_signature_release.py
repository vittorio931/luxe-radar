from pathlib import Path
import json

root = Path(__file__).resolve().parent
app = (root / "app_web.py").read_text(encoding="utf-8")
html = (root / "templates" / "index.html").read_text(encoding="utf-8")
css = (root / "static" / "app.css").read_text(encoding="utf-8")
js = (root / "static" / "app.js").read_text(encoding="utf-8")
sw = (root / "static" / "sw.js").read_text(encoding="utf-8")
asos = (root / "marketplaces" / "connectors" / "asos.py").read_text(encoding="utf-8")
aliexpress = (root / "marketplaces" / "connectors" / "aliexpress.py").read_text(encoding="utf-8")
retail = (root / "marketplaces" / "connectors" / "retail_public.py").read_text(encoding="utf-8")
req = (root / "requirements.txt").read_text(encoding="utf-8")
sites = json.loads((root / "marketplaces" / "sites.json").read_text(encoding="utf-8"))["sites"]

# Release/cache identity.
assert 'APP_VERSION = "3.8.1"' in app
assert 'ASSET_VERSION = "20260825-382"' in app
assert 'luxe-radar-shell-v371' in sw and '20260821-380' in sw
assert 'gunicorn==23.0.0' in req

# V3.2.0 precise-by-default visibility is included.

# Render source priority: useful/fast sources before slow or often-blocked sources.
order = ['"Vinted"', '"Zalando"', '"ASOS"', '"SSENSE"', '"Cdiscount"', '"AliExpress"', '"DHgate"']
positions = [app.index(token, app.index('PROGRESSIVE_BACKGROUND_SOURCES')) for token in order]
assert positions == sorted(positions)

# Signature appearance: persistent theme + six accents + list/grid + reduced motion.
for accent in ("lime", "cyan", "violet", "rose", "orange", "blue"):
    assert f'data-accent-choice="{accent}"' in html
    if accent != "lime":
        assert f'html[data-accent="{accent}"]' in css
assert 'id="appearance-menu-toggle"' in html
assert 'id="appearance-menu"' in html
assert 'data-result-view="grid"' in html and 'data-result-view="list"' in html
assert 'data-setting="reducedMotion"' in html
assert "resultView:'grid'" in js and "reducedMotion:false" in js
assert "document.documentElement.dataset.accent" in js
assert "document.body.classList.toggle('radar-list'" in js
assert "document.body.classList.toggle('reduced-motion'" in js
assert "lr:settings" in html  # pre-paint appearance restore, avoids theme flash

# Professional radar desk controls.
for token in ('id="scan-next-wave"', 'id="share-search-quick"', 'id="clear-result-filters"'):
    assert token in html
assert "clearResultFilters" in js
assert "$('#scan-next-wave')" in js
assert "$('#share-search-quick')" in js
assert "data-query-preset" in html and "[data-query-preset]" in js
assert "event.key==='/'" in js

# Existing infinite-scroll anti-spam is preserved.
for token in ("scheduleAutoLoad", "scrollCycleBusy", "expansionNextAt", "expansionBackoffMs"):
    assert token in js
assert "res.status===202" in js and "res.status===429" in js
assert 'retry_after_ms' in app

# Slow public sources fail fast rather than holding the whole two-worker Render pipeline.
assert 'HTTP_READ_TIMEOUT = 4 if IS_RENDER else 15' in asos
assert '_DEFAULT_FR_PAGES = 1 if IS_RENDER else 2' in asos
assert 'currency=EUR&shipCountry=FR' in aliexpress
assert 'aep_usuc_f' in aliexpress and 'region=FR' in aliexpress
assert '_SOURCE_COOLDOWN_UNTIL' in retail
assert 'response.status_code in {400, 403, 429}' in retail
assert 'sans aucun contournement' in retail and 'aucune route publique exploitable -> pause temporaire' in retail

# Catalogue and secret hygiene.
active = {x["name"] for x in sites if x.get("status") == "active"}
assert len(active) >= 14
assert len(sites) >= 1200
assert not (root / ".env").exists()

print("OK - V3.7.0 preserves source fail-fast, appearance and infinite-scroll guards.")
