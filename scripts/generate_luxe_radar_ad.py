from pathlib import Path
import math

import imageio.v2 as imageio
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "media"
OUT.mkdir(exist_ok=True)
W, H, FPS, DURATION = 1080, 1920, 30, 15
BG, PANEL, TEXT, MUTED, GREEN = "#07090d", "#11151d", "#f7f8fb", "#8992a3", "#a8ff3e"
FONT = Path("C:/Windows/Fonts/segoeui.ttf")
BOLD = Path("C:/Windows/Fonts/seguisb.ttf")
BLACK = Path("C:/Windows/Fonts/seguibl.ttf")


def font(size, heavy=False):
    return ImageFont.truetype(str(BLACK if heavy else BOLD if size > 44 else FONT), size)


def ease(x):
    x = max(0, min(1, x))
    return 1 - (1 - x) ** 3


def opacity(t, start, end, fade=.35):
    return min(ease((t-start)/fade), ease((end-t)/fade))


def rounded(draw, box, radius=30, fill=PANEL, outline="#242a36", width=2):
    draw.rounded_rectangle(box, radius, fill=fill, outline=outline, width=width)


def centered(draw, text, y, fnt, fill=TEXT):
    box = draw.textbbox((0, 0), text, font=fnt)
    draw.text(((W-(box[2]-box[0]))/2, y), text, font=fnt, fill=fill)


def base_frame():
    im = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((620, -280, 1280, 380), fill=(168, 255, 62, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(100))
    im.paste(glow, mask=glow)
    return im


def brand(draw):
    draw.rounded_rectangle((70, 64, 150, 144), 22, fill=GREEN)
    draw.text((87, 83), "LR", font=font(32, True), fill="#071006")
    draw.text((175, 82), "LUXE", font=font(37, True), fill=TEXT)
    draw.text((305, 82), "RADAR", font=font(37, True), fill=GREEN)


def search_ui(im, progress):
    d = ImageDraw.Draw(im)
    y = int(500 + (1-ease(progress))*120)
    rounded(d, (65, y, 1015, y+660), 42)
    d.text((110, y+55), "RECHERCHE MULTI-SOURCES", font=font(25), fill=GREEN)
    d.text((110, y+110), "Ton prochain bon plan", font=font(55, True), fill=TEXT)
    d.text((110, y+170), "commence ici.", font=font(55, True), fill=TEXT)
    fields = [("PRODUIT", "Nike Trail"), ("PRIX MAXIMUM", "50 €"), ("MARKETPLACE", "Toutes les sources")]
    for i, (label, value) in enumerate(fields):
        fy = y+270+i*105
        d.text((110, fy), label, font=font(20), fill=MUTED)
        d.rounded_rectangle((110, fy+30, 970, fy+92), 16, fill="#181d27", outline="#293140", width=2)
        shown = value[:max(0, int(len(value)*ease(progress*1.4-i*.12)))]
        d.text((135, fy+43), shown, font=font(28), fill=TEXT)
    d.rounded_rectangle((110, y+585, 970, y+645), 17, fill=GREEN)
    centered_text = "SCANNER MAINTENANT"
    tw = d.textbbox((0,0), centered_text, font=font(25, True))[2]
    d.text(((W-tw)/2, y+600), centered_text, font=font(25, True), fill="#071006")


def feature_cards(im, progress):
    d = ImageDraw.Draw(im)
    cards = [("50 PAR LOT", "Résultats continus"), ("4 SOURCES", "Vinted · eBay · Grailed · 67behaviour"), ("0 DOUBLON", "Les vraies annonces, simplement")]
    for i, (title, sub) in enumerate(cards):
        local = ease(progress*1.5-i*.18)
        x = int(70 + (1-local)*W)
        y = 540+i*260
        rounded(d, (x, y, x+940, y+205), 32)
        d.text((x+45, y+42), title, font=font(47, True), fill=GREEN)
        d.text((x+45, y+112), sub, font=font(27), fill=MUTED)


def pro_card(im, progress):
    d = ImageDraw.Draw(im)
    scale = .86 + .14*ease(progress)
    cw, ch = int(900*scale), int(800*scale)
    x, y = (W-cw)//2, 500+(800-ch)//2
    rounded(d, (x, y, x+cw, y+ch), 42, outline=GREEN, width=4)
    d.text((x+55, y+55), "LUXE RADAR", font=font(25), fill=GREEN)
    d.text((x+55, y+105), "PRO", font=font(82, True), fill=TEXT)
    d.text((x+55, y+225), "3,99 €", font=font(93, True), fill=GREEN)
    d.text((x+380, y+273), "/ mois", font=font(27), fill=MUTED)
    features = ["Alertes & favoris illimités", "Comparateur et suivi des prix", "Portfolio et exports"]
    for i, item in enumerate(features):
        yy=y+390+i*78
        d.ellipse((x+55,yy+5,x+81,yy+31),fill=GREEN)
        d.text((x+100,yy),item,font=font(29),fill=TEXT)
    d.rounded_rectangle((x+55,y+ch-105,x+cw-55,y+ch-40),18,fill=GREEN)
    label="7 JOURS GRATUITS"
    tw=d.textbbox((0,0),label,font=font(25,True))[2]
    d.text(((W-tw)/2,y+ch-88),label,font=font(25,True),fill="#071006")


def render(t):
    im = base_frame()
    d = ImageDraw.Draw(im)
    brand(d)
    if t < 3:
        a = opacity(t, 0, 3)
        shift = int((1-ease(t/.6))*80)
        centered(d, "Tu cherches encore", 540+shift, font(72, True), TEXT)
        centered(d, "site par site ?", 635+shift, font(92, True), GREEN)
        centered(d, "Perds moins de temps. Trouve mieux.", 815+shift, font(31), MUTED)
        d.arc((290, 1040, 790, 1540), 200, 510, fill=GREEN, width=18)
        angle=t*4.8
        cx,cy=540,1290
        d.line((cx,cy,cx+210*math.cos(angle),cy+210*math.sin(angle)),fill=GREEN,width=15)
        d.ellipse((cx-22,cy-22,cx+22,cy+22),fill=GREEN)
    elif t < 7:
        search_ui(im, (t-3)/4)
    elif t < 10.5:
        centered(d, "Tout au même endroit.", 300, font(68, True), TEXT)
        feature_cards(im, (t-7)/3.5)
    elif t < 13:
        centered(d, "Va plus loin avec", 285, font(50, True), TEXT)
        pro_card(im, (t-10.5)/2.5)
    else:
        centered(d, "TROUVE AVANT", 525, font(92, True), TEXT)
        centered(d, "LES AUTRES.", 630, font(105, True), GREEN)
        centered(d, "LUXE RADAR", 845, font(45, True), TEXT)
        d.rounded_rectangle((150, 1030, 930, 1120), 22, fill=GREEN)
        centered(d, "ESSAIE GRATUITEMENT", 1050, font(32, True), "#071006")
        centered(d, "Vinted · eBay · Grailed · 67behaviour", 1195, font(25), MUTED)
    return im


def main():
    target = OUT / "luxe_radar_pub_verticale.mp4"
    writer = imageio.get_writer(target, fps=FPS, codec="libx264", quality=8, pixelformat="yuv420p")
    for frame in range(DURATION*FPS):
        writer.append_data(__import__('numpy').asarray(render(frame/FPS)))
    writer.close()
    render(13.7).save(OUT / "luxe_radar_pub_miniature.png")
    print(target)


if __name__ == "__main__":
    main()
