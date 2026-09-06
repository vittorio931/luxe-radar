"""20 scénarios critiques de Quick Buy, sans réseau ni achat réel."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_web
import quick_buy


def offer(**changes):
    data = {"marketplace": "Vinted", "titre": "On Cloud 5 homme noir taille 42",
            "prix": 80, "devise": "EUR", "lien": "https://www.vinted.fr/items/123-on-cloud-5",
            "disponible": True, "frais": 4, "seller_type": "individual"}
    data.update(changes)
    return data


def check_provider(item=None, prefs=None):
    return quick_buy.VintedPurchaseProvider().check_offer(item or offer(), quick_buy.sanitise_preferences(prefs or {}))


def main():
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"QUICK_BUY_DB": str(Path(directory) / "qb.sqlite3"), "QUICK_BUY_ENABLED": "true", "QUICK_BUY_LAUNCH_FREE": "true"}):
        # 1–8 : état réel de l'offre et critères.
        assert check_provider()[0] is True                                      # offre valide
        assert check_provider(offer(disponible=False))[1] == "ITEM_SOLD"         # vendue
        assert check_provider(prefs={"item_price_max": 70})[1] == "PRICE_CHANGED" # prix augmenté/hors limite
        assert check_provider(prefs={"item_price_max": 90})[0] is True            # prix baissé
        assert check_provider(prefs={"fees_max": 2})[1] == "FEES_CHANGED"         # frais changés
        assert check_provider(offer(lien="https://evil.example/items/1"))[1] == "UNAVAILABLE" # redirection sûre
        assert check_provider(offer(prix=None))[1] == "UNKNOWN_STATE"             # état inconnu
        assert check_provider(prefs={"required_keywords": ["femme"]})[1] == "CRITERIA_MISMATCH"

        client = app_web.app.test_client()
        root = client.get("/")
        with client.session_transaction() as session:
            csrf = session["csrf_token"]
        app_web._cache_results([offer()], owner=csrf)
        key = quick_buy.offer_key(offer())
        headers = {"X-CSRF-Token": csrf}
        post = lambda request_id, **extra: client.post("/api/quick-buy/check", json={"offer_key": key, "quick_buy_request_id": request_id, **extra}, headers=headers)

        # 9–16 : confiance serveur, idempotence, session, CSRF et concurrence.
        first = post("request_0000000001", prix=1)                               # prix client falsifié ignoré
        assert first.status_code == 200 and first.get_json()["price"] == 80
        duplicate = post("request_0000000001")                                  # double clic/idempotence
        assert duplicate.status_code == 200 and duplicate.get_json() == first.get_json()
        assert client.post("/api/quick-buy/check", json={"offer_key": key, "quick_buy_request_id": "request_0000000002"}).status_code == 403 # CSRF
        assert post("request_0000000003", offer_key="0" * 32).status_code == 404 # annonce supprimée
        other = app_web.app.test_client(); other.get("/")
        with other.session_transaction() as session: other_csrf = session["csrf_token"]
        assert other.post("/api/quick-buy/check", json={"offer_key": key, "quick_buy_request_id": "request_0000000004"}, headers={"X-CSRF-Token": other_csrf}).status_code == 404 # session expirée/autre
        uid = quick_buy.user_id(csrf)
        with app_web._quick_buy_active_lock: app_web._quick_buy_active.add(uid)
        assert post("request_0000000005").status_code == 409                     # vérification concurrente
        with app_web._quick_buy_active_lock: app_web._quick_buy_active.discard(uid)
        unsupported = offer(marketplace="eBay", lien="https://www.ebay.fr/itm/123")
        app_web._cache_results([unsupported], owner=csrf)
        assert client.post("/api/quick-buy/check", json={"offer_key": quick_buy.offer_key(unsupported), "quick_buy_request_id": "request_0000000006"}, headers=headers).status_code == 400
        assert quick_buy.safe_vinted_url("https://vinted.fr.evil.test/items/1") is None
        public_ebay = app_web._public_result(unsupported)
        assert "quick_buy_available" not in public_ebay and "offer_key" not in public_ebay # bouton absent

        # 17–20 : quota, lancement gratuit, Pro, kill switch et panne fournisseur.
        with patch.dict(os.environ, {"QUICK_BUY_LAUNCH_FREE": "false", "QUICK_BUY_FREE_MONTHLY_LIMIT": "0"}):
            assert quick_buy.quota(uid, "free")["allowed"] is False              # quota dépassé
            assert quick_buy.quota(uid, "pro")["allowed"] is True               # Pro illimité
        assert quick_buy.quota(uid, "free")["allowed"] is True                  # lancement gratuit
        with patch.dict(os.environ, {"QUICK_BUY_ENABLED": "false"}):
            assert post("request_0000000007").status_code == 503                 # kill switch
        app_web._cache_results([offer()], owner=csrf)
        with patch.object(quick_buy.PROVIDERS["vinted"], "check_offer", side_effect=TimeoutError("offline")):
            assert post("request_0000000008").status_code == 503                 # réseau/fournisseur indisponible

        with quick_buy._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM quick_buy_events WHERE request_id='request_0000000001'").fetchone()[0]
        assert count == 1
        html = Path("templates/index.html").read_text(encoding="utf-8")
        js = Path("static/app.js").read_text(encoding="utf-8")
        quick_js = Path("static/quick-buy.js").read_text(encoding="utf-8")
        assert "quick_buy_available" in js and "TERMINER SUR VINTED" in quick_js
        assert "Aucun achat ni paiement" in html
    print("[OK] Quick Buy : 20 scénarios critiques + journal idempotent validés")


if __name__ == "__main__":
    main()
