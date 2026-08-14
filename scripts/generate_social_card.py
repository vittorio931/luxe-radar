"""Crée la carte Open Graph LUXE RADAR depuis l'asset de campagne original."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "media" / "campaign" / "assets" / "reseller-campaign-v1.png"
TARGET = ROOT / "static" / "social-card.png"
W, H = 1200, 630

source = Image.open(SOURCE).convert("RGB")
scale = max(W / source.width, H / source.height)
source = source.resize((round(source.width * scale), round(source.height * scale)), Image.Resampling.LANCZOS)
left = max(0, (source.width - W) // 2)
top = max(0, (source.height - H) // 2)
image = source.crop((left, top, left + W, top + H))
image = ImageEnhance.Brightness(image).enhance(0.42)
overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)
draw.rectangle((0, 0, W, H), fill=(4, 6, 10, 125))
draw.ellipse((760, -280, 1420, 380), fill=(168, 255, 62, 40))
overlay = overlay.filter(ImageFilter.GaussianBlur(35))
image = Image.alpha_composite(image.convert("RGBA"), overlay)
draw = ImageDraw.Draw(image)
black = "C:/Windows/Fonts/seguibl.ttf"
bold = "C:/Windows/Fonts/seguisb.ttf"
draw.rounded_rectangle((72, 65, 136, 129), 17, fill="#a8ff3e")
draw.text((86, 80), "LR", font=ImageFont.truetype(black, 23), fill="#071006")
draw.text((158, 78), "LUXE", font=ImageFont.truetype(black, 28), fill="#f7f8fb")
draw.text((252, 78), "RADAR", font=ImageFont.truetype(black, 28), fill="#a8ff3e")
draw.text((72, 205), "TROUVE AVANT", font=ImageFont.truetype(black, 70), fill="#f7f8fb")
draw.text((72, 282), "LES AUTRES.", font=ImageFont.truetype(black, 82), fill="#a8ff3e")
draw.text((76, 405), "Mode · sneakers · vintage · revente", font=ImageFont.truetype(bold, 25), fill="#d5d9e1")
draw.text((76, 465), "4 sources actives testées · résultats par lots de 50", font=ImageFont.truetype(bold, 20), fill="#9aa4b5")
image.convert("RGB").save(TARGET, format="PNG", optimize=True)
print(TARGET)
