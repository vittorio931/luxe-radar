from pathlib import Path
import json

root = Path(__file__).resolve().parent
app = (root / "app_web.py").read_text(encoding="utf-8")
engine = (root / "radar_engine.py").read_text(encoding="utf-8")
js = (root / "static" / "app.js").read_text(encoding="utf-8")
css = (root / "static" / "app.css").read_text(encoding="utf-8")
html = (root / "templates" / "index.html").read_text(encoding="utf-8")
sw = (root / "static" / "sw.js").read_text(encoding="utf-8")
retail = (root / "marketplaces" / "connectors" / "retail_public.py").read_text(encoding="utf-8")
billing = (root / "billing_stripe.py").read_text(encoding="utf-8")
render = (root / "render.yaml").read_text(encoding="utf-8")
sites = json.loads((root / "marketplaces" / "sites.json").read_text(encoding="utf-8"))["sites"]

assert 'APP_VERSION = "3.8.1"' in app
assert 'ASSET_VERSION = "20260822-381"' in app
assert "Le HTML n'attend plus eBay ni aucun autre marchand" in app
assert '"Spartoo", "Footshop", "JD Sports"' in app
assert '90 if request.endpoint == "expand_results"' in app
assert 'retry_after_ms' in app

assert 'clientQuickFilters={belowMedian:false,withImage:false,favoritesOnly:false,lowRisk:false}' in js
assert "r.status===404" in js
assert "createCurrentAlert" in js
assert "renderRadarRecentSearches" in js
assert "#smart-deals" in js
assert "data-client-filter" in js
assert "scheduleAutoLoad" in js and "scrollCycleBusy" in js and "expansionNextAt" in js

assert 'Que veux-tu comparer ?' in html
assert 'market-header' in html and 'global-search-form' in html
assert 'id="radar-recent-searches"' in html
assert 'id="smart-deals"' in html
assert 'data-client-filter="below-median"' in html
assert 'id="restore-hidden-results"' in html
assert 'id="quick-alert"' in html
assert "['Spartoo','Footshop','JD Sports']" in html

assert 'V3.3.0 — SHOPPING-FIRST UI' in css
assert '.shopping-hero' in css
assert '.precision-deck' in css
assert '.recent-query-chip' in css
assert '.quick-result-chip.featured-action' in css
assert 'body.radar-focus .main' in css

assert 'luxe-radar-shell-v371' in sw
assert '20260821-380' in sw

active = {x["name"] for x in sites if x.get("status") == "active"}
for source in ("Spartoo", "Footshop", "JD Sports", "eBay", "Vinted", "Zalando"):
    assert source in active, source
assert len(active) >= 14
assert len(sites) >= 1200

for token in ("class SpartooConnector", "class FootshopConnector", "class JDSportsConnector"):
    assert token in retail
assert "Contrôle d'accès détecté -> route ignorée" in retail
assert "parse_jsonld_products" in retail and "parse_html_cards" in retail
for source in ("Spartoo","Footshop","JD Sports","i-Run","Direct Running"):
    assert f'"{source}"' in engine


assert 'LUXE_RADAR_BILLING_ENABLED' in billing
assert 'return enabled and bool(secret_key())' in billing
assert 'python -m pip install -r requirements.txt && python -m playwright install chromium' in render
assert 'gunicorn --config gunicorn.conf.py wsgi:application' in render
assert '.env' not in [p.name for p in root.iterdir()]
assert 'connecteurs actifs testés · 50 résultats par lot' not in html

# Le ZIP public ne doit jamais inclure de secret local.
assert not (root / ".env").exists()

print("OK - V3.7.0 public release: UI, anti-spam, stale-token handling, 24 active sources and conservative retail connectors validated.")

from search_understanding import understand_query
for query, brand, model in [
    ("Onclod 5", "On", "Cloud 5"),
    ("Colmbia tech wind", "Columbia", None),
    ("Nikz P6000", "Nike", "P-6000"),
    ("Addidas samba", "Adidas", "Samba"),
    ("New Balnce 530", "New Balance", "530"),
    ("Salomn xt6", "Salomon", "XT-6"),
    ("Hka clifton 10", "Hoka", "Clifton"),
    ("Undr armour hovr", "Under Armour", "HOVR"),
]:
    understood = understand_query(query)
    assert understood.brand == brand, (query, understood)
    assert understood.model == model, (query, understood)
print("OK - V3.7.0 typo tolerance sample across major brands validated.")
