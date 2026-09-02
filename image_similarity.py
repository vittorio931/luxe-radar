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
    """Histogramme HSV : plus stable quand l'éclairage de la photo change."""
    bins = [0.0] * 96
    pixels = list(image.convert("HSV").resize((64, 64), Image.Resampling.BILINEAR).getdata())
    for hue, saturation, value in pixels:
        bins[(hue // 32) * 12 + (saturation // 86) * 4 + (value // 64)] += 1.0
    total = float(len(pixels) or 1)
    return tuple(value / total for value in bins)


def _difference_hash(image):
    pixels = list(image.convert("L").resize((17, 16), Image.Resampling.LANCZOS).getdata())
    return tuple(pixels[row * 17 + column] > pixels[row * 17 + column + 1]
                 for row in range(16) for column in range(16))


def _view_feature(image):
    grey = image.convert("L")
    return {
        "luma": _normalise_band(_pixels(grey, "L", (24, 24))),
        "edges": _normalise_band(_pixels(grey.filter(ImageFilter.FIND_EDGES), "L", (20, 20))),
        "colour": _pixels(image, "RGB", (10, 10)),
        "histogram": _colour_histogram(image),
        "hash": _difference_hash(image),
    }


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
            contained = ImageOps.pad(image, (256, 256), color=(245, 245, 245), method=Image.Resampling.LANCZOS)
            cropped = ImageOps.fit(image, (256, 256), method=Image.Resampling.LANCZOS, centering=(.5, .5))
            return (_view_feature(contained), _view_feature(cropped))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Image JPEG, PNG ou WebP invalide.") from exc


def _vector_similarity(first, second):
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)) / max(1, len(first)))
    return max(0.0, 1.0 - distance)


def _view_similarity(first, second):
    hash_score = 1.0 - sum(a != b for a, b in zip(first["hash"], second["hash"])) / len(first["hash"])
    histogram_score = sum(min(a, b) for a, b in zip(first["histogram"], second["histogram"]))
    return (
        .20 * _vector_similarity(first["luma"], second["luma"])
        + .18 * _vector_similarity(first["edges"], second["edges"])
        + .26 * _vector_similarity(first["colour"], second["colour"])
        + .28 * histogram_score
        + .08 * hash_score
    )


def similarity(first, second):
    """Meilleur accord entre vue contenue et vue recadrée des deux images."""
    score = max(_view_similarity(left, right) for left in first for right in second)
    return max(0.0, min(100.0, score * 100))


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
