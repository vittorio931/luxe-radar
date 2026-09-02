from io import BytesIO

from PIL import Image

from image_similarity import image_feature, similarity


def payload(color):
    image = Image.new("RGB", (32, 32), color)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def patterned_payload(background=(245, 245, 245), crop=False):
    image = Image.new("RGB", (180, 120), background)
    for x in range(45, 135):
        for y in range(18, 105):
            if (x - 90) ** 2 / 1900 + (y - 62) ** 2 / 1700 < 1:
                image.putpixel((x, y), (20, 70, 180))
    if crop:
        image = image.crop((28, 5, 152, 115)).resize((220, 180))
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def main():
    red = image_feature(payload((255, 0, 0)))
    assert similarity(red, red) == 100
    assert similarity(red, image_feature(payload((0, 0, 255)))) < 50
    product = image_feature(patterned_payload())
    assert similarity(product, image_feature(patterned_payload((220, 220, 220), crop=True))) > 75
    try:
        image_feature(b"not an image")
    except ValueError:
        pass
    else:
        raise AssertionError("Une image corrompue doit être rejetée")
    import app_web
    client = app_web.app.test_client()
    client.get("/")
    with client.session_transaction() as browser_session:
        csrf = browser_session["csrf_token"]
    token = app_web._cache_results([{
        "marketplace": "Test", "titre": "Image rouge", "prix": 10,
        "niveau_identite": "possible",
        "image": "https://example.test/red.png",
    }], csrf)
    original_limit = app_web.IMAGE_COMPARE_LIMIT
    app_web.IMAGE_COMPARE_LIMIT = 4
    spread = [{"marketplace": "A", "image": f"https://example.test/{index}.png"} for index in range(8)]
    spread += [{"marketplace": "B", "image": "https://example.test/b.png"}]
    candidates = app_web._image_rank_candidates(spread)
    assert len(candidates) == 4
    assert any(index == 8 for index, _url in candidates), candidates
    app_web.IMAGE_COMPARE_LIMIT = original_limit
    original_download = app_web.download_listing_image
    app_web.download_listing_image = lambda _url: payload((255, 0, 0))
    try:
        response = client.post(
            f"/api/results/{token}/image-rank",
            data={"image": (BytesIO(payload((255, 0, 0))), "query.png")},
            headers={"X-CSRF-Token": csrf},
        )
    finally:
        app_web.download_listing_image = original_download
    assert response.status_code == 200
    assert response.get_json()["compared"] == 1
    assert response.get_json()["results"][0]["similarite_image"] == 100
    original_analyse = app_web.analyse_visual_query
    app_web.analyse_visual_query = lambda _data, _mime: {
        "query": "pantalon Nike Trail noir", "brand": "Nike", "category": "pantalon",
        "model": "Trail", "colors": ["noir"], "confidence": .9,
    }
    try:
        understood = client.post(
            "/api/image-query",
            data={"image": (BytesIO(patterned_payload()), "product.jpg")},
            headers={"X-CSRF-Token": csrf},
        )
    finally:
        app_web.analyse_visual_query = original_analyse
    assert understood.status_code == 200
    assert understood.get_json()["query"] == "pantalon Nike Trail noir"
    print("OK - Similarité d'image et validation locale OK.")


if __name__ == "__main__":
    main()
