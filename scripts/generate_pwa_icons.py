"""Reproduit l'icône SVG LUXE RADAR en PNG pour les installateurs PWA."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

def make_icon(size: int) -> Path:
    scale = size / 512
    image = Image.new("RGB", (size, size), "#a8ff3e")
    draw = ImageDraw.Draw(image)
    center = size // 2
    radar_y = round(218 * scale)
    radar_radius = round(132 * scale)
    draw.ellipse((center-radar_radius, radar_y-radar_radius, center+radar_radius, radar_y+radar_radius), outline="#071006", width=max(2, round(32*scale)))
    draw.line((center, radar_y, round(358*scale), round(148*scale)), fill="#071006", width=max(2, round(26*scale)))
    dot = max(3, round(26*scale))
    draw.ellipse((center-dot, radar_y-dot, center+dot, radar_y+dot), fill="#071006")
    face = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", max(16, round(62*scale)))
    draw.text((center, round(410*scale)), "LR", font=face, fill="#071006", anchor="mm")
    target = ROOT / "static" / f"app-icon-{size}.png"
    image.save(target, format="PNG", optimize=True)
    return target

if __name__ == "__main__":
    for requested_size in (192, 512):
        print(make_icon(requested_size))
