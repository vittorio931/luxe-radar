from io import BytesIO

from PIL import Image

from image_similarity import image_feature, similarity


def payload(color):
    image = Image.new("RGB", (32, 32), color)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def main():
    red = image_feature(payload((255, 0, 0)))
    assert similarity(red, red) == 100
    assert similarity(red, image_feature(payload((0, 0, 255)))) < 50
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
        "image": "https://example.test/red.png",
    }], csrf)
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
    print("OK - Similarité d'image et validation locale OK.")


if __name__ == "__main__":
    main()
