"""Safe, lightweight visual comparison for already-discovered listing images."""
from io import BytesIO
import math
from urllib.parse import urlparse

import requests
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def _normalise_band(values):
    """Min-max normalisation of a luminance band (robust to exposure shifts)."""
    minimum, maximum = min(values), max(values)
    span = maximum - minimum
    if span == 0:
        return values
    return tuple((value - minimum) / span for value in values)


def _pixels(image, mode, size):
    resized = image.convert(mode).resize(size, Image.Resampling.LANCZOS)
    channels = 255.0
    if mode == "RGB":
        return tuple(channel / channels for pixel in resized.getdata() for channel in pixel)
    return tuple(pixel / channels for pixel in resized.getdata())


def _colour_histogram(image):
    """Histogramme RGB 4x4x4, peu sensible au cadrage et au fond."""
    bins = [0.0] * 64
    pixels = list(image.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR).getdata())
    for red, green, blue in pixels:
        bins[(red // 64) * 16 + (green // 64) * 4 + (blue // 64)] += 1.0
    total = float(len(pixels) or 1)
    # Répéter légèrement l'histogramme lui donne un poids utile face aux
    # grilles spatiales plus longues, sans modèle lourd en production.
    normalised = tuple(value / total for value in bins)
    return normalised * 4


def image_feature(data):
    """Descripteur perceptuel léger : silhouette, couleur, centre et contours.

    Il tolère mieux les recadrages et les fonds différents que l'ancien simple
    redimensionnement pixel-à-pixel, tout en restant compatible avec Render.
    """
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image invalide ou trop volumineuse.")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in ALLOWED_FORMATS:
                raise ValueError("Format d'image non pris en charge.")
            source.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            square = ImageOps.pad(image, (320, 320), color=(245, 245, 245), method=Image.Resampling.LANCZOS)
            width, height = square.size
            centre = square.crop((width * .12, height * .12, width * .88, height * .88))
            luminance = _normalise_band(_pixels(square, "L", (28, 28)))
            colour = _pixels(square, "RGB", (16, 16))
            centre_colour = _pixels(centre, "RGB", (16, 16))
            edges = _pixels(square.convert("L").filter(ImageFilter.FIND_EDGES), "L", (16, 16))
            return luminance + colour + centre_colour + edges + _colour_histogram(square)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Image JPEG, PNG ou WebP invalide.") from exc


def similarity(first, second):
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)) / len(first))
    return max(0.0, min(100.0, (1.0 - distance) * 100))


def download_listing_image(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    try:
        response = requests.get(url, timeout=(2, 4), stream=True, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg"})
        if response.status_code != 200 or not response.headers.get("Content-Type", "").lower().startswith("image/"):
            return None
        chunks, total = [], 0
        for chunk in response.iter_content(16 * 1024):
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException:
        return None
