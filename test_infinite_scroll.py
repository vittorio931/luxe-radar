import re
import time
from pathlib import Path
from unittest.mock import patch

import app_web
import index_engine
from app_web import RESULT_BATCH_SIZE, SUBSCRIPTION_PLANS, _cache_results, _normalized_reference, _rank_by_reference, _safe_number, app
from marketplaces.catalog import _normalized_site, get_sites
from marketplaces.connectors import get_available_connectors
from radar_engine import _cle_unique_multi, _selection_diversifiee


ROOT = Path(__file__).resolve().parent


def _search_token_from(html):
    match = re.search(r'"token":\s*"([0-9a-f]{32})"', html)
    return match.group(1) if match else None


def _await_search(client, token, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/results/{token}/status").get_json()
        if not status.get("pending"):
            return client.get(f"/api/results/{token}?offset=0&limit=200").get_json()["results"]
        time.sleep(0.05)
    raise AssertionError(f"recherche progressive non terminée en {timeout:g}s: {status}")


def sample_results(count=170):
    marketplaces = ["eBay"] * 120 + ["Vinted"] * 20 + ["Grailed"] * 15 + ["67behaviour"] * 15
    return [
        {
            "marketplace": marketplaces[i], "titre": f"Annonce {i}",
            "prix": (i % 80) + 1, "score": 100 - (i / 2),
            "score_confiance": 50 + (i % 45), "categorie": "BONNE AFFAIRE",
            "niveau_identite": "possible",
            "lien": f"https://example.com/{i}",
        }
        for i in range(count)
    ]


def main():
    assert _safe_number(float("nan"), 7) == 7
    assert _safe_number(float("inf"), 8) == 8
    reference_results = [{"titre": "Nike Pegasus DM4652 040", "score": 80}, {"titre": "Nike Trail sans ref", "score": 90}]
    assert _normalized_reference("DM4652-040") == "dm4652040"
    assert _rank_by_reference(reference_results, "DM4652-040")[0] is reference_results[0]
    assert _rank_by_reference(reference_results, "DM4652-040", strict=True) == [reference_results[0]]
    safety_results = [{"titre": "Nike DM4652-040", "categorie": "A IGNORER"}, {"titre": "Nike standard", "categorie": "BONNE AFFAIRE"}]
    assert _rank_by_reference(safety_results, "DM4652-040") == [safety_results[1], safety_results[0]]
    strict_client = app.test_client()
    strict_client.get("/")
    with strict_client.session_transaction() as strict_session:
        strict_csrf = strict_session["csrf_token"]
    mocked_results = [
        {"marketplace": "eBay", "titre": "Nike sans référence", "prix": 20, "score": 95, "score_confiance": 90, "niveau_identite": "possible", "lien": "https://example.com/no"},
        {"marketplace": "eBay", "titre": "Nike DM4652 040", "prix": 25, "score": 80, "score_confiance": 80, "niveau_identite": "possible", "lien": "https://example.com/ref"},
    ]
    # L'index local persistant peut contenir des offres d'exécutions
    # précédentes : on le désactive pour garder ce test déterministe, sinon une
    # carte indexée pourrait dédoublonner les résultats simulés.
    with patch("app_web.rechercher_multi_marketplaces", return_value=mocked_results) as mocked_search, \
            patch.object(index_engine, "index_enabled", return_value=False), \
            patch("app_web._index_results_async"):
        strict_response = strict_client.post("/", data={"csrf_token": strict_csrf, "marque": "Nike", "prix": "50", "plateforme": "eBay", "reference_exacte": "DM4652-040", "reference_stricte": "1"})
        strict_html = strict_response.get_data(as_text=True)
        assert strict_response.status_code == 200
        strict_token = _search_token_from(strict_html)
        assert strict_token and '"pending": true' in strict_html
        strict_results = _await_search(strict_client, strict_token)
        strict_titles = [item["titre"] for item in strict_results]
        assert "Nike DM4652 040" in strict_titles and "Nike sans référence" not in strict_titles
        assert mocked_search.call_args.kwargs["marque"] == "Nike DM4652-040"
    with patch("app_web.rechercher_multi_marketplaces") as rejected_search:
        invalid_reference = strict_client.post("/", data={"csrf_token": strict_csrf, "marque": "Nike", "prix": "50", "plateforme": "eBay", "reference_exacte": "--"})
    assert invalid_reference.status_code == 200 and "Référence exacte invalide." in invalid_reference.get_data(as_text=True)
    rejected_search.assert_not_called()
    ebay_source = (ROOT / "marketplaces" / "connectors" / "ebay.py").read_text(encoding="utf-8")
    assert "min(\n                limit,\n                100," in ebay_source
    results = sample_results()
    ranked, counts = _selection_diversifiee(results, len(results))
    assert len(ranked) == 170 and counts["eBay"] == 120
    assert ranked[0]["score"] == 100
    assert len({_cle_unique_multi(item) for item in ranked}) == 170
    score_sorted = sorted(results, key=lambda x: (-x["score"], -x["score_confiance"], x["prix"]))
    pure, _pure_counts = _selection_diversifiee(score_sorted, len(score_sorted), diversifie=False)
    assert [item["titre"] for item in pure] == [item["titre"] for item in score_sorted]
    assert [item["score"] for item in pure] == sorted((item["score"] for item in results), reverse=True)
    assert _cle_unique_multi({"marketplace": "eBay", "lien": "https://x/item?track=1"}) == \
           _cle_unique_multi({"marketplace": "eBay", "lien": "https://x/item?track=2"})

    client = app.test_client()
    client.get("/")
    with client.session_transaction() as browser_session:
        csrf_token = browser_session["csrf_token"]
    token = _cache_results(ranked, csrf_token)
    pages = [client.get(f"/api/results/{token}?offset={offset}").get_json() for offset in (0, 50, 100, 150)]
    assert [len(page["results"]) for page in pages] == [50, 50, 50, 20]
    all_urls = [item["lien"] for page in pages for item in page["results"]]
    assert len(all_urls) == len(set(all_urls)) == 170
    assert pages[-1]["has_more"] is False and pages[-1]["total"] == 170

    batch200 = client.get(f"/api/results/{token}?offset=0&limit=200").get_json()
    assert len(batch200["results"]) == 170 and batch200["has_more"] is False
    batch100 = client.get(f"/api/results/{token}?offset=0&limit=100").get_json()
    assert len(batch100["results"]) == 100 and batch100["next_offset"] == 100 and batch100["has_more"] is True
    assert client.get(f"/api/results/{token}?offset=0&limit=201").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&limit=bad").status_code == 400

    price_page = client.get(f"/api/results/{token}?offset=0&sort=price_asc").get_json()
    assert [item["prix"] for item in price_page["results"]] == sorted(item["prix"] for item in price_page["results"])
    score_page = client.get(f"/api/results/{token}?offset=0&sort=score").get_json()
    assert score_page["results"][0]["score"] >= score_page["results"][-1]["score"]
    ebay_page = client.get(f"/api/results/{token}?offset=0&marketplace=eBay").get_json()
    assert ebay_page["total"] == 120 and all(item["marketplace"] == "eBay" for item in ebay_page["results"])
    advanced_page = client.get(f"/api/results/{token}?offset=0&marketplace=Toutes&price_min=40&exclude=Annonce%2041").get_json()
    assert advanced_page["results"] and all(item["prix"] >= 40 for item in advanced_page["results"])
    assert all("Annonce 41" not in item["titre"] for item in advanced_page["results"])
    advanced_next = client.get(f"/api/results/{token}?offset=50&marketplace=Toutes&price_min=40&exclude=Annonce%2041").get_json()
    assert all(item["prix"] >= 40 and "Annonce 41" not in item["titre"] for item in advanced_next["results"])
    assert client.get(f"/api/results/{token}?offset=bad").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&marketplace=Inconnue").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&price_min=nan").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&price_min=1000001").status_code == 400
    max_page = client.get(f"/api/results/{token}?offset=0&price_max=10").get_json()
    assert max_page["results"] and all(item["prix"] <= 10 for item in max_page["results"])
    assert client.get(f"/api/results/{token}?offset=0&price_max=nan").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&price_max=1000001").status_code == 400
    exact_page = client.get(f"/api/results/{token}?offset=0&price_exact=45&price_tolerance=5").get_json()
    assert exact_page["results"] and all(abs(item["prix"] - 45) <= 5 for item in exact_page["results"])
    exact_strict = client.get(f"/api/results/{token}?offset=0&price_exact=45").get_json()
    assert exact_strict["results"] and all(item["prix"] == 45 for item in exact_strict["results"])
    assert client.get(f"/api/results/{token}?offset=0&price_exact=nan").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&price_exact=1000001").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&price_exact=45&price_tolerance=nan").status_code == 400
    assert client.get(f"/api/results/{token}?offset=0&price_exact=45&price_tolerance=101").status_code == 400
    range_page = client.get(f"/api/results/{token}?offset=0&price_min=20&price_max=30").get_json()
    assert range_page["results"] and all(20 <= item["prix"] <= 30 for item in range_page["results"])
    assert client.get("/api/results/00000000000000000000000000000000?offset=0").status_code == 404
    other_client = app.test_client()
    other_client.get("/")
    assert other_client.get(f"/api/results/{token}?offset=0").status_code == 404
    shared = client.get("/?q=Nike%20Trail&price=50&marketplace=eBay").get_data(as_text=True)
    assert 'value="Nike Trail"' in shared and 'value="50"' in shared
    assert '<option value="eBay" selected>' in shared and 'id="shown-count"' not in shared

    calls = []
    with patch("app_web.rechercher_multi_marketplaces", side_effect=lambda **kwargs: calls.append(kwargs) or sample_results()), \
            patch("app_web._progressive_source_order", return_value=["eBay"]), \
            patch.object(index_engine, "index_enabled", return_value=False), \
            patch("app_web._index_results_async"):
        response = client.post("/", data={"marque": "Nike Trail", "prix": "50", "plateforme": "Toutes", "csrf_token": csrf_token})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="sort-results"' in html and 'id="filter-marketplace"' in html
        assert 'id="price-min-filter"' in html and 'id="price-max-filter"' in html and 'id="exclude-filter"' in html
        assert 'id="price-exact-filter"' in html and 'id="price-tolerance-filter"' in html
        assert 'id="batch-size"' in html and '<option value="50" selected>' in html
        # V4 : la page s'affiche immédiatement puis se remplit en arrière-plan.
        assert 'id="shown-count">' in html and 'id="total-count">' in html and '"pending": true' in html
        assert all(section in html for section in (
            'id="view-dashboard"', 'id="view-radar"', 'id="view-favorites"',
            'id="view-history"', 'id="view-settings"',
            'id="view-alerts"', 'id="view-studio"',
            'id="view-portfolio"', 'id="command-palette"',
            'id="view-pricing"', 'id="checkout-modal"',
        ))
        assert '/static/app.css' in html and '/static/app.js' in html
        assert 'id="language-toggle"' in html and 'data-i18n="nav.dashboard"' in html
        assert 'data-reseller-nav' in html and 'id="experience-toggle"' in html
        assert 'class="skip-link"' in html and 'id="main-content" tabindex="-1"' in html
        assert 'id="load-status" role="status" aria-live="polite"' in html
        post_token = _search_token_from(html)
        assert post_token
        live_results = _await_search(client, post_token)
        assert len(live_results) == 170 and all("Annonce" in item["titre"] for item in live_results)
        plans = client.get('/api/billing/plans').get_json()
        assert plans['plans']['pro']['monthly'] == SUBSCRIPTION_PLANS['pro']['monthly']
        import os
        os.environ.pop('STRIPE_SECRET_KEY', None)
        checkout = client.post('/api/billing/checkout', json={'plan': 'pro', 'cycle': 'monthly'}, headers={'X-CSRF-Token': csrf_token})
        assert checkout.status_code in {501, 503} and checkout.get_json().get('code')
        assert client.get('/static/manifest.webmanifest').status_code == 200
        worker = client.get('/sw.js')
        assert worker.status_code == 200 and worker.headers.get('Service-Worker-Allowed') == '/'
        capabilities = client.get('/api/account/capabilities').get_json()
        assert capabilities['accounts_ready'] is False and capabilities['storage'] == 'local_browser'
        health_response = client.get('/api/health')
        health = health_response.get_json()
        assert health['status'] == 'ok' and health['connectors'] >= 4 and health['catalog_sites'] >= 1000
        assert health_response.headers.get('X-Request-ID') and 'app;dur=' in health_response.headers.get('Server-Timing', '')
        root_response = client.get('/')
        required_headers = {'Content-Security-Policy', 'X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy', 'Permissions-Policy'}
        assert all(header in root_response.headers for header in required_headers)
        assert "frame-ancestors 'none'" in root_response.headers['Content-Security-Policy']
        assert client.post('/', data={'marque': 'Nike', 'prix': '50', 'plateforme': 'Toutes'}).status_code == 403
        assert client.get(f'/api/results/{token}?offset=999999').status_code == 400
        assert client.get(f'/api/results/{token}?offset=0&sort=__bad__').status_code == 400
        assert len(calls) == 1
        lots_response = client.post("/", data={"marque": "Nike Trail", "prix": "50", "plateforme": "Toutes", "lots": "100", "csrf_token": csrf_token})
        lots_html = lots_response.get_data(as_text=True)
        assert 'id="shown-count">' in lots_html and 'id="total-count">' in lots_html and '"pending": true' in lots_html
        assert '<option value="100" selected>' in lots_html and "window.LUXE_RADAR" in lots_html
        lots_token = _search_token_from(lots_html)
        assert lots_token
        lots_results = _await_search(client, lots_token)
        assert len(lots_results) == 170

    active = set(get_available_connectors())
    assert {"Vinted", "eBay", "Grailed", "67behaviour"}.issubset(active)
    catalog = get_sites()
    assert all(site["status"] in {"active", "off", "to_test", "blocked", "non_implemented"} for site in catalog)
    synthetic = [
        _normalized_site({"name": f"Site {i}", "base_url": f"https://site{i}.example", "status": "off"})
        for i in range(1005)
    ]
    assert len(synthetic) == 1005 and all(site and not site["enabled"] for site in synthetic)
    assert RESULT_BATCH_SIZE == 50
    print("OK - 4 lots, tris, filtre, doublons, eBay et catalogue 1000+ valides.")


if __name__ == "__main__":
    main()
