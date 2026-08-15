from collections import OrderedDict
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import logging
import math
import secrets
import unicodedata
from threading import Lock, Semaphore
from time import monotonic, perf_counter
from uuid import uuid4
from urllib.parse import urlparse

import os
import re

from flask import Flask, Response, abort, g, jsonify, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

import billing_stripe

from connector_registry import get_available_connectors
from marketplaces.catalog import get_sites
from radar_engine import rechercher_multi_marketplaces, _cle_unique_multi, _selection_diversifiee, _analyser_resultat_multi
from image_similarity import MAX_IMAGE_BYTES, download_listing_image, image_feature, similarity
from search_understanding import understand_query, suggest_queries, canonicalize_search_query
from marketplaces.connectors.universal import discover_catalog_wave


app = Flask(__name__)
APP_VERSION = "3.0.1"
ASSET_VERSION = "20260815-301"
IS_PRODUCTION = os.environ.get("LUXE_RADAR_ENV", "development").lower() == "production"
IS_RENDER_RUNTIME = bool(
    os.environ.get("RENDER")
    or os.environ.get("RENDER_SERVICE_ID")
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    or IS_PRODUCTION
)
CONFIGURED_SECRET = os.environ.get("LUXE_RADAR_SECRET_KEY", "")
if IS_PRODUCTION and len(CONFIGURED_SECRET) < 32:
    raise RuntimeError("LUXE_RADAR_SECRET_KEY doit contenir au moins 32 caractères en production.")
app.secret_key = CONFIGURED_SECRET or secrets.token_hex(32)
if os.environ.get("LUXE_RADAR_TRUST_PROXY", "").lower() in {"1", "true", "yes"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_IMAGE_BYTES,
    SESSION_COOKIE_NAME="__Host-luxe_radar_session" if IS_PRODUCTION else "luxe_radar_session",
    SESSION_COOKIE_PATH="/",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION or os.environ.get("LUXE_RADAR_HTTPS", "").lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
logging.basicConfig(level=logging.INFO)

ALLOWED_HOSTS = {
    host.strip().lower().rstrip(".")
    for host in os.environ.get("LUXE_RADAR_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
}
if os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip():
    ALLOWED_HOSTS.add(os.environ["RENDER_EXTERNAL_HOSTNAME"].strip().lower().rstrip("."))
SAFE_SORTS = {"relevance", "price_asc", "price_desc", "score", "confidence", "marketplace", "image_similarity"}
PUBLIC_RESULT_FIELDS = {
    "marketplace", "titre", "title", "prix", "devise", "image", "lien", "url",
    "score", "score_confiance", "similarite_image", "categorie", "reference", "taille", "etat",
    "condition", "livraison", "prix_total", "vendeur", "note_vendeur", "source",
    "risque_contrefacon", "alerte_authenticite", "signaux_authenticite",
    "score_identite", "niveau_identite", "correspondance_verifiee",
}
REFERENCE_CATEGORY_ORDER = {"EXCELLENTE AFFAIRE": 0, "BONNE AFFAIRE": 1, "INTERESSANTE": 2, "A VERIFIER": 3, "DOUTEUSE": 4, "A IGNORER": 5}
SERVER_MESSAGES = {
    "missing_fields": {"fr": "Indique une marque et un prix maximum.", "en": "Enter a product and a maximum price."},
    "invalid_price": {"fr": "Prix invalide.", "en": "Invalid price."},
    "price_range": {"fr": "Le prix doit être compris entre 0 et 1 000 000.", "en": "The price must be between 0 and 1,000,000."},
    "invalid_marketplace": {"fr": "Marketplace invalide.", "en": "Invalid marketplace."},
    "invalid_reference": {"fr": "Référence exacte invalide.", "en": "Invalid exact reference."},
    "search_error": {"fr": "La recherche a rencontré un problème temporaire. Réessaie plus tard.", "en": "The search encountered a temporary problem. Please try again later."},
}
_rate_buckets = defaultdict(deque)
_rate_lock = Lock()
MAX_RATE_BUCKETS = 4096


def _is_mobile_request():
    """Détection légère utilisée uniquement pour limiter le premier lot HTML."""
    client_hint = request.headers.get("Sec-CH-UA-Mobile", "").strip()
    if client_hint == "?1":
        return True
    user_agent = (request.headers.get("User-Agent") or "").casefold()
    mobile_tokens = ("iphone", "ipod", "android", "mobile", "windows phone")
    return any(token in user_agent for token in mobile_tokens)


def _is_json_request():
    return request.path.startswith("/api/")


def _request_language():
    requested = request.form.get("language") or request.headers.get("X-Language") or request.accept_languages.best_match(["fr", "en"])
    return requested if requested in {"fr", "en"} else "fr"


def _message(key):
    return SERVER_MESSAGES[key][_request_language()]


def _error(message, status):
    headers = {"Retry-After": "60"} if status == 429 else {}
    if _is_json_request():
        return jsonify({"error": message}), status, headers
    headers["Content-Type"] = "text/plain; charset=utf-8"
    return message, status, headers


def _rate_allowed(key, limit, window=60):
    now = monotonic()
    with _rate_lock:
        if len(_rate_buckets) >= 1024:
            stale = [bucket_key for bucket_key, events in _rate_buckets.items() if not events or now - events[-1] > window]
            for bucket_key in stale:
                _rate_buckets.pop(bucket_key, None)
        while len(_rate_buckets) >= MAX_RATE_BUCKETS:
            _rate_buckets.pop(next(iter(_rate_buckets)))
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


@app.before_request
def security_gate():
    host = (urlparse(f"//{request.host or ''}").hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS:
        abort(400)
    g.csp_nonce = secrets.token_urlsafe(18)
    g.request_id = secrets.token_hex(8)
    g.request_started = perf_counter()
    session.permanent = True
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    client = request.remote_addr or "unknown"
    if not _rate_allowed((client, "global"), 300):
        return _error("Trop de requêtes. Réessaie dans une minute.", 429)
    if request.method == "POST":
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(str(supplied), str(session.get("csrf_token", ""))):
            return _error("Requête de sécurité invalide. Recharge la page.", 403)
        route_limit = 12 if request.endpoint == "accueil" else (90 if request.endpoint == "expand_results" else 30)
        if not _rate_allowed((client, request.endpoint, "post"), route_limit):
            return _error("Trop de tentatives. Patiente une minute.", 429)


@app.after_request
def security_headers(response):
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self'; img-src 'self' https: http: data:; "
        "connect-src 'self'; font-src 'self'; media-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-Download-Options"] = "noopen"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Origin-Agent-Cluster"] = "?1"
    response.headers["X-DNS-Prefetch-Control"] = "off"
    response.headers["X-Request-ID"] = getattr(g, "request_id", "")
    elapsed_ms = max(0, (perf_counter() - getattr(g, "request_started", perf_counter())) * 1000)
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path == "/" or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.path.startswith("/static/campaign/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    elif request.path.startswith("/static/") and request.args.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.errorhandler(413)
def request_too_large(_error_value):
    return _error("Requête trop volumineuse.", 413)


@app.errorhandler(400)
def bad_request(_error_value):
    return _error("Requête invalide.", 400)


@app.errorhandler(404)
def not_found(_error_value):
    return _error("Ressource introuvable.", 404)


@app.errorhandler(405)
def method_not_allowed(_error_value):
    return _error("Méthode non autorisée.", 405)


@app.errorhandler(500)
def internal_error(_error_value):
    return _error("Une erreur interne est survenue.", 500)


@app.get("/api/health")
def health():
    sites, marketplaces = _app_metadata()
    return jsonify({
        "status": "ok",
        "version": APP_VERSION,
        "progressive": True,
        "progressive_workers": _PROGRESSIVE_WORKERS,
        "coverage_mode": "max",
        "connectors": len(marketplaces),
        "catalog_sites": len(sites),
        "billing_ready": _billing_ready(),
    })


@app.get("/api/version")
def app_version():
    return jsonify({
        "version": APP_VERSION,
        "asset_version": ASSET_VERSION,
        "render": IS_RENDER_RUNTIME,
        "progressive": True,
        "progressive_workers": _PROGRESSIVE_WORKERS,
        "coverage_mode": "max",
    })


@app.get("/robots.txt")
def robots_txt():
    body = "\n".join((
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        f"Sitemap: {url_for('sitemap_xml', _external=True)}",
        "",
    ))
    return Response(body, content_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml")
def sitemap_xml():
    locations = (url_for("accueil", _external=True), url_for("trust_center", _external=True))
    escaped = [location.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;") for location in locations]
    entries = "".join(f"<url><loc>{location}</loc></url>" for location in escaped)
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n'
    return Response(body, content_type="application/xml; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/confiance")
def trust_center():
    sites, marketplaces = _app_metadata()
    language = "en" if request.args.get("lang") == "en" else "fr"
    copy = {
        "fr": {
            "title": "Confiance & confidentialité", "back": "← Retour à l’application", "kicker": "CENTRE DE CONFIANCE",
            "headline": "Clair sur ce qui fonctionne. Clair sur les limites.",
            "intro": "LUXE RADAR aide à chercher et comparer. Il ne vend pas les articles, n’authentifie pas les pièces et n’active jamais une source simplement parce que son site existe.",
            "tested": "connecteurs testés", "listed": "sites référencés", "fake": "0 faux résultat",
            "cards": (
                ("Sources actives", "Seuls {markets} sont présentés comme actifs. Les sites « à tester », bloqués, OFF ou non implémentés restent désactivés."),
                ("Données locales", "Favoris, historique, alertes, collections, suivi de prix et inventaire restent dans ce navigateur. Ils peuvent être exportés ou effacés depuis la section Données."),
                ("Recherche temporaire", "Les résultats paginés sont gardés brièvement en mémoire côté serveur, liés à la session puis supprimés automatiquement. Aucun compte cloud n’est actuellement créé."),
                ("Achat responsable", "Scores et confiance servent à classer, jamais à garantir l’authenticité, le vendeur, l’état, le bénéfice ou la disponibilité. Vérifie toujours l’annonce sur la marketplace."),
                ("Accès respectueux", "Aucun CAPTCHA, 403, mur de connexion ou contrôle anti-bot ne doit être contourné. Une source qui ne peut pas être intégrée proprement reste OFF."),
                ("Mise en production", "La configuration prévoit HTTPS, cookies sécurisés, CSP, CSRF, limitation de débit et secrets externes. Le checkout reste désactivé par défaut, même si une clé Stripe existe. Il ne doit être activé qu’après branchement de la confirmation serveur et du provisionnement d’abonnement."),
            ),
            "before": "Avant une commercialisation", "legal": "Cette page décrit le comportement technique actuel. Elle ne remplace pas des mentions légales, CGU, CGV ou une politique de confidentialité relues pour le pays de publication.",
            "footer": "Service indépendant, non affilié aux marketplaces. Les achats sont conclus sur leurs sites.", "switch": "English",
        },
        "en": {
            "title": "Trust & privacy", "back": "← Back to the app", "kicker": "TRUST CENTRE",
            "headline": "Clear about what works. Clear about the limits.",
            "intro": "LUXE RADAR helps people search and compare. It does not sell items, authenticate products or activate a source merely because its website exists.",
            "tested": "tested connectors", "listed": "catalogued sites", "fake": "0 fake results",
            "cards": (
                ("Active sources", "Only {markets} are presented as active. To-test, blocked, OFF and unimplemented sites remain disabled."),
                ("Local data", "Favorites, history, alerts, collections, price tracking and inventory stay in this browser. They can be exported or erased from the Data section."),
                ("Temporary searches", "Paginated results are held briefly in server memory, bound to the session and then removed automatically. No cloud account is currently created."),
                ("Responsible buying", "Scores and confidence help rank listings; they never guarantee authenticity, the seller, condition, profit or availability. Always verify the listing on its marketplace."),
                ("Respectful access", "CAPTCHAs, 403 blocks, login walls and anti-bot controls must not be bypassed. A source that cannot be integrated cleanly remains OFF."),
                ("Production readiness", "The configuration provides for HTTPS, secure cookies, CSP, CSRF, rate limits and external secrets. Checkout stays disabled by default even when a Stripe key exists. It should only be enabled after server-side confirmation and subscription provisioning are connected."),
            ),
            "before": "Before commercial launch", "legal": "This page describes current technical behaviour. It is not a substitute for legal notices, terms or a privacy policy reviewed for the publication country.",
            "footer": "Independent service, not affiliated with marketplaces. Purchases are completed on their websites.", "switch": "Français",
        },
    }[language]
    return render_template(
        "trust.html",
        catalog_site_count=len(sites),
        marketplaces=list(marketplaces),
        language=language,
        copy=copy,
        csp_nonce=g.csp_nonce,
    )


CATALOG_BATCH_SIZE = 50
CATALOG_STATUSES = {"active", "to_test", "blocked", "non_implemented", "off"}
CATALOG_STATUS_ORDER = {"active": 0, "to_test": 1, "off": 2, "non_implemented": 3, "blocked": 4}


def _public_catalog_site(site):
    url = str(site.get("url") or site.get("base_url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        url = ""
    return {
        "name": str(site.get("display_name") or site.get("name") or site.get("domain") or "")[:120],
        "url": url,
        "domain": str(site.get("domain") or parsed.netloc or "")[:180],
        "category": str(site.get("category") or "Non classé")[:120],
        "country": str(site.get("country") or "")[:12],
        "currency": str(site.get("currency") or "")[:12],
        "status": str(site.get("status") or "off"),
        "enabled": bool(site.get("enabled")),
        "connector_type": str(site.get("connector_type") or "none")[:40],
        "supports_search": bool(site.get("supports_search")),
        "supports_price": bool(site.get("supports_price")),
        "supports_image": bool(site.get("supports_image")),
        "supports_reference": bool(site.get("supports_reference")),
    }


def _catalog_search_key(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


@app.get("/api/search-suggestions")
def search_suggestions():
    query = request.args.get("q", "").strip()[:120]
    if len(query) < 2:
        return jsonify({"understanding": None, "suggestions": []})
    info = understand_query(query)
    return jsonify({
        "understanding": info.to_dict(),
        "suggestions": suggest_queries(query, limit=8),
    })


@app.get("/api/catalog")
def catalog_page():
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Offset invalide."}), 400
    if offset < 0 or offset > 100_000:
        return jsonify({"error": "Offset hors limites."}), 400
    query = _catalog_search_key(str(request.args.get("q") or "").strip()[:80])
    status = str(request.args.get("status") or "all").strip()
    category = str(request.args.get("category") or "all").strip()[:120]
    if status != "all" and status not in CATALOG_STATUSES:
        return jsonify({"error": "Statut invalide."}), 400

    sites, _marketplaces = _app_metadata()
    public_sites = [_public_catalog_site(site) for site in sites]
    status_counts = {key: 0 for key in CATALOG_STATUSES}
    for site in public_sites:
        if site["status"] in status_counts:
            status_counts[site["status"]] += 1
    categories = sorted({site["category"] for site in public_sites}, key=str.casefold)
    if query:
        public_sites = [site for site in public_sites if query in _catalog_search_key(" ".join((site["name"], site["domain"], site["category"], site["country"])))]
    if status != "all":
        public_sites = [site for site in public_sites if site["status"] == status]
    if category != "all":
        public_sites = [site for site in public_sites if site["category"] == category]
    public_sites.sort(key=lambda site: (CATALOG_STATUS_ORDER.get(site["status"], 9), site["name"].casefold(), site["domain"].casefold()))
    total = len(public_sites)
    batch = public_sites[offset:offset + CATALOG_BATCH_SIZE]
    next_offset = offset + len(batch)
    return jsonify({
        "sites": batch,
        "next_offset": next_offset,
        "has_more": next_offset < total,
        "total": total,
        "total_catalog": len(sites),
        "status_counts": status_counts,
        "categories": categories,
    })

def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


INITIAL_RESULTS = 50
MOBILE_INITIAL_RESULTS = 25
RESULT_BATCH_SIZE = 50
SEARCH_RESULT_LIMIT = _bounded_env_int(
    "LUXE_RADAR_SEARCH_RESULT_LIMIT",
    500 if IS_RENDER_RUNTIME else 1000,
    100,
    2000,
)
MAX_BATCH_SIZE = 200
IMAGE_COMPARE_LIMIT = _bounded_env_int("LUXE_RADAR_IMAGE_COMPARE_LIMIT", 64, 16, 120)
CACHE_TTL_SECONDS = (20 if IS_RENDER_RUNTIME else 30) * 60
MAX_CACHED_SEARCHES = _bounded_env_int(
    "LUXE_RADAR_MAX_CACHED_SEARCHES",
    8 if IS_RENDER_RUNTIME else 20,
    4,
    50,
)

# Pipeline progressif V2.8.6. Seules deux sources HTTP éprouvées construisent
# le premier rendu ; toutes les autres enrichissent ensuite le même catalogue.
# L'ordre est volontaire : sources HTTP utiles d'abord, navigateurs/marketplaces
# bloquées ensuite. Une recherche ciblée sur UNE marketplace reste synchrone.
PROGRESSIVE_FAST_SOURCES = ("eBay",)
PROGRESSIVE_BACKGROUND_SOURCES = (
    # V2.9.2 : les sources HTTP qui répondent réellement passent d'abord.
    # Grailed/Vinted utilisent Chromium ou sont plus variables : ils restent
    # volontairement à la fin pour ne plus monopoliser un worker pendant que
    # l'utilisateur attend les premières vagues utiles.
    "Zalando", "SSENSE", "ASOS", "AliExpress", "DHgate",
    "Spartoo", "Footshop", "JD Sports", "Cdiscount", "67behaviour",
    "1688", "Grailed", "Vinted",
)

SUBSCRIPTION_PLANS = {
    "free": {
        "name": "Gratuit", "monthly": 0, "yearly": 0,
        "tagline": "Pour découvrir les bonnes affaires.",
    },
    "pro": {
        "name": "Pro", "monthly": 3.99, "yearly": 39.90,
        "tagline": "Pour chercher plus vite et décider mieux.",
    },
    "reseller": {
        "name": "Reseller", "monthly": 8.99, "yearly": 89.90,
        "tagline": "Pour piloter une activité de revente.",
    },
}


def _billing_ready():
    """OFF tant qu'aucune clé Stripe n'est configurée dans .env."""
    return billing_stripe.billing_ready()


@app.get("/api/billing/plans")
def billing_plans():
    return jsonify({"plans": SUBSCRIPTION_PLANS, "billing_ready": _billing_ready()})


@app.post("/api/billing/checkout")
def billing_checkout():
    payload = request.get_json(silent=True) or {}
    plan = payload.get("plan")
    cycle = payload.get("cycle", "monthly")
    if plan not in {"pro", "reseller"} or cycle not in {"monthly", "yearly"}:
        return jsonify({"error": "Offre ou période invalide."}), 400
    if not _billing_ready():
        return jsonify({
            "error": "Paiement pas encore configuré.",
            "code": "billing_not_configured",
        }), 503
    try:
        checkout_url = billing_stripe.create_checkout_session(
            plan, cycle, owner=str(session.get("csrf_token") or "")
        )
    except RuntimeError as exc:
        return jsonify({
            "error": "Le fournisseur de paiement a refusé la demande.",
            "code": "stripe_error",
            "detail": str(exc)[:300],
        }), 502
    return jsonify({"checkout_url": checkout_url})


@app.get("/api/account/capabilities")
def account_capabilities():
    return jsonify({
        "accounts_ready": False,
        "sync_ready": False,
        "billing_ready": _billing_ready(),
        "storage": "local_browser",
        "next_requirements": ["database", "secure_session_secret", "email_provider", "stripe_webhooks"],
    })


@app.get("/sw.js")
def service_worker():
    response = app.send_static_file("sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response

_search_cache = OrderedDict()
_cache_lock = Lock()
# Dernière recherche progressive par session. Les tâches d'une ancienne
# recherche deviennent obsolètes et sortent dès qu'un worker les prend.
_progressive_owner_tokens = {}
_metadata_cache = {"sites": None, "marketplaces": None}
_metadata_lock = Lock()
_image_feature_cache = OrderedDict()
_IMAGE_FEATURE_CACHE_MAX = 200
_image_feature_lock = Lock()

# Recherche progressive : concurrence faible sur Render pour ne pas lancer
# plusieurs Chromium simultanément sur le plan 512 Mo. En local on garde plus
# de parallélisme. Les tâches sont bornées par les timeouts des connecteurs.
_PROGRESSIVE_WORKERS = _bounded_env_int(
    "LUXE_RADAR_PROGRESSIVE_WORKERS",
    2 if IS_RENDER_RUNTIME else 5,
    1,
    5,
)
_progressive_executor = ThreadPoolExecutor(
    max_workers=_PROGRESSIVE_WORKERS,
    thread_name_prefix="luxe-progressive",
)
# Un seul navigateur Playwright lourd à la fois sur le même process. Les deux
# workers progressifs peuvent continuer à faire du HTTP en parallèle.
_browser_progressive_semaphore = Semaphore(1)

print(
    f"[LUXE RADAR] V{APP_VERSION} | progressive=eBay-first | "
    f"workers={_PROGRESSIVE_WORKERS} | render={IS_RENDER_RUNTIME}"
)


MARQUES = [
    ("Nike", 20), ("Adidas", 20), ("Jordan", 30), ("New Balance", 25),
    ("Asics", 25), ("Salomon", 30), ("On", 30), ("Hoka", 30),
    ("Saucony", 25), ("Puma", 20), ("Reebok", 20), ("Mizuno", 25),
    ("Brooks", 25), ("Merrell", 25), ("Converse", 20), ("Vans", 20),
    ("Timberland", 30), ("Dr. Martens", 30), ("Under Armour", 15),
    ("Columbia", 20), ("The North Face", 25), ("Patagonia", 25),
    ("Arc'teryx", 35), ("Helly Hansen", 25), ("Barbour", 30),
    ("Napapijri", 25), ("Rab", 30), ("Mammut", 30), ("Fjallraven", 30),
    ("Jack Wolfskin", 25), ("Peak Performance", 30), ("Gore Wear", 25),
    ("Oakley", 25), ("Kappa", 15), ("Umbro", 15),
    ("Essentials", 50), ("Ralph Lauren", 20), ("Lacoste", 20),
    ("Fred Perry", 20), ("Tommy Hilfiger", 20), ("Carhartt WIP", 20),
    ("Stone Island", 30), ("C.P. Company", 30), ("Diesel", 25),
    ("Moncler", 40), ("Canada Goose", 40), ("Burberry", 40),
    ("Palm Angels", 40), ("Ami Paris", 40), ("Jacquemus", 40),
    ("Acne Studios", 40), ("Off-White", 40), ("Gucci", 50),
    ("Prada", 50), ("Balenciaga", 50), ("Saint Laurent", 50),
    ("Dior", 50), ("Givenchy", 50), ("Fendi", 50), ("Valentino", 50),
    ("Maison Margiela", 50), ("Amiri", 50),
]


def _clean_cache(now=None):
    now = monotonic() if now is None else now
    expired = [
        token for token, entry in _search_cache.items()
        if now - entry["created_at"] > CACHE_TTL_SECONDS
    ]
    for token in expired:
        entry = _search_cache.pop(token, None) or {}
        owner = str(entry.get("owner") or "")
        if owner and _progressive_owner_tokens.get(owner) == token:
            _progressive_owner_tokens.pop(owner, None)
    while len(_search_cache) > MAX_CACHED_SEARCHES:
        token, entry = _search_cache.popitem(last=False)
        owner = str(entry.get("owner") or "")
        if owner and _progressive_owner_tokens.get(owner) == token:
            _progressive_owner_tokens.pop(owner, None)


def _app_metadata():
    with _metadata_lock:
        if _metadata_cache["sites"] is None:
            _metadata_cache["sites"] = tuple(get_sites())
        if _metadata_cache["marketplaces"] is None:
            _metadata_cache["marketplaces"] = tuple(get_available_connectors().keys())
        return _metadata_cache["sites"], _metadata_cache["marketplaces"]


def invalidate_app_metadata():
    with _metadata_lock:
        _metadata_cache.update(sites=None, marketplaces=None)


def _marketplace_counts(results):
    counts = {}
    for item in results or []:
        marketplace = str(item.get("marketplace") or "Inconnu")
        counts[marketplace] = counts.get(marketplace, 0) + 1
    return counts


def _cache_results(
    results, owner=None, pending_sources=None, completed_sources=None,
    search_query=None, search_price=None,
):
    token = uuid4().hex
    pending_sources = list(dict.fromkeys(str(source) for source in (pending_sources or []) if str(source)))
    completed_sources = list(dict.fromkeys(str(source) for source in (completed_sources or []) if str(source)))
    owner_key = str(owner or "")
    with _cache_lock:
        _clean_cache()

        # Une nouvelle recherche de la même session rend les anciennes tâches
        # progressives obsolètes. On ne supprime pas les résultats précédents
        # (les liens déjà ouverts restent lisibles), mais on empêche les workers
        # en file d'aller lancer de nouveaux connecteurs pour rien.
        if owner_key:
            previous_token = _progressive_owner_tokens.get(owner_key)
            previous = _search_cache.get(previous_token) if previous_token else None
            if previous is not None:
                previous["cancelled"] = True
                previous["pending"] = False
                previous["pending_sources"] = []
            if pending_sources:
                _progressive_owner_tokens[owner_key] = token
            else:
                _progressive_owner_tokens.pop(owner_key, None)

        _search_cache[token] = {
            "created_at": monotonic(),
            "owner": owner_key,
            "results": [dict(item, _rank_index=index) for index, item in enumerate(results or [])],
            "pending": bool(pending_sources),
            "pending_sources": pending_sources,
            "completed_sources": completed_sources,
            "failed_sources": [],
            "source_counts": _marketplace_counts(results),
            "generation": 0,
            "cancelled": False,
            "search_query": str(search_query or "").strip(),
            "search_price": _safe_number(search_price, None),
            "page_state": {"eBay": 1, "Zalando": 1, "Vinted": 1},
            "page_empty": {"eBay": 0, "Zalando": 0, "Vinted": 0},
            "page_exhausted": [],
            "discovery_cursor": 0,
            "discovery_has_more": True,
            "expansion_round": 0,
            "expansion_inflight": False,
            "expansion_exhausted": False,
            "catalog_scanned": 0,
        }
        _clean_cache()
    return token


def _cached_results_snapshot(token, owner=None):
    """Retourne une copie du catalogue courant sans exposer l'état partagé."""
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return None
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return None
        return [dict(item) for item in entry.get("results") or []]


def _merge_progressive_results(existing, additions):
    """Fusionne des résultats déjà analysés en conservant le classement pur."""
    uniques = []
    seen = set()
    for item in [*(existing or []), *(additions or [])]:
        key = _cle_unique_multi(item)
        if key in seen:
            continue
        seen.add(key)
        cleaned = dict(item)
        cleaned.pop("_rank_index", None)
        uniques.append(cleaned)

    ranked, _counts = _selection_diversifiee(
        uniques,
        min(len(uniques), SEARCH_RESULT_LIMIT),
        diversifie=False,
    )
    return ranked


def _append_expansion_results(existing, additions):
    """Ajoute une vague d'infinite-scroll sans déplacer les cartes déjà vues.

    Les résultats initiaux peuvent encore être reclassés pendant le pipeline
    progressif. Une fois l'utilisateur arrivé au bas de la liste, les vagues
    suivantes sont append-only : l'offset reste stable et le scroll ne saute
    plus en arrière après chaque extension.
    """
    existing_clean = []
    seen = set()
    for item in existing or []:
        key = _cle_unique_multi(item)
        if key in seen:
            continue
        seen.add(key)
        cleaned = dict(item)
        cleaned.pop("_rank_index", None)
        existing_clean.append(cleaned)

    fresh = []
    for item in additions or []:
        key = _cle_unique_multi(item)
        if key in seen:
            continue
        seen.add(key)
        cleaned = dict(item)
        cleaned.pop("_rank_index", None)
        fresh.append(cleaned)

    if fresh:
        fresh, _counts = _selection_diversifiee(
            fresh, min(len(fresh), SEARCH_RESULT_LIMIT), diversifie=False
        )
    room = max(0, SEARCH_RESULT_LIMIT - len(existing_clean))
    return existing_clean + fresh[:room]


def _progressive_task_allowed(token, source, owner):
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None or entry.get("cancelled"):
            return False
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return False
        return source in (entry.get("pending_sources") or [])


def _complete_progressive_source(token, source, additions, reference, strict, owner):
    """Fusion atomique d'une source progressive terminée."""
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None or entry.get("cancelled"):
            return None
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return None

        existing = [dict(item) for item in entry.get("results") or []]
        results = _merge_progressive_results(existing, additions)
        results = _rank_by_reference(results, reference, strict)
        entry["results"] = [dict(item, _rank_index=index) for index, item in enumerate(results)]

        pending = [name for name in entry.get("pending_sources") or [] if name != source]
        completed = list(entry.get("completed_sources") or [])
        if source not in completed:
            completed.append(source)
        entry["pending_sources"] = pending
        entry["completed_sources"] = completed
        entry["source_counts"] = _marketplace_counts(results)
        entry["pending"] = bool(pending)
        # Ne force pas le navigateur à recharger les 50 premières cartes
        # lorsqu'une source termine avec zéro nouveauté.
        if len(results) != len(existing):
            entry["generation"] = int(entry.get("generation", 0)) + 1
        return len(existing), len(results), bool(pending)


def _fail_progressive_source(token, source, owner):
    with _cache_lock:
        entry = _search_cache.get(token)
        if entry is None or entry.get("cancelled"):
            return
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return
        entry["pending_sources"] = [name for name in entry.get("pending_sources") or [] if name != source]
        failed = list(entry.get("failed_sources") or [])
        if source not in failed:
            failed.append(source)
        entry["failed_sources"] = failed
        entry["pending"] = bool(entry["pending_sources"])


def _finish_progressive_source(token, query, price, source, reference, strict, owner):
    """Termine UNE source en arrière-plan puis fusionne sans relancer les autres."""
    if not _progressive_task_allowed(token, source, owner):
        return

    started = perf_counter()
    try:
        # Mono-source : radar_engine exécute désormais directement le connecteur,
        # sans sous-executor impossible à annuler. Les deux connecteurs
        # Playwright partagent en plus un sémaphore afin de ne jamais lancer
        # deux Chromium simultanément sur le petit service Render.
        def _search_source():
            # V2.9.2 : ne pas analyser 100+ cartes par source au premier passage.
            # Le scroll infini demandera les pages suivantes au besoin.
            source_caps = {
                "Zalando": 50,
                "SSENSE": 50,
                "ASOS": 60,
                "AliExpress": 60,
                "DHgate": 50,
                "Cdiscount": 40,
                "67behaviour": 30,
                "1688": 30,
                "Grailed": 36,
                "Vinted": 30,
            }
            source_limit = min(source_caps.get(source, 50), SEARCH_RESULT_LIMIT)
            return rechercher_multi_marketplaces(
                marque=query,
                prix_max=price,
                plateformes=[source],
                limite=source_limit,
            )

        if source in {"Vinted", "Grailed"}:
            with _browser_progressive_semaphore:
                # La tâche a pu devenir obsolète pendant l'attente du navigateur.
                # Revalider juste avant de lancer Chromium évite qu'une ancienne
                # recherche démarre Vinted/Grailed après une nouvelle requête.
                if not _progressive_task_allowed(token, source, owner):
                    return
                additions = _search_source()
        else:
            additions = _search_source()
        if not _progressive_task_allowed(token, source, owner):
            return
        state = _complete_progressive_source(
            token, source, additions, reference, strict, owner
        )
        if state is None:
            return
        before, total, still_pending = state
        print(
            f"[PROGRESSIF] {source} terminé: +{max(0, total-before)} -> "
            f"{total} résultats en {perf_counter()-started:.2f}s"
            + (" | autres sources en cours" if still_pending else " | catalogue final")
        )
    except Exception:
        app.logger.exception("Échec de la source progressive %s", source)
        _fail_progressive_source(token, source, owner)


def _safe_number(value, default=0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _canonicalize_search_query(value):
    """Compréhension centrale : fautes sûres + marque/modèle/type contextualisés."""
    return canonicalize_search_query(value)


def _normalized_reference(value):
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())[:80]


def _rank_by_reference(results, reference, strict=False):
    needle = _normalized_reference(reference)
    if not needle:
        return list(results)
    matching, decorated = [], []
    for index, item in enumerate(results):
        haystack = _normalized_reference(f"{item.get('reference') or ''} {item.get('titre') or item.get('title') or ''}")
        is_match = needle in haystack
        if is_match:
            matching.append(item)
        decorated.append((REFERENCE_CATEGORY_ORDER.get(item.get("categorie"), 3), not is_match, index, item))
    return matching if strict else [item for _category, _not_match, _index, item in sorted(decorated, key=lambda entry: entry[:3])]


def _sorted_results(results, sort="relevance", marketplace="Toutes", price_min=None, price_max=None, price_exact=None, price_tolerance=None, exclude="", risk="all", identity="confirmed"):
    filtered = list(results)
    if marketplace and marketplace != "Toutes":
        filtered = [item for item in filtered if item.get("marketplace") == marketplace]
    if price_min not in (None, ""):
        minimum = max(0, _safe_number(price_min))
        filtered = [item for item in filtered if _safe_number(item.get("prix"), -1) >= minimum]
    if price_max not in (None, ""):
        maximum = max(0, _safe_number(price_max))
        filtered = [item for item in filtered if _safe_number(item.get("prix"), float("inf")) <= maximum]
    if price_exact not in (None, ""):
        cible = max(0, _safe_number(price_exact))
        tolerance = max(0, _safe_number(price_tolerance)) if price_tolerance not in (None, "") else 0
        filtered = [
            item for item in filtered
            if abs(_safe_number(item.get("prix"), float("inf")) - cible) <= tolerance
        ]
    excluded = [word.casefold().strip() for word in str(exclude or "").split(",") if word.strip()]
    if excluded:
        filtered = [
            item for item in filtered
            if not any(word in str(item.get("titre") or item.get("title") or "").casefold() for word in excluded)
        ]
    if risk == "hide_high":
        filtered = [item for item in filtered if item.get("risque_contrefacon") != "eleve"]
    elif risk == "low_only":
        filtered = [item for item in filtered if item.get("risque_contrefacon") == "faible"]
    if identity == "confirmed":
        filtered = [item for item in filtered if str(item.get("niveau_identite") or "") in {"fort", "possible"}]
    elif identity == "strong":
        filtered = [item for item in filtered if str(item.get("niveau_identite") or "") == "fort"]
    elif identity == "unverified":
        filtered = [item for item in filtered if str(item.get("niveau_identite") or "") == "rejet"]
    keys = {
        "relevance": lambda item: item.get("_rank_index", 0),
        "price_asc": lambda item: (_safe_number(item.get("prix"), float("inf")), item.get("_rank_index", 0)),
        "price_desc": lambda item: (-_safe_number(item.get("prix"), -1), item.get("_rank_index", 0)),
        "score": lambda item: (-_safe_number(item.get("score")), item.get("_rank_index", 0)),
        "confidence": lambda item: (-_safe_number(item.get("score_confiance")), item.get("_rank_index", 0)),
        "image_similarity": lambda item: (-_safe_number(item.get("similarite_image"), -1), item.get("_rank_index", 0)),
        "marketplace": lambda item: (str(item.get("marketplace") or "").casefold(), item.get("_rank_index", 0)),
    }
    return sorted(filtered, key=keys.get(sort, keys["relevance"]))


def _cached_image_feature(url):
    """Download once per URL per session; embedded features are bounded in memory."""
    with _image_feature_lock:
        if url in _image_feature_cache:
            _image_feature_cache.move_to_end(url)
            return _image_feature_cache[url]
    data = download_listing_image(url)
    feature = None
    if data:
        try:
            feature = image_feature(data)
        except ValueError:
            feature = None
    with _image_feature_lock:
        _image_feature_cache[url] = feature
        while len(_image_feature_cache) > _IMAGE_FEATURE_CACHE_MAX:
            _image_feature_cache.popitem(last=False)
    return feature


def _rank_by_image(results, upload_feature):
    """Compare at most 32 public listing thumbnails with bounded concurrent downloads."""
    candidates = [(index, item.get("image")) for index, item in enumerate(results) if item.get("image")][:IMAGE_COMPARE_LIMIT]
    compared = 0

    def compare(candidate):
        index, url = candidate
        feature = _cached_image_feature(url)
        if feature is None:
            return index, None
        return index, round(similarity(upload_feature, feature), 1)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(candidates)))) as executor:
        futures = [executor.submit(compare, candidate) for candidate in candidates]
        for future in as_completed(futures):
            index, score = future.result()
            if score is not None:
                results[index]["similarite_image"] = score
                compared += 1
    return compared


def _public_result(item):
    public = {}
    for key in PUBLIC_RESULT_FIELDS:
        value = item.get(key)
        if value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, str):
            value = value[:2048] if key in {"lien", "url", "image"} else value[:500] if key in {"titre", "title"} else value[:200]
        public[key] = value
    for key in ("lien", "url", "image"):
        if key not in public:
            continue
        value = str(public.get(key) or "")
        parsed = urlparse(value)
        public[key] = value if parsed.scheme in {"http", "https"} and parsed.netloc else ""
    return public


def _result_page(token, offset, limit=RESULT_BATCH_SIZE, sort="relevance", marketplace="Toutes", price_min=None, price_max=None, price_exact=None, price_tolerance=None, exclude="", risk="all", identity="confirmed", owner=None):
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), MAX_BATCH_SIZE))
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return None
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return None
        results = _sorted_results(entry["results"], sort=sort, marketplace=marketplace, price_min=price_min, price_max=price_max, price_exact=price_exact, price_tolerance=price_tolerance, exclude=exclude, risk=risk, identity=identity)
        page = [_public_result(item) for item in results[offset:offset + limit]]
        next_offset = offset + len(page)
        return {
            "results": page,
            "next_offset": next_offset,
            "has_more": next_offset < len(results),
            "total": len(results),
        }


@app.get("/api/results/<token>/status")
def result_status(token):
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        return jsonify({"error": "Identifiant de recherche invalide."}), 400
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return jsonify({"error": "Recherche expirée."}), 404
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(session.get("csrf_token") or "")):
            return jsonify({"error": "Recherche expirée."}), 404
        pending_sources = list(entry.get("pending_sources") or [])
        completed_sources = list(entry.get("completed_sources") or [])
        failed_sources = list(entry.get("failed_sources") or [])
        source_counts = dict(entry.get("source_counts") or {})
        for source in completed_sources:
            source_counts.setdefault(source, 0)
        for source in failed_sources:
            source_counts.setdefault(source, 0)
        identity_counts = {"fort": 0, "possible": 0, "rejet": 0}
        for item in entry.get("results") or []:
            level = str(item.get("niveau_identite") or "possible")
            if level in identity_counts:
                identity_counts[level] += 1
        return jsonify({
            "pending": bool(entry.get("pending")),
            "generation": int(entry.get("generation", 0)),
            "total": len(entry.get("results") or []),
            "pending_sources": pending_sources,
            "completed_sources": completed_sources,
            "failed_sources": failed_sources,
            "source_counts": source_counts,
            "identity_counts": identity_counts,
            "expansion_inflight": bool(entry.get("expansion_inflight")),
            "expansion_exhausted": bool(entry.get("expansion_exhausted")),
            "catalog_scanned": int(entry.get("catalog_scanned", 0)),
        })


EXPAND_PAGE_SOURCES = ("eBay", "Zalando", "Vinted")
EXPAND_WAVE_ORDER = ("eBay", "Zalando", "Vinted", "__catalog__")


def _analyse_discovery_items(items, query, price):
    analysed = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        try:
            item = _analyser_resultat_multi(raw, query=query, prix_max=price)
        except Exception:
            item = None
        if item is not None:
            analysed.append(item)
    return analysed


def _expand_search_once(token, owner, marketplace="Toutes"):
    """Ajoute une nouvelle vague réelle au catalogue d'une recherche.

    - eBay / Zalando / Vinted : page suivante de la source ;
    - catalogue massif : quelques sites publics non bloqués sont sondés en
      parallèle. Les 1000+ domaines ne sont jamais frappés simultanément.
    """
    owner = str(owner or "")
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return None, 404
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, owner):
            return None, 404
        if entry.get("expansion_inflight"):
            return {"accepted": True, "busy": True, "added": 0, "exhausted": bool(entry.get("expansion_exhausted")), "retry_after_ms": 1800}, 202
        query = str(entry.get("search_query") or "").strip()
        price = entry.get("search_price")
        if not query or price in (None, ""):
            entry["expansion_exhausted"] = True
            return {"accepted": False, "busy": False, "added": 0, "exhausted": True}, 200

        exhausted = set(entry.get("page_exhausted") or [])
        round_index = int(entry.get("expansion_round", 0))
        discovery_has_more = bool(entry.get("discovery_has_more", True))
        initial_pipeline_pending = bool(entry.get("pending_sources") or [])

        requested = str(marketplace or "Toutes")
        target = None
        if requested != "Toutes":
            if requested in EXPAND_PAGE_SOURCES and requested not in exhausted:
                target = requested
            else:
                return {"accepted": False, "busy": False, "added": 0, "exhausted": True, "source": requested}, 200
        else:
            # Pendant que Grailed/Vinted/les sources lentes finissent leur premier
            # passage, le scroll peut déjà avancer sur les deux pages HTTP rapides.
            # On évite ainsi le "mur" de 20-30 s en bas de liste.
            wave_order = ("eBay", "Zalando") if initial_pipeline_pending else EXPAND_WAVE_ORDER
            for step in range(len(wave_order)):
                candidate = wave_order[(round_index + step) % len(wave_order)]
                if candidate == "__catalog__":
                    if discovery_has_more:
                        target = candidate
                        round_index += step + 1
                        break
                elif candidate not in exhausted:
                    target = candidate
                    round_index += step + 1
                    break
            if target is None:
                # Les vagues rapides sont peut-être épuisées, mais le premier
                # pipeline peut encore apporter de nouvelles annonces.
                if initial_pipeline_pending:
                    return {
                        "accepted": True, "busy": True, "added": 0,
                        "exhausted": False, "source": "sources en cours",
                        "retry_after_ms": 1800,
                    }, 202
                entry["expansion_exhausted"] = True
                return {"accepted": False, "busy": False, "added": 0, "exhausted": True}, 200

        entry["expansion_inflight"] = True
        entry["expansion_round"] = round_index
        existing_count = len(entry.get("results") or [])
        page_state = dict(entry.get("page_state") or {})
        page_empty = dict(entry.get("page_empty") or {})
        discovery_cursor = int(entry.get("discovery_cursor", 0))

    additions = []
    scanned = 0
    next_discovery_cursor = discovery_cursor
    next_discovery_has_more = discovery_has_more
    next_page = None
    error = None
    try:
        if target == "__catalog__":
            raw_items, next_discovery_cursor, next_discovery_has_more, scanned = discover_catalog_wave(
                query=query,
                price_max=price,
                cursor=discovery_cursor,
                site_limit=6 if IS_RENDER_RUNTIME else 10,
                per_site_limit=8,
            )
            additions = _analyse_discovery_items(raw_items, query, price)
        else:
            next_page = int(page_state.get(target, 1)) + 1
            page_limits = {"eBay": 50, "Zalando": 50, "Vinted": 30}
            additions = rechercher_multi_marketplaces(
                marque=query,
                prix_max=price,
                plateformes=[target],
                limite=min(page_limits.get(target, 50), SEARCH_RESULT_LIMIT),
                page=next_page,
            )
    except Exception as exc:
        error = str(exc)[:240]
        additions = []

    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return None, 404
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, owner):
            return None, 404

        existing = [dict(item) for item in entry.get("results") or []]
        merged = _append_expansion_results(existing, additions)
        added = max(0, len(merged) - len(existing))
        entry["results"] = [dict(item, _rank_index=index) for index, item in enumerate(merged)]
        entry["source_counts"] = _marketplace_counts(merged)
        if added:
            entry["generation"] = int(entry.get("generation", 0)) + 1

        exhausted = set(entry.get("page_exhausted") or [])
        if target == "__catalog__":
            entry["discovery_cursor"] = next_discovery_cursor
            entry["discovery_has_more"] = bool(next_discovery_has_more)
            entry["catalog_scanned"] = int(entry.get("catalog_scanned", 0)) + int(scanned or 0)
        else:
            state = dict(entry.get("page_state") or {})
            empty = dict(entry.get("page_empty") or {})
            state[target] = next_page or int(state.get(target, 1))
            if added == 0:
                empty[target] = int(empty.get(target, 0)) + 1
            else:
                empty[target] = 0
            # Deux pages successives sans nouveauté = source épuisée pour cette
            # requête. Cela évite de boucler éternellement sur une page miroir.
            if empty[target] >= 2:
                exhausted.add(target)
            entry["page_state"] = state
            entry["page_empty"] = empty
            entry["page_exhausted"] = sorted(exhausted)

        entry["expansion_inflight"] = False
        entry["expansion_exhausted"] = (
            all(source in exhausted for source in EXPAND_PAGE_SOURCES)
            and not bool(entry.get("discovery_has_more", True))
        )
        return {
            "accepted": True,
            "busy": False,
            "source": "catalogue" if target == "__catalog__" else target,
            "page": next_page,
            "added": added,
            "total": len(merged),
            "catalog_scanned": int(entry.get("catalog_scanned", 0)),
            "exhausted": bool(entry.get("expansion_exhausted")),
            "error": error,
        }, 200


@app.post("/api/results/<token>/expand")
def expand_results(token):
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        return jsonify({"error": "Identifiant de recherche invalide."}), 400
    marketplace = str(request.args.get("marketplace") or "Toutes")[:80]
    payload, status = _expand_search_once(
        token,
        session.get("csrf_token"),
        marketplace=marketplace,
    )
    if payload is None:
        return jsonify({"error": "Recherche expirée. Relance le radar."}), status
    return jsonify(payload), status


@app.get("/api/results/<token>")
def more_results(token):
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        return jsonify({"error": "Identifiant de recherche invalide."}), 400
    try:
        offset = int(request.args.get("offset", INITIAL_RESULTS))
    except (TypeError, ValueError):
        return jsonify({"error": "Offset invalide."}), 400
    if offset < 0 or offset > SEARCH_RESULT_LIMIT:
        return jsonify({"error": "Offset hors limites."}), 400
    sort = request.args.get("sort", "relevance")
    if sort not in SAFE_SORTS:
        return jsonify({"error": "Tri invalide."}), 400
    marketplace = request.args.get("marketplace", "Toutes")[:80]
    allowed_marketplaces = set(_app_metadata()[1])
    with _cache_lock:
        cache_entry = _search_cache.get(token)
        if cache_entry is not None and secrets.compare_digest(
            str(cache_entry.get("owner") or ""), str(session.get("csrf_token") or "")
        ):
            allowed_marketplaces.update(str(name) for name in (cache_entry.get("source_counts") or {}).keys())
    if marketplace not in {"Toutes", *allowed_marketplaces}:
        return jsonify({"error": "Marketplace invalide."}), 400
    price_min = request.args.get("price_min")
    if price_min not in (None, ""):
        try:
            parsed_minimum = float(price_min)
        except (TypeError, ValueError):
            return jsonify({"error": "Prix minimum invalide."}), 400
        if not math.isfinite(parsed_minimum) or not (0 <= parsed_minimum <= 1_000_000):
            return jsonify({"error": "Prix minimum hors limites."}), 400
        price_min = parsed_minimum
    price_max = request.args.get("price_max")
    if price_max not in (None, ""):
        try:
            parsed_maximum = float(price_max)
        except (TypeError, ValueError):
            return jsonify({"error": "Prix maximum invalide."}), 400
        if not math.isfinite(parsed_maximum) or not (0 <= parsed_maximum <= 1_000_000):
            return jsonify({"error": "Prix maximum hors limites."}), 400
        price_max = parsed_maximum
    price_exact = request.args.get("price_exact")
    if price_exact not in (None, ""):
        try:
            parsed_exact = float(price_exact)
        except (TypeError, ValueError):
            return jsonify({"error": "Prix exact invalide."}), 400
        if not math.isfinite(parsed_exact) or not (0 <= parsed_exact <= 1_000_000):
            return jsonify({"error": "Prix exact hors limites."}), 400
        price_exact = parsed_exact
    price_tolerance = request.args.get("price_tolerance")
    if price_tolerance not in (None, ""):
        try:
            parsed_tolerance = float(price_tolerance)
        except (TypeError, ValueError):
            return jsonify({"error": "Tolérance de prix invalide."}), 400
        if not math.isfinite(parsed_tolerance) or not (0 <= parsed_tolerance <= 100):
            return jsonify({"error": "Tolérance de prix hors limites."}), 400
        price_tolerance = parsed_tolerance
    limit = request.args.get("limit")
    if limit is not None:
        try:
            parsed_limit = int(limit)
        except (TypeError, ValueError):
            return jsonify({"error": "Taille de lot invalide."}), 400
        if not (1 <= parsed_limit <= MAX_BATCH_SIZE):
            return jsonify({"error": "Taille de lot hors limites."}), 400
    else:
        parsed_limit = RESULT_BATCH_SIZE
    exclude = request.args.get("exclude")
    if exclude is not None:
        exclude = exclude[:200]
    risk = request.args.get("risk", "all")
    if risk not in {"all", "hide_high", "low_only"}:
        risk = "all"
    identity = request.args.get("identity", "confirmed")
    if identity not in {"all", "confirmed", "strong", "unverified"}:
        identity = "confirmed"
    page = _result_page(token, offset, limit=parsed_limit, sort=sort, marketplace=marketplace, price_min=price_min, price_max=price_max, price_exact=price_exact, price_tolerance=price_tolerance, exclude=exclude, risk=risk, identity=identity, owner=session.get("csrf_token"))
    if page is None:
        return jsonify({"error": "Recherche expirée. Relance le radar."}), 404
    return jsonify(page)


@app.post("/api/results/<token>/image-rank")
def rank_results_by_image(token):
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        return jsonify({"error": "Identifiant de recherche invalide."}), 400
    upload = request.files.get("image")
    if upload is None or upload.mimetype not in {"image/jpeg", "image/png", "image/webp"}:
        return jsonify({"error": "Choisis une image JPEG, PNG ou WebP de 2 Mo maximum."}), 400
    try:
        feature = image_feature(upload.stream.read(MAX_IMAGE_BYTES + 1))
    except ValueError:
        return jsonify({"error": "Choisis une image JPEG, PNG ou WebP de 2 Mo maximum."}), 400
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None or not secrets.compare_digest(str(entry.get("owner") or ""), str(session.get("csrf_token") or "")):
            return jsonify({"error": "Recherche expirée. Relance le radar."}), 404
        results = [dict(item) for item in entry["results"]]
    compared = _rank_by_image(results, feature)
    with _cache_lock:
        entry = _search_cache.get(token)
        if entry is None or not secrets.compare_digest(str(entry.get("owner") or ""), str(session.get("csrf_token") or "")):
            return jsonify({"error": "Recherche expirée. Relance le radar."}), 404
        for index, item in enumerate(entry["results"]):
            item.pop("similarite_image", None)
            if "similarite_image" in results[index]:
                item["similarite_image"] = results[index]["similarite_image"]
    page = _result_page(token, 0, INITIAL_RESULTS, sort="image_similarity", owner=session.get("csrf_token"))
    return jsonify({"results": page["results"], "next_offset": page["next_offset"], "has_more": page["has_more"], "total": page["total"], "compared": compared})


@app.route("/", methods=["GET", "POST"])
def accueil():
    catalog_sites, active_marketplaces = _app_metadata()
    annonces = []
    recherche = None
    erreur = None
    search_token = None
    total_results = 0
    selected_platform = "Toutes"
    prix_saisi = ""
    prefill_query = ""
    reference_saisie = ""
    reference_stricte = False
    initial_lots = MOBILE_INITIAL_RESULTS if _is_mobile_request() else INITIAL_RESULTS
    search_pending = False
    search_generation = 0
    interpreted_query = None

    if request.method == "GET":
        prefill_query = request.args.get("q", "").strip()[:120]
        prix_saisi = request.args.get("price", "").strip()[:30]
        requested_platform = request.args.get("marketplace", "Toutes").strip()[:80]
        selected_platform = requested_platform if requested_platform in {"Toutes", *active_marketplaces} else "Toutes"
        reference_saisie = request.args.get("ref", "").strip()[:60]
        reference_stricte = request.args.get("strict") == "1" and bool(_normalized_reference(reference_saisie))

    if request.method == "POST":
        recherche = request.form.get("marque", "").strip()[:120]
        understood = understand_query(recherche) if recherche else None
        interpreted_query = understood.canonical if understood and understood.corrected else None
        prix_saisi = request.form.get("prix", "").strip()[:30]
        selected_platform = request.form.get("plateforme", "Toutes").strip()[:80]
        reference_saisie = request.form.get("reference_exacte", "").strip()[:60]
        reference_stricte = request.form.get("reference_stricte") == "1"
        allowed_platforms = {"Toutes", *active_marketplaces}
        if selected_platform not in allowed_platforms:
            erreur = _message("invalid_marketplace")
            selected_platform = "Toutes"
        lots_raw = request.form.get("lots", "").strip()
        default_initial_lots = MOBILE_INITIAL_RESULTS if _is_mobile_request() else INITIAL_RESULTS
        try:
            initial_lots = max(1, min(int(lots_raw), MAX_BATCH_SIZE)) if lots_raw else default_initial_lots
        except (TypeError, ValueError):
            initial_lots = default_initial_lots
        normalized_reference = _normalized_reference(reference_saisie)
        reference_stricte = reference_stricte and bool(normalized_reference)
        if reference_saisie and len(normalized_reference) < 3:
            erreur = _message("invalid_reference")
        if recherche and prix_saisi:
            try:
                prix = float(prix_saisi)
                if not (0 < prix <= 1_000_000):
                    erreur = _message("price_range")
                elif erreur is None:
                    plateformes = None if selected_platform == "Toutes" else [selected_platform]
                    base_connector_query = _canonicalize_search_query(recherche)
                    connector_query = (
                        base_connector_query
                        if not normalized_reference or normalized_reference in _normalized_reference(base_connector_query)
                        else f"{base_connector_query} {reference_saisie}"
                    )
                    # Recherche "Toutes" : seules les sources réellement rapides construisent
                    # le premier rendu. ASOS/Cdiscount (recherche profonde), AliExpress, Vinted et Grailed
                    # continuent ensuite indépendamment. Une source lente ne bloque donc plus
                    # l'affichage initial.
                    if plateformes is None:
                        fast_platforms = [
                            name for name in PROGRESSIVE_FAST_SOURCES
                            if name in active_marketplaces
                        ]
                        progressive_sources = [
                            name for name in PROGRESSIVE_BACKGROUND_SOURCES
                            if name in active_marketplaces
                        ]
                        # Tout connecteur actif non encore classé est traité en
                        # arrière-plan par défaut : une nouvelle source ne doit
                        # jamais ralentir accidentellement le premier rendu.
                        progressive_sources.extend(
                            name for name in active_marketplaces
                            if name not in fast_platforms and name not in progressive_sources
                        )
                    else:
                        fast_platforms = []
                        progressive_sources = []

                    progressive = plateformes is None and bool(progressive_sources)
                    if progressive:
                        fast_started = perf_counter()
                        # Premier rendu = seulement ce qu'il faut pour remplir
                        # l'écran. Les pages suivantes restent disponibles via
                        # l'infinite scroll, donc inutile d'analyser 100 cartes ici.
                        # V2.9.3 : premier affichage réellement rapide.
                        # On classe un petit lot eBay immédiatement ; les autres
                        # sources et pages enrichissent ensuite sans bloquer le rendu.
                        fast_limit = 14 if _is_mobile_request() else 18
                        all_results = rechercher_multi_marketplaces(
                            marque=connector_query,
                            prix_max=prix,
                            plateformes=fast_platforms,
                            limite=fast_limit,
                            delai_total_secondes=(6 if IS_RENDER_RUNTIME else 12),
                            max_workers=min(2, max(1, len(fast_platforms))),
                        ) if fast_platforms else []
                        all_results = _rank_by_reference(all_results, reference_saisie, reference_stricte)
                        owner = session.get("csrf_token")
                        search_token = _cache_results(
                            all_results,
                            owner,
                            pending_sources=progressive_sources,
                            completed_sources=fast_platforms,
                            search_query=connector_query,
                            search_price=prix,
                        )
                        search_pending = bool(progressive_sources)
                        print(
                            f"[PROGRESSIF] premiers résultats prêts: {len(all_results)} "
                            f"en {perf_counter()-fast_started:.2f}s | "
                            f"arrière-plan: {', '.join(progressive_sources)} "
                            f"| workers={_PROGRESSIVE_WORKERS}"
                        )
                        for source in progressive_sources:
                            _progressive_executor.submit(
                                _finish_progressive_source,
                                search_token, connector_query, prix, source,
                                reference_saisie, reference_stricte, owner,
                            )
                    else:
                        single_limit = min(
                            SEARCH_RESULT_LIMIT,
                            max(80, min(initial_lots * 2, 120)),
                        )
                        all_results = rechercher_multi_marketplaces(
                            marque=connector_query, prix_max=prix,
                            plateformes=plateformes, limite=single_limit
                        )
                        all_results = _rank_by_reference(all_results, reference_saisie, reference_stricte)
                        search_token = _cache_results(
                            all_results, session.get("csrf_token"),
                            search_query=connector_query, search_price=prix,
                        )
                    first_page = _result_page(search_token, 0, initial_lots, identity="confirmed", owner=session.get("csrf_token")) if search_token else None
                    annonces = first_page["results"] if first_page else []
                    total_results = first_page["total"] if first_page else 0
            except ValueError:
                erreur = _message("invalid_price")
            except Exception:
                app.logger.exception("Échec contrôlé d'une recherche radar")
                erreur = _message("search_error")
        else:
            erreur = _message("missing_fields")

    return render_template(
        "index.html",
        marques=MARQUES,
        marketplaces=list(active_marketplaces),
        catalog_site_count=len(catalog_sites),
        annonces=annonces,
        recherche=recherche,
        prefill_query=prefill_query,
        erreur=erreur,
        prix_saisi=prix_saisi,
        selected_platform=selected_platform,
        reference_saisie=reference_saisie,
        reference_stricte=reference_stricte,
        search_token=search_token,
        total_results=total_results,
        initial_results=initial_lots,
        search_pending=search_pending,
        search_generation=search_generation,
        interpreted_query=interpreted_query,
        app_version=APP_VERSION,
        asset_version=ASSET_VERSION,
        mobile_request=_is_mobile_request(),
        subscription_plans=SUBSCRIPTION_PLANS,
        billing_ready=_billing_ready(),
        csrf_token=session["csrf_token"],
        csp_nonce=g.csp_nonce,
    )


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, host="127.0.0.1", port=5000)
