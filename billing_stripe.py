"""Branchement Stripe Checkout minimal via l'API HTTP directe.

Aucun paquet `stripe` requis : on appelle l'API REST avec `requests`.
Le serveur crée une Session de Checkout Stripe (mode abonnement) et renvoie
l'URL hébergée à ouvrir dans le navigateur.

Les prix (Products/Prices) sont créés automatiquement au premier appel grâce à
un `lookup_key` stable, ce qui rend l'opération idempotente.

Configuration attendue dans `.env` :
    STRIPE_SECRET_KEY=sk_test_...
    LUXE_RADAR_PUBLIC_BASE_URL=http://localhost:5000   (facultatif, défaut localhost)
"""
import os

import requests
from dotenv import find_dotenv, load_dotenv

_API_BASE = os.environ.get("LUXE_RADAR_STRIPE_API_BASE", "https://api.stripe.com/v1")

_dotenv_path = find_dotenv(usecwd=False)
if _dotenv_path:
    load_dotenv(_dotenv_path)
_DEFAULT_BASE_URL = "http://localhost:5000"
_CACHE_PRICES = {}

PLAN_META = {
    "pro": {"name": "LUXE RADAR Premium"},
    "reseller": {"name": "LUXE RADAR Pro"},
}


def secret_key():
    """Clé secrète Stripe, jamais exposée dans l'interface."""
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def billing_ready():
    """Paiement désactivé par défaut en production.

    Une clé Stripe présente ne suffit volontairement pas à activer le checkout :
    l'opérateur doit aussi définir LUXE_RADAR_BILLING_ENABLED=1 après avoir
    branché la confirmation serveur (webhook/provisionnement). Cela évite tout
    débit accidentel pendant une mise en ligne du Radar.
    """
    enabled = (os.environ.get("LUXE_RADAR_BILLING_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    return enabled and bool(secret_key())


def price_euros(plan, cycle):
    """Lit le montant dans SUBSCRIPTION_PLANS (import différé, pas de cycle)."""
    from app_web import SUBSCRIPTION_PLANS
    return float(SUBSCRIPTION_PLANS[plan][cycle])


def _api(path, data, method="POST", timeout=30):
    if method == "GET":
        response = requests.get(
            _API_BASE + path,
            params=data,
            auth=(secret_key(), ""),
            timeout=timeout,
        )
    else:
        response = requests.post(
            _API_BASE + path,
            data=data,
            auth=(secret_key(), ""),
            timeout=timeout,
        )
    if response.status_code >= 400:
        try:
            message = response.json()["error"]["message"]
        except Exception:
            message = response.text[:200]
        raise RuntimeError(f"Stripe {response.status_code}: {message}")
    return response.json()


def _ensure_price(plan, cycle):
    cache_key = f"{plan}_{cycle}"
    cached = _CACHE_PRICES.get(cache_key)
    if cached:
        return cached
    lookup_key = f"luxe_radar_v340_{plan}_{cycle}"
    existing = _api("/prices", {"lookup_keys[]": lookup_key, "limit": "1"}, method="GET")
    prices = existing.get("data") or []
    if prices:
        price_id = prices[0]["id"]
    else:
        amount = int(round(price_euros(plan, cycle) * 100))
        product = _api("/products", {"name": PLAN_META[plan]["name"], "metadata[app]": "luxe_radar"})
        created = _api("/prices", {
            "currency": "eur",
            "unit_amount": str(amount),
            "recurring[interval]": "year" if cycle == "yearly" else "month",
            "product": product["id"],
            "lookup_key": lookup_key,
        })
        price_id = created["id"]
    _CACHE_PRICES[cache_key] = price_id
    return price_id


def create_checkout_session(plan, cycle, owner):
    """Crée une Session de Checkout et renvoie l'URL hébergée."""
    price_id = _ensure_price(plan, cycle)
    base = (os.environ.get("LUXE_RADAR_PUBLIC_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    session_data = _api("/checkout/sessions", {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": base + "/?paiement=succes",
        "cancel_url": base + "/?paiement=echec",
        "client_reference_id": owner or "",
        "allow_promotion_codes": "true",
    })
    return session_data["url"]
