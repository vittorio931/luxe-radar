from pathlib import Path

root = Path(__file__).resolve().parent
app = (root / "app_web.py").read_text(encoding="utf-8")
js = (root / "static" / "app.js").read_text(encoding="utf-8")
css = (root / "static" / "app.css").read_text(encoding="utf-8")
sw = (root / "static" / "sw.js").read_text(encoding="utf-8")

assert 'ASSET_VERSION = "20260903-405"' in app
assert "skipped_sources" in js and "chip.classList.toggle('skipped'" in js
assert "avec résultats sur ${attempted} interrogée" in js
assert "with results out of ${attempted} checked" in js
assert ".source-chip.skipped" in css
assert "luxe-radar-shell-v405-source-truth" in sw
assert js.count("20260903-405") == 0

print("OK - bilan honnête des sources répondantes, interrogées et en pause")
