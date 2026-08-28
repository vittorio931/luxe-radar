from pathlib import Path

from app_web import app


def main():
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    template = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    required_html = (
        'id="condition-filter"',
        'id="view-feed"',
        'id="personal-feed"',
        'id="similar-panel"',
        'data-view="feed"',
    )
    for marker in required_html:
        assert marker in template, marker

    script = client.get("/static/app.js").get_data(as_text=True)
    required_script = (
        "priceObservations",
        "observePrices",
        "priceSignal",
        "conditionFor",
        "showSimilar",
        "renderPersonalFeed",
        "Très bon prix",
    )
    for marker in required_script:
        assert marker in script, marker

    print("OK - historique prix, baisses, état, verdict, similaires et feed présents.")


if __name__ == "__main__":
    main()
