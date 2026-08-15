from pathlib import Path
root=Path(__file__).resolve().parent
app=(root/'app_web.py').read_text(encoding='utf-8')
js=(root/'static'/'app.js').read_text(encoding='utf-8')
html=(root/'templates'/'index.html').read_text(encoding='utf-8')
assert 'APP_VERSION = "3.7.0"' in app
assert 'identity = request.args.get("identity", "confirmed")' in app
assert 'identity="confirmed"' in app and 'owner=session.get("csrf_token")' in app
assert "identity:$('#identity-filter')?.value||'confirmed'" in js
assert '<option value="confirmed" selected>Pertinence : précise</option>' in html
assert 'data-precision-mode="precise"' in html and 'data-precision-mode="explore"' in html
print('OK - V3.7.0 precise-by-default visibility and opt-in Explore mode validated.')
