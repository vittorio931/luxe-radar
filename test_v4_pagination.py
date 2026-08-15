"""Test V4 : pagination hybride partageable + profil de capacités des sources.

Offline (recherches simulées, un seul worker eBay) : tokens en session,
réutilisation signature, pages serveur /search?page=N, calage hors bornes,
/api/sources/health.
"""
import json
import time

from unittest.mock import patch

from app_web import SEARCH_PAGE_SIZE, _search_signature, app


def sample_results(count=170):
    marketplaces = ["eBay"] * 120 + ["Vinted"] * 20 + ["Grailed"] * 15 + ["67behaviour"] * 15
    return [
        {
            "marketplace": marketplaces[i], "titre": f"Annonce {i}",
            "prix": (i % 80) + 1, "score": 100 - (i / 2),
            "score_confiance": 50 + (i % 45), "categorie": "BONNE AFFAIRE",
            "niveau_identite": "fort",
            "lien": f"https://example.com/{i}",
        }
        for i in range(count)
    ]


def _boot(html):
    start = html.index("window.LUXE_RADAR")
    payload = html[start:].split(";", 1)[0].split("=", 1)[1].strip()
    return json.loads(payload)


def _wait_done(client, token, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/results/{token}/status").get_json()
        if status and not status["pending"]:
            return status
        time.sleep(0.15)
    raise AssertionError("recherche non stabilisée (pending)")


def main():
    assert SEARCH_PAGE_SIZE == 50

    client = app.test_client()
    client.get("/")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    with patch(
        "app_web.rechercher_multi_marketplaces",
        side_effect=lambda **kwargs: sample_results(),
    ) as mocked, patch(
        "app_web._progressive_source_order", return_value=["eBay"]
    ):
        posted = client.post("/", data={
            "csrf_token": csrf, "marque": "Nike Trail", "prix": "50",
            "plateforme": "Toutes",
        })
        assert posted.status_code == 200
        html = posted.get_data(as_text=True)
        boot = _boot(html)
        assert boot["page"] == 1 and boot["pageSize"] == 50
        for marker in ("result-pager", "pager-prev", "pager-current", "pager-total", "pager-next"):
            assert f'id="{marker}"' in html

        with client.session_transaction() as session:
            first_token = session["lr_search_token"]
            first_signature = session["lr_search_signature"]
        assert first_token and first_signature
        assert first_signature == _search_signature("Nike Trail", "50", "Toutes", "", False)
        _wait_done(client, first_token)
        assert mocked.call_count == 1
        settled = _wait_done(client, first_token)
        expected_total = settled["total"]
        expected_pages = (expected_total + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE
        assert expected_total >= 100

        page1 = client.get("/search?q=Nike%20Trail&price=50&marketplace=Toutes&page=1")
        boot1 = _boot(page1.get_data(as_text=True))
        assert boot1["page"] == 1 and boot1["totalPages"] == expected_pages and boot1["initialCount"] == 50
        assert mocked.call_count == 1

        page2 = client.get("/search?q=Nike%20Trail&price=50&marketplace=Toutes&page=2")
        assert page2.status_code == 200
        boot2 = _boot(page2.get_data(as_text=True))
        assert boot2["page"] == 2 and boot2["totalPages"] == expected_pages and boot2["initialCount"] == 50
        with client.session_transaction() as session:
            assert session["lr_search_token"] == first_token
            assert session["lr_search_signature"] == first_signature
        assert mocked.call_count == 1

        page0 = client.get("/search?q=Nike%20Trail&price=50&page=0")
        assert _boot(page0.get_data(as_text=True))["page"] == 1
        assert mocked.call_count == 1

        page9000 = client.get("/search?q=Nike%20Trail&price=50&page=9000")
        assert page9000.status_code == 200
        boot9000 = _boot(page9000.get_data(as_text=True))
        last_count = expected_total - (expected_pages - 1) * SEARCH_PAGE_SIZE
        assert boot9000["page"] == expected_pages and boot9000["initialCount"] == last_count
        assert mocked.call_count == 1

        fresh = client.get("/search?q=Nike%20Air%20Max&price=80&page=2")
        assert fresh.status_code == 200
        assert _boot(fresh.get_data(as_text=True))["page"] == 2
        with client.session_transaction() as session:
            fresh_token = session["lr_search_token"]
            assert fresh_token != first_token
        _wait_done(client, fresh_token)
        assert mocked.call_count == 2

    empty = client.get("/search?page=2")
    assert empty.status_code == 200
    assert "Indique un produit, une marque ou une référence." in empty.get_data(as_text=True)

    health = client.get("/api/sources/health")
    assert health.status_code == 200 and health.is_json
    sources = health.get_json()["sources"]
    for required in ("eBay", "Vinted", "Grailed", "67behaviour"):
        assert required in sources, required
        assert isinstance(sources[required]["supports_pagination"], bool)
        assert int(sources[required]["expansion_page_size"]) > 0
        assert int(sources[required]["max_pages"]) >= 1
        assert "ok" in sources[required]["health"]
    assert sources["eBay"]["supports_pagination"] is True
    assert sources["67behaviour"]["supports_pagination"] is False
    assert sources["eBay"]["expansion_page_size"] == 50

    print("OK - V4 pagination hybride (tokens, pages serveur, calage, health) validée.")


if __name__ == "__main__":
    main()
