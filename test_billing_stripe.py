"""Test du branchement Stripe Checkout sans réseau réel.

L'appel HTTP vers Stripe est simulé : on vérifie le flux de bout en bout
(création des prix idempotents, session de checkout, réponses d'erreur) et la
désactivation propre tant qu'aucune clé n'est configurée.
"""
import os
import sys

from app_web import app, _billing_ready
import billing_stripe

FAKE_PRICE_ID = "price_test_radar_pro_monthly"
FAKE_SESSION_URL = "https://checkout.stripe.com/c/pay/test-session"
LOOKUP_PRO_MONTHLY = "luxe_radar_v340_pro_monthly"


class FakeStripe:
    def __init__(self):
        self.prices = []
        self.calls = []

    def __call__(self, path, data, method="POST"):
        self.calls.append((path, dict(data)))
        if path == "/prices" and ("lookup_keys[]" in data or "lookup_keys[0]" in data):
            lookup = data.get("lookup_keys[]") or data.get("lookup_keys[0]")
            matches = [p for p in self.prices if p["lookup_key"] == lookup]
            return {"data": matches[:1]}
        if path == "/products":
            return {"id": "prod_test", "name": data["name"]}
        if path == "/prices":
            created = {
                "id": FAKE_PRICE_ID if data.get("lookup_key") == LOOKUP_PRO_MONTHLY else "price_test_other",
                "lookup_key": data.get("lookup_key"),
                "unit_amount": int(data["unit_amount"]),
                "recurring[interval]": data["recurring[interval]"],
            }
            self.prices.append(created)
            return created
        if path == "/checkout/sessions":
            assert data["mode"] == "subscription"
            return {"url": FAKE_SESSION_URL}
        return {"error": {"message": "route inconnue"}}


def test_checkout_flow():
    fake = FakeStripe()
    original_api = billing_stripe._api
    original_cache = dict(billing_stripe._CACHE_PRICES)
    billing_stripe._CACHE_PRICES.clear()
    billing_stripe._api = fake
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_demo"
    os.environ["LUXE_RADAR_BILLING_ENABLED"] = "1"
    client = app.test_client()
    client.get("/")
    with client.session_transaction() as browser_session:
        csrf = browser_session["csrf_token"]
    try:
        assert _billing_ready() is True
        response = client.post(
            "/api/billing/checkout",
            json={"plan": "pro", "cycle": "monthly"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.get_json()["checkout_url"] == FAKE_SESSION_URL
        assert any(path == "/checkout/sessions" for path, _ in fake.calls)
        assert any(path == "/prices" and LOOKUP_PRO_MONTHLY in (data.get("lookup_keys[]") or data.get("lookup_keys[0]") or "") for path, data in fake.calls)
        annual = client.post(
            "/api/billing/checkout",
            json={"plan": "reseller", "cycle": "yearly"},
            headers={"X-CSRF-Token": csrf},
        )
        assert annual.status_code == 200 and annual.get_json()["checkout_url"] == FAKE_SESSION_URL
        bad = client.post(
            "/api/billing/checkout",
            json={"plan": "free", "cycle": "monthly"},
            headers={"X-CSRF-Token": csrf},
        )
        assert bad.status_code == 400
    finally:
        billing_stripe._api = original_api
        billing_stripe._CACHE_PRICES.clear()
        billing_stripe._CACHE_PRICES.update(original_cache)
        os.environ.pop("STRIPE_SECRET_KEY", None)
        os.environ.pop("LUXE_RADAR_BILLING_ENABLED", None)


def test_billing_disabled_without_key():
    client = app.test_client()
    client.get("/")
    os.environ.pop("STRIPE_SECRET_KEY", None)
    os.environ.pop("LUXE_RADAR_BILLING_ENABLED", None)
    billing_stripe._CACHE_PRICES.clear()
    with client.session_transaction() as browser_session:
        csrf = browser_session["csrf_token"]
    assert _billing_ready() is False
    response = client.post(
        "/api/billing/checkout",
        json={"plan": "pro", "cycle": "monthly"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "billing_not_configured"


def test_stripe_error_maps_to_502():
    def failing_api(path, data, method="POST"):
        raise RuntimeError("Stripe 401: invalid API key")

    original_api = billing_stripe._api
    billing_stripe._api = failing_api
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_bad"
    os.environ["LUXE_RADAR_BILLING_ENABLED"] = "1"
    client = app.test_client()
    client.get("/")
    with client.session_transaction() as browser_session:
        csrf = browser_session["csrf_token"]
    try:
        response = client.post(
            "/api/billing/checkout",
            json={"plan": "pro", "cycle": "monthly"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 502
        body = response.get_json()
        assert body["code"] == "stripe_error" and "invalid API key" in body["detail"]
    finally:
        billing_stripe._api = original_api
        os.environ.pop("STRIPE_SECRET_KEY", None)
        os.environ.pop("LUXE_RADAR_BILLING_ENABLED", None)


if __name__ == "__main__":
    test_billing_disabled_without_key()
    test_checkout_flow()
    test_stripe_error_maps_to_502()
    print("OK - Stripe Checkout (API simulée) validé.")
