"""Contrats du Bot Vinted : index uniquement, validation et sécurité."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app_web


def main():
    app_web.app.testing = True
    client = app_web.app.test_client()
    root = client.get("/")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    endpoint = "/api/vinted-bot/scan"
    assert client.post(endpoint, json={"query": "On Cloud 5", "price_max": 120}).status_code == 403
    assert client.post(endpoint, json={"query": "x", "price_max": 120}, headers=headers).status_code == 400
    assert client.post(endpoint, json={"query": "On Cloud 5", "price_max": -1}, headers=headers).status_code == 400
    result = SimpleNamespace(results=[{
        "marketplace": "Vinted", "titre": "On Cloud 5 noir",
        "prix": 75, "devise": "EUR",
        "lien": "https://www.vinted.fr/items/777-on-cloud-5",
    }])
    with patch.object(app_web.index_engine, "search", return_value=result) as search:
        response = client.post(endpoint, json={"query": "On Cloud 5", "price_max": 120}, headers=headers)
    data = response.get_json()
    assert response.status_code == 200 and data["source"] == "Vinted" and data["count"] == 1
    assert data["mode"] == "indexed_offers" and data["offers"][0]["quick_buy_available"] is True
    assert search.call_args.kwargs["marketplace"] == "Vinted"
    many = SimpleNamespace(results=[dict(result.results[0], titre=f"On Cloud 5 {index}", lien=f"https://www.vinted.fr/items/{1000 + index}-cloud") for index in range(150)])
    with patch.object(app_web.index_engine, "search", return_value=many):
        page = client.post(endpoint, json={"query": "On Cloud 5", "price_max": 120, "offset": 100, "limit": 100}, headers=headers).get_json()
    assert len(page["offers"]) == 50 and page["next_offset"] == 150 and page["has_more"] is False
    with patch.object(app_web.index_engine, "search", side_effect=OSError("index offline")):
        assert client.post(endpoint, json={"query": "On Cloud 5", "price_max": 120}, headers=headers).status_code == 503
    live_connector = SimpleNamespace(search=lambda **kwargs: result.results)
    app_web.app.testing = False
    try:
        with patch.object(app_web.index_engine, "search", side_effect=OSError("index offline")), \
             patch.object(app_web, "get_connector", return_value=live_connector):
            live_response = client.post(
                endpoint,
                json={"query": "On Cloud 5", "price_max": 120, "live_page": 3},
                headers=headers,
            )
        live_data = live_response.get_json()
        assert live_response.status_code == 200 and live_data["count"] == 1
        assert live_data["index_available"] is False and live_data["live_available"] is True
        assert live_data["live_page"] == 3
    finally:
        app_web.app.testing = True
    html = root.get_data(as_text=True)
    script = Path("static/vinted-bot.js").read_text(encoding="utf-8")
    app_script = Path("static/app.js").read_text(encoding="utf-8")
    assert "vinted-bot-mount" in html and "BOT DE SURVEILLANCE" in script and "Aucun achat automatique" in script
    assert html.index('data-view="vintedbot"') < html.index('data-view="favorites"')
    assert 'id="view-vintedbot"' in html and "'vintedbot'" in app_script
    assert "setInterval" in script and "IntersectionObserver" in script and "/api/vinted-bot/scan" in script
    assert "live_page" in script and "visibilitychange" in script
    persisted = {"id": "watch-1", "active": True, "query": "On Cloud 5", "price_max": 120}
    with patch.object(app_web.vinted_watch, "save_watch", return_value=persisted):
        watch_response = client.put(
            "/api/vinted-bot/watch",
            json={"query": "On Cloud 5", "price_max": 120, "interval": 300},
            headers=headers,
        )
    assert watch_response.status_code == 201 and watch_response.get_json()["watch"]["active"]
    with patch.object(app_web.vinted_watch, "alerts", return_value=[result.results[0]]):
        alert_response = client.get("/api/vinted-bot/alerts?mark_read=1")
    assert alert_response.status_code == 200 and alert_response.get_json()["count"] == 1
    assert "/api/vinted-bot/watch" in script and "/api/vinted-bot/alerts" in script
    assert "password" not in script.casefold() and "cookie" not in script.casefold()
    print("[OK] Bot Vinted : validation, index réel, CSRF, panne et UI validés")


if __name__ == "__main__":
    main()
