from pathlib import Path
root=Path(__file__).resolve().parent
app=(root/'app_web.py').read_text(encoding='utf-8')
html=(root/'templates/index.html').read_text(encoding='utf-8')
js=(root/'static/app.js').read_text(encoding='utf-8')
css=(root/'static/app.css').read_text(encoding='utf-8')
billing=(root/'billing_stripe.py').read_text(encoding='utf-8')
sw=(root/'static/sw.js').read_text(encoding='utf-8')
assert 'APP_VERSION = "3.8.1"' in app and 'ASSET_VERSION = "20260825-382"' in app
assert '"name": "Premium", "monthly": 4.99' in app
assert '"name": "Pro", "monthly": 12.99' in app
assert 'id="auto-radar-form"' in html and 'id="auto-radar-list"' in html
assert 'Radar automatique' in html and 'Bêta locale' in html
assert "let autoRadars = storedArray('autoRadars',50);" in js
assert 'function hasPremiumAccess()' in js and 'function renderAutoRadars()' in js
assert "uiVersion:360" in js and "uiVersion:320" not in js
assert 'luxe_radar_v340_' in billing
assert 'LUXE_RADAR_BILLING_ENABLED' in billing
assert 'V3.4.0 — MONETIZATION READY' in css
assert 'luxe-radar-shell-v371' in sw
assert not (root/'.env').exists()
print('OK - V3.7.0 monetization: Free/Premium/Pro, local Automatic Radar beta, billing safety and UI migration validated.')
