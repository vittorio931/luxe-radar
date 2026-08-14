"""Crée les posters WebP légers utilisés par la galerie web."""

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "static" / "campaign"

for source in sorted(CAMPAIGN.glob("luxe_radar_*_miniature.png")):
    target = source.with_suffix(".webp")
    image = Image.open(source).convert("RGB")
    image.save(target, format="WEBP", quality=78, method=6)
    print(f"{source.name}: {source.stat().st_size} -> {target.stat().st_size} octets")
