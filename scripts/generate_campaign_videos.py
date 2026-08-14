"""Génère six publicités verticales LUXE RADAR de 60 secondes.

La voix est synthétisée localement avec Piper et le modèle français SIWIS,
dont le corpus est publié sous CC BY 4.0. La musique est créée localement et
reste sous la voix pour préserver l'intelligibilité.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from piper import PiperVoice, SynthesisConfig


ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media" / "campaign"
ASSETS = MEDIA / "assets"
PUBLIC = ROOT / "static" / "campaign"
VOICE_MODEL = MEDIA / "voices" / "fr_FR-siwis-medium.onnx"
W, H, FPS, DURATION = 720, 1280, 15, 60
BG, PANEL, WHITE, MUTED, LIME, CYAN = "#07090d", "#121720", "#f7f8fb", "#9aa4b5", "#a8ff3e", "#67e8f9"
FONT_REGULAR = "C:/Windows/Fonts/segoeui.ttf"
FONT_SEMIBOLD = "C:/Windows/Fonts/seguisb.ttf"
FONT_BLACK = "C:/Windows/Fonts/seguibl.ttf"


@dataclass(frozen=True)
class Campaign:
    slug: str
    hook: str
    subhook: str
    narration: str
    beats: tuple[tuple[str, str], ...]


CAMPAIGNS = (
    Campaign(
        "revendeur",
        "TON TEMPS VAUT PLUS",
        "QUE DIX ONGLETS OUVERTS.",
        "En revente, la marge se joue souvent avant l'achat. Mais chercher la bonne pièce sur plusieurs sites prend du temps. LUXE RADAR réunit les annonces réelles de Vinted, eBay, Grailed et soixante-sept behaviour. Tu saisis l'article et ton budget. Les résultats les plus pertinents arrivent d'abord, puis la suite se charge automatiquement. Garde une opportunité en favori, compare plusieurs annonces, crée une alerte et calcule ta marge avant de décider. Tu peux ensuite trier la liste déjà obtenue par prix, score, confiance ou marketplace, sans recommencer toute la recherche. Tes données restent sur ton appareil et peuvent être exportées quand tu veux. Ton Portfolio suit ensuite le prix d'achat, la vente et ton bénéfice réel. Moins de temps à chercher au hasard. Plus de temps pour analyser, négocier et vendre. LUXE RADAR. Trouve avant les autres.",
        (("UNE RECHERCHE", "4 sources réellement connectées"), ("50 PAR LOT", "La suite se charge automatiquement"), ("COMPARE", "Prix · score · confiance"), ("CALCULE", "Ta marge avant l'achat"), ("SUIS", "Stock · ventes · bénéfices")),
    ),
    Campaign(
        "bonne_affaire",
        "LA BONNE AFFAIRE",
        "NE T'ATTEND PAS.",
        "Tu connais ce moment. Tu trouves enfin la bonne paire, mais elle est déjà vendue. LUXE RADAR t'aide à chercher plus vite, sans inventer de résultats. Une seule recherche interroge les marketplaces réellement connectées. Tu fixes ton budget, puis le Radar classe les annonces utiles du meilleur résultat au moins bon. Pas un petit top qui cache la suite : les résultats continuent quand tu descends. Tu peux filtrer, trier, enregistrer tes favoris et reprendre une recherche depuis ton historique. Les doublons sont retirés pour garder une liste claire. Que tu cherches une sneaker, une pièce vintage ou un vêtement de luxe, tu gardes le contrôle de tes critères. Lance ton Radar. La prochaine bonne affaire est peut-être déjà en ligne.",
        (("CHERCHE UNE FOIS", "Vinted · eBay · Grailed · 67behaviour"), ("GARDE LA SUITE", "Les résultats moins classés restent visibles"), ("ZÉRO BRUIT", "Doublons retirés"), ("TON BUDGET", "Tes filtres restent appliqués"), ("GARDE L'AVANCE", "Favoris · historique · alertes")),
    ),
    Campaign(
        "gain_de_temps",
        "ARRÊTE DE CHERCHER.",
        "COMMENCE À DÉCIDER.",
        "Ouvrir un site. Retaper la recherche. Changer les filtres. Recommencer ailleurs. Ce n'est pas compliqué, mais répété chaque jour, ça coûte des heures. LUXE RADAR centralise la recherche mode et sneakers dans une interface simple. Au départ, cinquante résultats. À l'approche du bas, cinquante de plus, jusqu'à la fin des annonces disponibles. Un tri ne relance pas toutes les plateformes : il réorganise les résultats déjà obtenus. En mode Essentiel, l'interface reste légère. En mode Expert, tu retrouves les outils avancés. Et le mode Revendeur ajoute marge, inventaire et suivi du bénéfice. Tes données personnelles restent stockées localement et peuvent être exportées. Moins de répétition. Des décisions plus claires. Essaie LUXE RADAR gratuitement.",
        (("1 RADAR", "Au lieu de répéter la même recherche"), ("50 + 50 + 50", "Chargement progressif"), ("TRIE SANS RELANCER", "Prix · score · marketplace"), ("3 MODES", "Essentiel · Expert · Revendeur"), ("TES DONNÉES", "Locales et exportables")),
    ),
    Campaign(
        "acheteur_malin",
        "UN BON PRIX NE SUFFIT PAS.",
        "IL FAUT LA BONNE ANNONCE.",
        "Acheter moins cher, c'est bien. Comprendre ce que tu achètes, c'est mieux. Avec LUXE RADAR, tu recherches une pièce sur les sources réellement connectées et tu gardes tes critères au même endroit. Le classement tient compte de la pertinence, du score et de la confiance. Tu peux ensuite comparer plusieurs annonces sans perdre celles que tu as déjà chargées. Avant de cliquer, utilise la checklist : photos nettes, vendeur fiable, prix cohérent et référence vérifiée. Ajoute les meilleures options aux favoris, puis calcule ton budget total. LUXE RADAR ne garantit jamais l'authenticité d'un produit et ne remplace pas tes vérifications. Il t'aide simplement à organiser les informations utiles pour décider plus sereinement. Cherche mieux. Compare clairement. Achète seulement quand les éléments sont cohérents.",
        (("PERTINENCE", "Les annonces utiles en premier"), ("CONFIANCE", "Un indicateur, jamais une garantie"), ("COMPARE", "Jusqu'à quatre opportunités"), ("VÉRIFIE", "Photos · vendeur · prix · référence"), ("DÉCIDE", "Avec tes propres critères")),
    ),
    Campaign(
        "chasse_vintage",
        "LES BELLES PIÈCES",
        "SONT RAREMENT BIEN RANGÉES.",
        "Une pièce vintage peut apparaître avec un titre incomplet, une orthographe différente ou sur une plateforme que tu n'ouvres pas tous les jours. LUXE RADAR rassemble les résultats disponibles sur ses connecteurs actifs et conserve une longue liste au lieu de masquer tout ce qui dépasse un petit top. Commence avec une marque, une matière, une collection ou une référence. Ajuste ton budget. Puis explore les résultats du meilleur au moins bon. Les filtres restent en place pendant le défilement et les doublons sont retirés. Sauvegarde une trouvaille, crée une collection et reviens plus tard depuis l'historique. Pour une pièce rare, prends toujours le temps de vérifier les photos, les mesures, l'état, la provenance et les conditions de retour. LUXE RADAR accélère la recherche. Ton œil fait le reste.",
        (("CHERCHE LARGE", "Marque · matière · collection · référence"), ("DESCENDS", "La longue liste reste accessible"), ("FILTRE", "Sans perdre les résultats chargés"), ("COLLECTIONNE", "Garde tes trouvailles ensemble"), ("VÉRIFIE", "État · mesures · provenance")),
    ),
    Campaign(
        "transparence",
        "PAS DE FAUSSE PROMESSE.",
        "JUSTE UN RADAR PLUS CLAIR.",
        "LUXE RADAR référence plus de mille sites, mais seuls les connecteurs réellement testés restent actifs. Aujourd'hui, la recherche utilise Vinted, eBay, Grailed et soixante-sept behaviour. Les autres sites du catalogue ne fabriquent aucun résultat et ne sont jamais présentés comme fonctionnels sans test. Quand tu lances une recherche, le moteur récupère des annonces réelles, retire les doublons, classe la pertinence et affiche cinquante résultats par lot. Le total trouvé et le nombre affiché restent visibles. Un tri réorganise les données déjà obtenues au lieu de solliciter inutilement toutes les plateformes. Tes données personnelles restent dans ton navigateur. Tu peux les exporter ou les supprimer. Et si une source bloque l'accès, LUXE RADAR ne contourne ni captcha, ni protection. Une application utile commence par dire clairement ce qu'elle fait. Et ce qu'elle ne fait pas.",
        (("1 218 SITES", "Un catalogue, pas 1 218 faux connecteurs"), ("4 ACTIFS", "Uniquement les sources testées"), ("RÉSULTATS RÉELS", "Aucune annonce fabriquée"), ("DONNÉES LOCALES", "Exportables et supprimables"), ("TRANSPARENT", "Ce qui marche est clairement indiqué")),
    ),
)


def font(size: int, black: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BLACK if black else FONT_SEMIBOLD if size >= 25 else FONT_REGULAR, size)


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, minimum: int = 24) -> ImageFont.FreeTypeFont:
    while size > minimum and draw.textbbox((0, 0), text, font=font(size, True))[2] > max_width:
        size -= 2
    return font(size, True)


def backdrop(t: float, photo: Image.Image | None) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    if photo is not None:
        scale = max(W / photo.width, H / photo.height) * (1.02 + 0.035 * t / DURATION)
        resized = photo.resize((int(photo.width * scale), int(photo.height * scale)), Image.Resampling.LANCZOS)
        left = max(0, (resized.width - W) // 2)
        top = max(0, (resized.height - H) // 2)
        crop = resized.crop((left, top, left + W, top + H))
        crop = ImageEnhance.Brightness(crop).enhance(0.42)
        crop = ImageEnhance.Color(crop).enhance(0.75)
        image.paste(crop)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    sd.rectangle((0, 0, W, H), fill=(4, 6, 10, 82))
    sd.ellipse((350, -160, 930, 420), fill=(168, 255, 62, 36))
    sd.ellipse((-280, 700, 320, 1380), fill=(103, 232, 249, 22))
    shade = shade.filter(ImageFilter.GaussianBlur(70))
    image = Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB")
    return image


def brand(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((42, 40, 98, 96), 15, fill=LIME)
    draw.text((54, 52), "LR", font=font(21, True), fill="#071006")
    draw.text((116, 51), "LUXE", font=font(24, True), fill=WHITE)
    draw.text((202, 51), "RADAR", font=font(24, True), fill=LIME)
    attribution = "Voix SIWIS · CC BY 4.0"
    box = draw.textbbox((0, 0), attribution, font=font(13))
    draw.text(((W - (box[2] - box[0])) / 2, 1232), attribution, font=font(13), fill="#778293")


def centered(draw: ImageDraw.ImageDraw, text: str, y: int, face: ImageFont.FreeTypeFont, color: str = WHITE) -> None:
    box = draw.textbbox((0, 0), text, font=face)
    draw.text(((W - (box[2] - box[0])) / 2, y), text, font=face, fill=color)


def render_frame(campaign: Campaign, t: float, photo: Image.Image | None) -> Image.Image:
    image = backdrop(t, photo)
    draw = ImageDraw.Draw(image)
    brand(draw)
    if t < 6:
        centered(draw, campaign.hook, 260, fit_text(draw, campaign.hook, 640, 58), WHITE)
        centered(draw, campaign.subhook, 332, fit_text(draw, campaign.subhook, 640, 52), LIME)
        centered(draw, "LUXE RADAR", 1045, font(22, True), WHITE)
        centered(draw, "Mode & sneakers · recherche intelligente", 1090, font(17), MUTED)
        return image
    index = min(len(campaign.beats) - 1, int((t - 6) / ((DURATION - 12) / len(campaign.beats))))
    title, copy = campaign.beats[index]
    progress = ((t - 6) % ((DURATION - 12) / len(campaign.beats))) / ((DURATION - 12) / len(campaign.beats))
    y_shift = int(18 * (1 - min(1, progress * 3)))
    draw.rounded_rectangle((38, 260 + y_shift, 682, 720 + y_shift), 30, fill="#10151de8", outline="#303846", width=2)
    draw.text((72, 302 + y_shift), f"0{index + 1}", font=font(19, True), fill=LIME)
    draw.text((72, 365 + y_shift), title, font=fit_text(draw, title, 575, 54), fill=WHITE)
    draw.multiline_text((72, 455 + y_shift), copy, font=font(25), fill=MUTED, spacing=11)
    draw.rounded_rectangle((72, 610 + y_shift, 648, 622 + y_shift), 6, fill="#29313d")
    draw.rounded_rectangle((72, 610 + y_shift, 72 + int(576 * progress), 622 + y_shift), 6, fill=LIME)
    if t >= 54:
        draw.rounded_rectangle((58, 868, 662, 982), 24, fill=LIME)
        centered(draw, "ESSAIE GRATUITEMENT", 899, font(27, True), "#071006")
        centered(draw, "Trouve avant les autres.", 1032, font(23, True), WHITE)
    else:
        centered(draw, "Résultats réels · aucun faux site activé", 1084, font(17), MUTED)
    return image


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path), "-f", "null", "NUL"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if not match:
        raise RuntimeError(f"Durée audio introuvable pour {path}")
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def synthesize(campaign: Campaign, target: Path) -> None:
    config_path = VOICE_MODEL.with_suffix(".onnx.json")
    if not VOICE_MODEL.is_file() or not config_path.is_file():
        raise FileNotFoundError(
            "Modèle Piper absent. Lance : python -m piper.download_voices "
            f"--download-dir {VOICE_MODEL.parent} fr_FR-siwis-medium"
        )
    voice = PiperVoice.load(str(VOICE_MODEL))
    config = SynthesisConfig(volume=1.0, length_scale=1.08, noise_scale=0.58, noise_w_scale=0.72, normalize_audio=True)
    raw = target.with_name(f"{target.stem}_raw.wav")
    with wave.open(str(raw), "wb") as wav_file:
        voice.synthesize_wav(campaign.narration, wav_file, syn_config=config)
    tempo = max(0.5, min(2.0, media_duration(raw) / 55.5))
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(raw), "-filter:a", f"atempo={tempo:.6f}", str(target)],
        check=True,
        capture_output=True,
    )
    raw.unlink(missing_ok=True)


def make_music(target: Path, duration: int = DURATION) -> None:
    sample_rate = 44_100
    count = sample_rate * duration
    timeline = np.arange(count) / sample_rate
    audio = np.zeros(count, dtype=np.float64)
    chords = ((110.0, 138.59, 164.81), (98.0, 123.47, 146.83), (82.41, 110.0, 138.59), (92.5, 116.54, 146.83))
    for second in range(duration):
        part = slice(second * sample_rate, min((second + 1) * sample_rate, count))
        local = timeline[part]
        chord = chords[(second // 4) % len(chords)]
        pad = sum(np.sin(2 * np.pi * frequency * local) + 0.22 * np.sin(4 * np.pi * frequency * local) for frequency in chord) / len(chord)
        pulse = 0.55 + 0.45 * np.sin(2 * np.pi * 1.7 * local) ** 2
        audio[part] = 0.16 * pad * pulse
        for offset in (0.0, 0.5):
            start = int((second + offset) * sample_rate)
            end = min(start + int(0.17 * sample_rate), count)
            x = np.arange(end - start) / sample_rate
            audio[start:end] += 0.10 * np.sin(2 * np.pi * 58 * x) * np.exp(-24 * x)
    fade = np.minimum(1, np.arange(count) / sample_rate) * np.minimum(1, (count - np.arange(count)) / sample_rate)
    pcm = (np.clip(audio * fade, -1, 1) * 32767).astype("<i2")
    with wave.open(str(target), "wb") as handle:
        handle.setparams((1, 2, sample_rate, count, "NONE", ""))
        handle.writeframes(pcm.tobytes())


def render_video(campaign: Campaign, photo: Image.Image | None, silent: Path) -> None:
    writer = imageio.get_writer(silent, fps=FPS, codec="libx264", quality=8, pixelformat="yuv420p", macro_block_size=16)
    try:
        for frame_index in range(DURATION * FPS):
            writer.append_data(np.asarray(render_frame(campaign, frame_index / FPS, photo)))
    finally:
        writer.close()


def mix(silent: Path, voice: Path, music: Path, target: Path) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg, "-y", "-i", str(silent), "-i", str(voice), "-i", str(music),
        "-filter_complex",
        "[1:a]aresample=44100,volume=1.35,highpass=f=80,lowpass=f=12000,acompressor=threshold=-18dB:ratio=3:attack=10:release=180[voice];"
        "[2:a]volume=0.30[music];[voice][music]amix=inputs=2:duration=longest:weights='1 0.42',loudnorm=I=-14:TP=-1.5:LRA=8[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-t", str(DURATION),
        "-metadata", "comment=Voix française SIWIS (CC BY 4.0), synthèse locale Piper", "-movflags", "+faststart", str(target),
    ]
    subprocess.run(command, check=True)


async def generate(campaign: Campaign, photo: Image.Image | None, audio_only: bool = False) -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    voice = MEDIA / f"{campaign.slug}_voix.wav"
    music = MEDIA / f"{campaign.slug}_musique.wav"
    silent = MEDIA / f"{campaign.slug}_silencieux.mp4"
    target = MEDIA / f"luxe_radar_{campaign.slug}_60s.mp4"
    thumb = MEDIA / f"luxe_radar_{campaign.slug}_miniature.png"
    synthesize(campaign, voice)
    if not audio_only:
        make_music(music)
        render_video(campaign, photo, silent)
        render_frame(campaign, 2.5, photo).save(thumb)
    elif not silent.is_file() or not music.is_file():
        raise FileNotFoundError("Les pistes vidéo et musique existantes sont requises avec --audio-only")
    mix(silent, voice, music, target)
    shutil.copy2(target, PUBLIC / target.name)
    print(target)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", choices=[item.slug for item in CAMPAIGNS] + ["all"], default="all")
    parser.add_argument("--photo", type=Path, default=ASSETS / "reseller-campaign-v1.png")
    parser.add_argument("--audio-only", action="store_true", help="Réutilise les images et musiques déjà rendues")
    args = parser.parse_args()
    photo = Image.open(args.photo).convert("RGB") if args.photo.exists() else None
    selected = CAMPAIGNS if args.campaign == "all" else tuple(item for item in CAMPAIGNS if item.slug == args.campaign)
    for campaign in selected:
        await generate(campaign, photo, audio_only=args.audio_only)


if __name__ == "__main__":
    asyncio.run(main())
