from collections import OrderedDict
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import logging
import math
import secrets
import unicodedata
from threading import Lock
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

from marketplaces.connectors import get_available_connectors
from marketplaces.catalog import get_sites
from radar_engine import rechercher_multi_marketplaces, _cle_unique_multi, _selection_diversifiee
from image_similarity import MAX_IMAGE_BYTES, download_listing_image, image_feature, similarity


app = Flask(__name__)
IS_PRODUCTION = os.environ.get("LUXE_RADAR_ENV", "development").lower() == "production"
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
        route_limit = 12 if request.endpoint == "accueil" else 30
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
        "connectors": len(marketplaces),
        "catalog_sites": len(sites),
        "billing_ready": _billing_ready(),
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
                ("Mise en production", "La configuration prévoit HTTPS, cookies sécurisés, CSP, CSRF, limitation de débit et secrets externes. Le paiement devient actif dès qu’une clé Stripe est configurée ; la confirmation par webhooks reste à brancher."),
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
                ("Production readiness", "The configuration provides for HTTPS, secure cookies, CSP, CSRF, rate limits and external secrets. Payments become active as soon as a Stripe key is configured; webhook confirmation is still to be connected."),
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
RESULT_BATCH_SIZE = 50
SEARCH_RESULT_LIMIT = _bounded_env_int("LUXE_RADAR_SEARCH_RESULT_LIMIT", 1000, 100, 2000)
MAX_BATCH_SIZE = 200
IMAGE_COMPARE_LIMIT = _bounded_env_int("LUXE_RADAR_IMAGE_COMPARE_LIMIT", 64, 16, 120)
CACHE_TTL_SECONDS = 30 * 60
MAX_CACHED_SEARCHES = 20

# Sources qui peuvent prendre sensiblement plus de temps ou varier davantage.
# Elles sont ajoutées progressivement après le premier rendu pour ne pas
# bloquer l'utilisateur. Une recherche ciblée sur une seule marketplace reste
# synchrone afin de garder un comportement simple et prévisible.
PROGRESSIVE_BACKGROUND_SOURCES = ("ASOS", "Cdiscount", "AliExpress", "DHgate", "1688", "Vinted", "Grailed")

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
_metadata_cache = {"sites": None, "marketplaces": None}
_metadata_lock = Lock()
_image_feature_cache = OrderedDict()
_IMAGE_FEATURE_CACHE_MAX = 200
_image_feature_lock = Lock()

# Recherche progressive : une tâche indépendante par source lente.
# Les mises à jour du cache sont atomiques, donc deux sources qui terminent au
# même moment ne peuvent pas écraser les résultats l'une de l'autre.
_progressive_executor = ThreadPoolExecutor(max_workers=7, thread_name_prefix="luxe-progressive")


MARQUES = [
    ("Nike", 20), ("Adidas", 20), ("Jordan", 30), ("New Balance", 25),
    ("Asics", 25), ("Salomon", 30), ("On", 30), ("Under Armour", 15),
    ("Essentials", 50),
    ("Ralph Lauren", 20), ("Lacoste", 20), ("Fred Perry", 20),
    ("Tommy Hilfiger", 20), ("Carhartt WIP", 20), ("Patagonia", 25),
    ("The North Face", 25), ("Arc'teryx", 35), ("Stone Island", 30),
    ("C.P. Company", 30), ("Diesel", 25), ("Moncler", 40),
    ("Canada Goose", 40), ("Burberry", 40), ("Palm Angels", 40),
    ("Ami Paris", 40), ("Jacquemus", 40), ("Acne Studios", 40),
    ("Off-White", 40), ("Gucci", 50), ("Prada", 50),
    ("Balenciaga", 50), ("Saint Laurent", 50), ("Dior", 50),
    ("Givenchy", 50), ("Fendi", 50), ("Valentino", 50),
    ("Maison Margiela", 50), ("Amiri", 50),
]


def _clean_cache(now=None):
    now = monotonic() if now is None else now
    expired = [
        token for token, entry in _search_cache.items()
        if now - entry["created_at"] > CACHE_TTL_SECONDS
    ]
    for token in expired:
        _search_cache.pop(token, None)
    while len(_search_cache) > MAX_CACHED_SEARCHES:
        _search_cache.popitem(last=False)


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


def _cache_results(results, owner=None, pending_sources=None, completed_sources=None):
    token = uuid4().hex
    pending_sources = list(dict.fromkeys(str(source) for source in (pending_sources or []) if str(source)))
    completed_sources = list(dict.fromkeys(str(source) for source in (completed_sources or []) if str(source)))
    with _cache_lock:
        _clean_cache()
        _search_cache[token] = {
            "created_at": monotonic(),
            "owner": str(owner or ""),
            "results": [dict(item, _rank_index=index) for index, item in enumerate(results or [])],
            "pending": bool(pending_sources),
            "pending_sources": pending_sources,
            "completed_sources": completed_sources,
            "failed_sources": [],
            "source_counts": _marketplace_counts(results),
            "generation": 0,
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


def _complete_progressive_source(token, source, additions, reference, strict, owner):
    """Fusion atomique d'une source progressive terminée."""
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
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
        entry["generation"] = int(entry.get("generation", 0)) + 1
        return len(existing), len(results), bool(pending)


def _fail_progressive_source(token, source, owner):
    with _cache_lock:
        entry = _search_cache.get(token)
        if entry is None:
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
    started = perf_counter()
    try:
        additions = rechercher_multi_marketplaces(
            marque=query,
            prix_max=price,
            plateformes=[source],
            limite=SEARCH_RESULT_LIMIT,
        )
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
    """Corrige uniquement les alias/fautes sûrs avant d'appeler les sources.

    Le texte saisi reste affiché tel quel dans l'interface ; cette version sert
    seulement aux connecteurs. On évite volontairement les corrections
    générales qui pourraient modifier un nom de modèle légitime.
    """
    value = " ".join(str(value or "").split())
    value = re.sub(
        r"(?i)\b(?:essantials|essencials|essensials|essentails)\b",
        "Essentials",
        value,
    )
    if re.search(r"(?i)\bnike\s+running\b", value):
        value = re.sub(r"(?i)\bmiller\b", "Miler", value)
    return value


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


def _sorted_results(results, sort="relevance", marketplace="Toutes", price_min=None, price_max=None, price_exact=None, price_tolerance=None, exclude="", risk="all"):
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


def _result_page(token, offset, limit=RESULT_BATCH_SIZE, sort="relevance", marketplace="Toutes", price_min=None, price_max=None, price_exact=None, price_tolerance=None, exclude="", risk="all", owner=None):
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
        results = _sorted_results(entry["results"], sort=sort, marketplace=marketplace, price_min=price_min, price_max=price_max, price_exact=price_exact, price_tolerance=price_tolerance, exclude=exclude, risk=risk)
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
        return jsonify({
            "pending": bool(entry.get("pending")),
            "generation": int(entry.get("generation", 0)),
            "total": len(entry.get("results") or []),
            "pending_sources": list(entry.get("pending_sources") or []),
            "completed_sources": list(entry.get("completed_sources") or []),
            "failed_sources": list(entry.get("failed_sources") or []),
            "source_counts": dict(entry.get("source_counts") or {}),
        })


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
    if marketplace not in {"Toutes", *_app_metadata()[1]}:
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
    page = _result_page(token, offset, limit=parsed_limit, sort=sort, marketplace=marketplace, price_min=price_min, price_max=price_max, price_exact=price_exact, price_tolerance=price_tolerance, exclude=exclude, risk=risk, owner=session.get("csrf_token"))
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
    initial_lots = INITIAL_RESULTS
    search_pending = False
    search_generation = 0

    if request.method == "GET":
        prefill_query = request.args.get("q", "").strip()[:120]
        prix_saisi = request.args.get("price", "").strip()[:30]
        requested_platform = request.args.get("marketplace", "Toutes").strip()[:80]
        selected_platform = requested_platform if requested_platform in {"Toutes", *active_marketplaces} else "Toutes"
        reference_saisie = request.args.get("ref", "").strip()[:60]
        reference_stricte = request.args.get("strict") == "1" and bool(_normalized_reference(reference_saisie))

    if request.method == "POST":
        recherche = request.form.get("marque", "").strip()[:120]
        prix_saisi = request.form.get("prix", "").strip()[:30]
        selected_platform = request.form.get("plateforme", "Toutes").strip()[:80]
        reference_saisie = request.form.get("reference_exacte", "").strip()[:60]
        reference_stricte = request.form.get("reference_stricte") == "1"
        allowed_platforms = {"Toutes", *active_marketplaces}
        if selected_platform not in allowed_platforms:
            erreur = _message("invalid_marketplace")
            selected_platform = "Toutes"
        lots_raw = request.form.get("lots", "").strip()
        try:
            initial_lots = max(1, min(int(lots_raw), MAX_BATCH_SIZE)) if lots_raw else INITIAL_RESULTS
        except (TypeError, ValueError):
            initial_lots = INITIAL_RESULTS
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
                    progressive_sources = (
                        [name for name in PROGRESSIVE_BACKGROUND_SOURCES if name in active_marketplaces]
                        if plateformes is None else []
                    )
                    progressive = plateformes is None and bool(progressive_sources)
                    if progressive:
                        fast_platforms = [name for name in active_marketplaces if name not in progressive_sources]
                        fast_started = perf_counter()
                        all_results = rechercher_multi_marketplaces(
                            marque=connector_query,
                            prix_max=prix,
                            plateformes=fast_platforms,
                            limite=SEARCH_RESULT_LIMIT,
                        ) if fast_platforms else []
                        all_results = _rank_by_reference(all_results, reference_saisie, reference_stricte)
                        owner = session.get("csrf_token")
                        search_token = _cache_results(
                            all_results,
                            owner,
                            pending_sources=progressive_sources,
                            completed_sources=fast_platforms,
                        )
                        search_pending = bool(progressive_sources)
                        print(
                            f"[PROGRESSIF] premiers résultats prêts: {len(all_results)} "
                            f"en {perf_counter()-fast_started:.2f}s | "
                            f"arrière-plan: {', '.join(progressive_sources)}"
                        )
                        for source in progressive_sources:
                            _progressive_executor.submit(
                                _finish_progressive_source,
                                search_token, connector_query, prix, source,
                                reference_saisie, reference_stricte, owner,
                            )
                    else:
                        all_results = rechercher_multi_marketplaces(
                            marque=connector_query, prix_max=prix, plateformes=plateformes, limite=SEARCH_RESULT_LIMIT
                        )
                        all_results = _rank_by_reference(all_results, reference_saisie, reference_stricte)
                        search_token = _cache_results(all_results, session.get("csrf_token")) if all_results else None
                    first_page = _result_page(search_token, 0, initial_lots, owner=session.get("csrf_token")) if search_token else None
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
        subscription_plans=SUBSCRIPTION_PLANS,
        billing_ready=_billing_ready(),
        csrf_token=session["csrf_token"],
        csp_nonce=g.csp_nonce,
    )


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, host="127.0.0.1", port=5000)
