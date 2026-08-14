"""Safe, lightweight visual comparison for already-discovered listing images."""
from io import BytesIO
import math
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def _normalise_band(values):
    """Min-max normalisation of a luminance band (robust to exposure shifts)."""
    minimum, maximum = min(values), max(values)
    span = maximum - minimum
    if span == 0:
        return values
    return tuple((value - minimum) / span for value in values)


def image_feature(data):
    """Visual feature = structure (28x28 luminance) + colour (16x16 RGB)."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image invalide ou trop volumineuse.")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in ALLOWED_FORMATS:
                raise ValueError("Format d'image non pris en charge.")
            source.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            color = tuple(channel / 255 for pixel in image.convert("RGB").resize((16, 16)).getdata() for channel in pixel)
            luminance = tuple(pixel / 255 for pixel in image.convert("L").resize((28, 28)).getdata())
            return _normalise_band(luminance) + color
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
