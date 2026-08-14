"""Contrôles rapides des médias publicitaires intégrés au site."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

from app_web import app


ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT / "static" / "campaign"
SLUGS = (
    "revendeur",
    "bonne_affaire",
    "gain_de_temps",
    "acheteur_malin",
    "chasse_vintage",
    "transparence",
)
CAPTIONS = (
    "revendeur-fr.vtt",
    "bonne-affaire-fr.vtt",
    "gain-de-temps-fr.vtt",
    "acheteur-malin-fr.vtt",
    "chasse-vintage-fr.vtt",
    "transparence-fr.vtt",
    "revendeur-en.vtt",
    "bonne-affaire-en.vtt",
    "gain-de-temps-en.vtt",
    "acheteur-malin-en.vtt",
    "chasse-vintage-en.vtt",
    "transparence-en.vtt",
)


def probe(path: Path) -> str:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    return result.stderr


def audio_levels(path: Path) -> tuple[float, float]:
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "NUL"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    mean_match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    peak_match = re.search(r"max_volume:\s*(-?[\d.]+) dB", result.stderr)
    assert mean_match and peak_match
    return float(mean_match.group(1)), float(peak_match.group(1))


def main() -> None:
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert html.count("<video controls") == len(SLUGS)
    assert html.count('kind="captions"') == len(CAPTIONS)
    assert html.count('preload="none"') == len(SLUGS)
    assert html.count('data-src="/static/campaign/') == len(SLUGS)
    assert '<source src="/static/campaign/' not in html
    assert ' poster="/static/campaign/' not in html
    assert html.count('_miniature.webp') == len(SLUGS)
    assert html.count('class="campaign-download"') == len(SLUGS)
    assert 'aria-labelledby="campaign-kit-title"' in html
    assert 'class="campaign-guide"' in html
    guide = CAMPAIGN / "guide-publication.txt"
    guide_text = guide.read_text(encoding="utf-8")
    assert "ATTRIBUTION À CONSERVER" in guide_text and "CC BY 4.0" in guide_text
    guide_response = client.get("/static/campaign/guide-publication.txt")
    assert guide_response.status_code == 200 and guide_response.content_type.startswith("text/plain")
    assert guide_response.headers["Cache-Control"] == "public, max-age=86400"
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "Ready to publish." in app_js and "'Transparence':'Transparency'" in app_js
    assert "campaignObserver ||= new IntersectionObserver" in app_js
    assert "campaignObserver.unobserve(entry.target)" in app_js
    app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    assert ".campaign-download{display:flex" in app_css and "min-height:44px" in app_css
    for slug in SLUGS:
        video = CAMPAIGN / f"luxe_radar_{slug}_60s.mp4"
        poster = CAMPAIGN / f"luxe_radar_{slug}_miniature.webp"
        assert video.is_file() and 1_000_000 < video.stat().st_size < 15_000_000
        assert poster.is_file() and 10_000 < poster.stat().st_size < 100_000
        with Image.open(poster) as poster_image:
            assert poster_image.size == (720, 1280) and poster_image.format == "WEBP"
        metadata = probe(video)
        assert re.search(r"Duration: 00:01:00\.0\d", metadata)
        assert "Video: h264" in metadata and "720x1280" in metadata
        assert "Audio: aac" in metadata and "44100 Hz" in metadata
        assert "Voix française SIWIS (CC BY 4.0)" in metadata
        mean_db, peak_db = audio_levels(video)
        assert -18.0 <= mean_db <= -11.0, (video.name, mean_db)
        assert -3.0 <= peak_db <= -0.8, (video.name, peak_db)
        response = client.get(f"/static/campaign/{video.name}", headers={"Range": "bytes=0-1023"})
        assert response.status_code == 206 and len(response.data) == 1024
        assert response.headers["Cache-Control"] == "public, max-age=86400"
    for caption in CAPTIONS:
        path = CAMPAIGN / caption
        assert path.read_text(encoding="utf-8").startswith("WEBVTT\n")
        response = client.get(f"/static/campaign/{caption}")
        assert response.status_code == 200 and response.content_type.startswith("text/vtt")
    print("OK - 6 vidéos de 60 s, audio, miniatures, sous-titres et streaming validés.")


if __name__ == "__main__":
    main()
