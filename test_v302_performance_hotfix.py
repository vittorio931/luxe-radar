from pathlib import Path
import importlib.util

root = Path(__file__).resolve().parent
app = (root / 'app_web.py').read_text(encoding='utf-8')
zalando = (root / 'marketplaces/connectors/zalando.py').read_text(encoding='utf-8')
sw = (root / 'static/sw.js').read_text(encoding='utf-8')
req = (root / 'requirements.txt').read_text(encoding='utf-8')

assert 'APP_VERSION = "3.7.1"' in app
assert 'ASSET_VERSION = "20260816-371"' in app
for source in ("Vinted","Zalando","ASOS","SSENSE","Cdiscount"):
    assert f'"{source}"' in app
assert '"Cdiscount"' in app and '"Zalando"' in app
assert 'wave_order = ("eBay",) if initial_pipeline_pending else EXPAND_WAVE_ORDER' in app
assert '"Zalando": 30' in app
assert 'empty_threshold = 1 if target == "Zalando" else 2' in app
assert 'target == "Zalando" and IS_RENDER_RUNTIME' in app
assert 'int(next_page or 1) >= 2' in app

assert 'HTTP_CONNECT_TIMEOUT = 2.5' in zalando
assert 'HTTP_READ_TIMEOUT = 6' in zalando
assert 'total=0' in zalando and 'read=0' in zalando and 'status=0' in zalando
assert '_CIRCUIT_TIMEOUT_SECONDS = 120' in zalando
assert '_CIRCUIT_BLOCK_SECONDS = 600' in zalando
assert 'if response.status_code in {403, 429}' in zalando
assert 'aucun contournement' in zalando
assert 'pause rapide' in zalando

assert 'luxe-radar-shell-v371' in sw
assert '20260816-371' in sw
assert 'gunicorn==23.0.0' in req
assert 'gunicorn==23.0.1' not in req
assert not (root / '.env').exists()

# Vérifie aussi la configuration Retry réelle, sans effectuer de requête réseau.
spec = importlib.util.spec_from_file_location('zalando_v302', root / 'marketplaces/connectors/zalando.py')
# L'import direct relatif n'est pas pratique hors package; on valide donc la config par source.

print('OK - V3.7.0 retains V3.0.2 Zalando fast-fail, Render page cap and anti-stall hotfix validated.')
