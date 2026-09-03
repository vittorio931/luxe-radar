from pathlib import Path

root = Path(__file__).resolve().parent
html = (root / "instant-site" / "index.html").read_text(encoding="utf-8")
css = (root / "instant-site" / "instant.css").read_text(encoding="utf-8")
js = (root / "instant-site" / "instant.js").read_text(encoding="utf-8")
blueprint = (root / "render.yaml").read_text(encoding="utf-8")

assert 'id="instant-search"' in html
assert 'id="engine-status"' in html
assert 'name="q"' in html
assert "https://luxe-radar.onrender.com" in js
assert "/api/health" in js and "mode: 'no-cors'" in js
assert "url.searchParams.set('q', query)" in js
assert "@media(max-width:800px)" in css
assert "name: luxe-radar-instant" in blueprint
assert "runtime: static" in blueprint
assert "staticPublishPath: ./instant-site" in blueprint

print("OK - instant static frontend wakes the free Render backend and forwards searches")
