from collections import OrderedDict
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import json
import logging
import math
import secrets
import time
import unicodedata
import hashlib
from threading import Event, Lock, Semaphore
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
import collector
import index_engine
import learn
import search_sessions

from connector_registry import get_available_connectors
from marketplaces.connectors import get_connector
from marketplaces.catalog import get_sites
from radar_engine import rechercher_multi_marketplaces, _cle_unique_multi, _selection_diversifiee, _analyser_resultat_multi
from image_similarity import MAX_IMAGE_BYTES, download_listing_image, image_feature, similarity
from search_understanding import understand_query, suggest_queries, canonicalize_search_query
from marketplaces.connectors.universal import discover_catalog_wave
from marketplaces.source_health import current_environment, registry as source_health

try:
    from search_intent import parse_search_intent as _parse_intent_impl
except Exception:  # pragma: no cover - observability must never crash the app
    _parse_intent_impl = None


def _parse_intent(query):
    if _parse_intent_impl is None:
        return None
    try:
        return _parse_intent_impl(query)
    except Exception:
        return None


app = Flask(__name__)
APP_VERSION = "3.8.0"
ASSET_VERSION = "20260821-380"
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
    "explication_pertinence", "conflit_pertinence",
}
REFERENCE_CATEGORY_ORDER = {"EXCELLENTE AFFAIRE": 0, "BONNE AFFAIRE": 1, "INTERESSANTE": 2, "A VERIFIER": 3, "DOUTEUSE": 4, "A IGNORER": 5}
SERVER_MESSAGES = {
    "missing_fields": {"fr": "Indique un produit, une marque ou une référence.", "en": "Enter a product, brand or reference."},
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
    try:
        import resource as _resource
        memory_rss_mb = round(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        memory_rss_mb = None
    return jsonify({
        "status": "ok",
        "version": APP_VERSION,
        "progressive": True,
        "progressive_workers": _PROGRESSIVE_WORKERS,
        "coverage_mode": "max",
        "connectors": len(marketplaces),
        "catalog_sites": len(sites),
        "billing_ready": _billing_ready(),
        "index_enabled": index_engine.index_enabled(),
        "diagnostics": {
            "env": current_environment(),
            "queue_p50_ms": source_health.queue_p50(),
            "queue_p95_ms": source_health.queue_p95(),
            "network_p50_ms": source_health.network_p50(),
            "network_p95_ms": source_health.network_p95(),
            "cooldown_count": source_health.cooldown_count(),
            "memory_rss_mb": memory_rss_mb,
        },
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
        "index_enabled": index_engine.index_enabled(),
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
        return jsonify({"understanding": None, "suggestions": [], "catalog_total": 0, "preview": []})
    info = understand_query(query)
    curated = suggest_queries(query, limit=8)
    indexed_suggestions = []
    indexed = index_engine.IndexSearch([], 0, None, index_engine.canonical_query(query))
    if index_engine.index_enabled():
        try:
            indexed_suggestions = index_engine.suggest(query, limit=8)
            indexed = index_engine.search(query, identity="confirmed", limit=6)
        except Exception as exc:
            app.logger.debug("Aide de recherche indexée indisponible: %s", str(exc)[:160])
    suggestions = []
    seen = set()
    # Correction / modèles connus d'abord, puis vraies offres déjà présentes
    # dans le catalogue. Les compteurs viennent uniquement de données indexées.
    for item in [*curated, *indexed_suggestions]:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or item.get("label") or "").strip()[:180]
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        clean = {
            "value": value,
            "label": str(item.get("label") or value)[:180],
            "kind": str(item.get("kind") or "suggestion")[:40],
        }
        if item.get("count") is not None:
            clean["count"] = max(0, int(item.get("count") or 0))
        if item.get("price") not in (None, ""):
            price = float(item.get("price") or 0)
            if math.isfinite(price) and price > 0:
                clean["price"] = round(price, 2)
        suggestions.append(clean)
        if len(suggestions) >= 10:
            break
    preview = []
    for item in indexed.results[:6]:
        public = _public_result(item)
        preview.append({
            "title": str(public.get("titre") or public.get("title") or "")[:180],
            "marketplace": str(public.get("marketplace") or "")[:80],
            "price": _safe_number(public.get("prix_total", public.get("prix")), 0),
            "image": str(public.get("image") or "")[:2048],
        })
    return jsonify({
        "understanding": info.to_dict(),
        "suggestions": suggestions,
        "catalog_total": int(indexed.total),
        "catalog_age_seconds": indexed.age_seconds,
        "preview": preview,
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
SEARCH_PAGE_SIZE = 50
SEARCH_RESULT_LIMIT = _bounded_env_int(
    "LUXE_RADAR_SEARCH_RESULT_LIMIT",
    5000,
    500,
    10000,
)
MAX_BATCH_SIZE = 200
IMAGE_COMPARE_LIMIT = _bounded_env_int("LUXE_RADAR_IMAGE_COMPARE_LIMIT", 64, 16, 120)
CACHE_TTL_SECONDS = (20 if IS_RENDER_RUNTIME else 30) * 60
MAX_CACHED_SEARCHES = _bounded_env_int(
    "LUXE_RADAR_MAX_CACHED_SEARCHES",
    5 if IS_RENDER_RUNTIME else 12,
    3,
    30,
)
# V4.1 : les tokens de recherche survivent aux restarts Gunicorn via SQLite.
# 30 à 60 minutes, nettoyés périodiquement en arrière-plan du cache RAM.
SEARCH_SESSION_TTL_SECONDS = _bounded_env_int(
    "LUXE_RADAR_SEARCH_SESSION_TTL_MINUTES",
    45,
    30,
    60,
) * 60
_SESSION_DISK_CLEANUP_EVERY_SECONDS = 300

# Pipeline progressif V2.8.6. Seules deux sources HTTP éprouvées construisent
# le premier rendu ; toutes les autres enrichissent ensuite le même catalogue.
# L'ordre est volontaire : sources HTTP utiles d'abord, navigateurs/marketplaces
# bloquées ensuite. Une recherche ciblée sur UNE marketplace reste synchrone.
PROGRESSIVE_FAST_SOURCES = ("eBay",)
PROGRESSIVE_BACKGROUND_SOURCES = (
    "i-Run", "Direct Running", "Alltricks", "Deporvillage",
    "Vinted", "Zalando", "ASOS", "21RUN", "Running Point",
    "MisterRunning", "Hardloop", "Ekosport", "Courir", "SSENSE",
    "Cdiscount", "Spartoo", "Footshop", "JD Sports",
    "AliExpress", "DHgate", "67behaviour", "1688", "Grailed",
    "Nike", "Adidas", "New Balance Store", "On Store", "Salomon Store",
    "Veja Store", "Puma", "Converse", "Foot Locker", "Sneakersnstuff",
    "End Clothing", "Cettire", "The Outnet", "Rouje", "Represent",
    "Kith", "Asphaltgold", "BSTN", "43einhalb", "Laced",
    "Galeries Lafayette", "La Redoute",
)

_RUNNING_BRANDS = {
    "On", "Asics", "Hoka", "Salomon", "Saucony", "Mizuno", "Brooks",
    "New Balance", "Nike", "Adidas", "Puma", "Under Armour",
    "Veja", "Converse",
}
_LUXURY_FASHION_BRANDS = {
    "Stone Island", "C.P. Company", "Moncler", "Gucci", "Prada", "Dior",
    "Balenciaga", "Versace", "Burberry", "Off-White", "Supreme", "Stussy",
    "Palace", "Amiri", "Ralph Lauren", "Lacoste", "Carhartt", "Arc'teryx",
    "The North Face", "Patagonia", "Represent", "Kith",
}

# Univers « Luxe » : requêtes réelles utilisées pour remplir le catalogue quand
# l'utilisateur clique sur Luxe sans texte. Le mot « luxury » n'est jamais envoyé
# aux connecteurs : seules ces marques (et les offres déjà indexées) alimentent
# le résultat.
_LUXURY_UNIVERSE_QUERIES = (
    "Stone Island", "Moncler", "Gucci", "Balenciaga", "Prada",
    "Dior", "Off-White", "Ralph Lauren",
)

def _rotating_luxury_query() -> str:
    import time as _time
    return _LUXURY_UNIVERSE_QUERIES[int(_time.strftime("%j")) % len(_LUXURY_UNIVERSE_QUERIES)]

def _progressive_source_order(query, active_marketplaces):
    """Met les marchands les plus plausibles et productifs en tête.

    L'ordre combine l'intention de la requête (running/fashion/générique) et la
    santé observée pour l'environnement courant :
    - source productive récente -> TIER A (devance la vague) ;
    - source vide lente répétée -> TIER C (tout à la fin) ;
    - source en cooldown/bloquée -> omise (aucun worker consommé).
    La productivité reste dynamique par requête : on ne fige aucun classement.
    """
    try:
        info = understand_query(query)
        brand = getattr(info, "brand", None)
        product_type = getattr(info, "product_type", None)
    except Exception:
        brand = None
        product_type = None
    running_priority = [
        "eBay", "i-Run", "Direct Running", "21RUN", "Running Point",
        "MisterRunning", "Alltricks", "Deporvillage", "Vinted", "Zalando",
        "ASOS", "Hardloop", "Ekosport", "Courir", "Footshop", "JD Sports",
        "Nike", "Adidas", "New Balance Store", "On Store", "Salomon Store",
        "Puma", "Converse", "Foot Locker", "Sneakersnstuff",
        "SSENSE", "Spartoo", "Cdiscount", "Grailed", "67behaviour",
        "AliExpress", "DHgate", "1688",
    ]
    fashion_priority = [
        "eBay", "Vinted", "SSENSE", "ASOS", "Zalando", "Grailed",
        "Courir", "Spartoo", "Footshop", "JD Sports", "Cdiscount",
        "Rouje", "Represent", "Kith", "Laced", "End Clothing",
        "Cettire", "The Outnet", "Galeries Lafayette", "La Redoute",
        "Nike", "Adidas", "Puma", "Converse", "Veja Store",
        "67behaviour", "AliExpress", "DHgate",
        "i-Run", "Direct Running", "Alltricks", "Deporvillage",
        "21RUN", "Running Point", "MisterRunning", "Hardloop", "Ekosport", "1688",
    ]
    generic_priority = [
        "eBay", "Vinted", "ASOS", "Zalando", "SSENSE", "Courir",
        "Spartoo", "Footshop", "JD Sports", "Grailed",
        "Nike", "Adidas", "New Balance Store", "Puma", "Converse",
        "Foot Locker", "Sneakersnstuff", "End Clothing", "Cettire",
        "The Outnet", "Laced", "Asphaltgold", "BSTN", "43einhalb",
        "Galeries Lafayette", "La Redoute",
        "i-Run", "Direct Running", "Alltricks", "Deporvillage",
        "21RUN", "Running Point", "MisterRunning", "Hardloop", "Ekosport",
        "Cdiscount", "67behaviour", "AliExpress", "DHgate", "1688",
    ]
    if brand in _RUNNING_BRANDS or product_type == "chaussures":
        preferred = running_priority
    elif brand in _LUXURY_FASHION_BRANDS:
        preferred = fashion_priority
    else:
        preferred = generic_priority
    active = list(active_marketplaces or [])
    ranked = {}
    for index, name in enumerate(preferred):
        if name in active:
            ranked[name] = index
    for index, name in enumerate(active):
        ranked.setdefault(name, len(preferred) + index)
    scored = []
    for name in active:
        base = ranked.get(name, len(preferred))
        score = source_health.priority_score(name, base)
        if score is None:
            continue
        scored.append((score, base, name))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [name for _score, _base, name in scored]

SUBSCRIPTION_PLANS = {
    "free": {
        "name": "Gratuit", "monthly": 0, "yearly": 0,
        "tagline": "Comparer les prix sans payer.",
    },
    # Les identifiants internes restent stables pour ne pas casser la couche
    # de billing existante. Dans l'interface, `pro` = Premium et
    # `reseller` = Pro / revendeur.
    "pro": {
        "name": "Premium", "monthly": 4.99, "yearly": 49.90,
        "tagline": "Veille, alertes et Radar automatique.",
    },
    "reseller": {
        "name": "Pro", "monthly": 12.99, "yearly": 129.90,
        "tagline": "Outils avancés pour acheter et revendre.",
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


@app.get("/api/index/status")
def index_status():
    state = index_engine.stats()
    # Never expose the server filesystem path publicly.
    state.pop("db_path", None)
    state["mode"] = "hybrid-index-first"
    state["persistent_hint"] = bool(os.environ.get("LUXE_RADAR_INDEX_DB"))
    return jsonify(state)


@app.get("/api/collector/status")
def collector_status():
    """Panneau de statut du collecteur profond (queue, passes récentes)."""
    try:
        # Collector.status() inclut déjà un snapshot DB fail-fast. Ne pas
        # refaire collector_stats() ici : cette duplication pouvait bloquer
        # plusieurs secondes sous contention SQLite.
        return jsonify(_collector.status())
    except Exception as exc:  # pragma: no cover - défensif
        return jsonify({"enabled": collector.COLLECTOR_ENABLED, "error": str(exc)[:200]})


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
    3 if IS_RENDER_RUNTIME else 5,
    1,
    5,
)
_progressive_executor = ThreadPoolExecutor(
    max_workers=_PROGRESSIVE_WORKERS,
    thread_name_prefix="luxe-progressive",
)
# V3.6 : écritures SQLite hors du chemin critique de la recherche.
_index_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="luxe-index")

# V3.8 : collecteur de catalogue profond en thread daemon (hors hot path).
# S'il meurt ou n'est pas démarré, la web app continue de servir l'index
# déjà persisté sur disque : la lecture ne dépend jamais du collecteur.
_collector = collector.Collector()


def _start_background_workers():
    """Démarre collector + learning une seule fois (appelé par hook Gunicorn ou __main__)."""
    try:
        if collector.COLLECTOR_ENABLED:
            _collector.start()
    except Exception:  # pragma: no cover - le collecteur ne doit pas bloquer l'app
        pass
    try:
        import index_engine as _ie
        learn.start_learn_worker(db_path=_ie.default_db_path())
    except Exception:  # pragma: no cover - le learning ne doit pas bloquer l'app
        pass


def _index_results_async(results, query):
    if not results or not query or not index_engine.index_enabled():
        return
    snapshot = [dict(item) for item in results if isinstance(item, dict)]
    if not snapshot:
        return
    def _write():
        try:
            index_engine.upsert_results(snapshot, query)
        except Exception as exc:
            app.logger.warning("Index V3.6 indisponible: %s", str(exc)[:180])
    _index_executor.submit(_write)

# Un seul navigateur Playwright lourd à la fois sur le même process. Les deux
# workers progressifs peuvent continuer à faire du HTTP en parallèle.
_browser_progressive_semaphore = Semaphore(1)

print(
    f"[LUXE RADAR] V{APP_VERSION} | instant=global-index/background-live | "
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
        # V4.1 : NE PAS supprimer la ligne SQLite ici. L'éviction RAM (20-30 min)
        # précède le TTL session (45 min) : la ligne reste pour la restauration
        # après un restart. Seul _clean_sessions_disk la purge (by updated_at).
    while len(_search_cache) > MAX_CACHED_SEARCHES:
        token, entry = _search_cache.popitem(last=False)
        owner = str(entry.get("owner") or "")
        if owner and _progressive_owner_tokens.get(owner) == token:
            _progressive_owner_tokens.pop(owner, None)


# V4.1 : nettoyage borné des sessions SQLite (jamais plus d'une écriture
# toutes les 5 minutes, même si _clean_cache est appelé très souvent).
_session_disk_cleanup_next = 0.0


def _clean_sessions_disk(now=None):
    global _session_disk_cleanup_next
    now = monotonic() if now is None else now
    if now < _session_disk_cleanup_next:
        return
    _session_disk_cleanup_next = now + _SESSION_DISK_CLEANUP_EVERY_SECONDS
    try:
        search_sessions.delete_expired(SEARCH_SESSION_TTL_SECONDS)
    except Exception:
        app.logger.warning("Nettoyage des sessions de recherche indisponible", exc_info=True)


def _persistable_state(entry):
    """Copie légère des curseurs/statuts à garder entre deux workers."""
    results = []
    for item in (entry.get("results") or [])[:SEARCH_RESULT_LIMIT]:
        cleaned = dict(item)
        cleaned.pop("_rank_index", None)
        results.append(cleaned)
    return {
        "results": results,
        "page_state": dict(entry.get("page_state") or {}),
        "page_empty": dict(entry.get("page_empty") or {}),
        "recall_limit": dict(entry.get("recall_limit") or {}),
        "recall_empty": dict(entry.get("recall_empty") or {}),
        "page_exhausted": list(entry.get("page_exhausted") or []),
        "discovery_cursor": int(entry.get("discovery_cursor", 0)),
        "discovery_has_more": bool(entry.get("discovery_has_more", True)),
        "expansion_round": int(entry.get("expansion_round", 0)),
        "expansion_exhausted": bool(entry.get("expansion_exhausted")),
        "catalog_scanned": int(entry.get("catalog_scanned", 0)),
        "source_pages": dict(entry.get("source_pages") or {}),
        "completed_sources": list(entry.get("completed_sources") or []),
        "failed_sources": list(entry.get("failed_sources") or []),
        "pending_sources": list(entry.get("pending_sources") or []),
        "live_added": int(entry.get("live_added", 0)),
        "duplicates_total": int(entry.get("duplicates_total", 0)),
        "received_total": int(entry.get("received_total", 0)),
        "generation": int(entry.get("generation", 0)),
        "index_mode": bool(entry.get("index_mode")),
        "index_hit_count": int(entry.get("index_hit_count", 0)),
        "index_total": int(entry.get("index_total", 0)),
        "index_age_seconds": entry.get("index_age_seconds"),
    }


def _persist_search_session(entry, token, owner):
    """Upsert SQLite, hors chemin critique : un échec ne casse jamais la réponse."""
    if not token:
        return
    try:
        search_sessions.save_search_session(
            token=token,
            owner=owner,
            search_request=str(entry.get("search_query") or ""),
            search_price_raw=str(entry.get("search_price") or "")
            if entry.get("search_price") not in (None, "")
            else "",
            selected_platform=str(entry.get("selected_platform") or "Toutes"),
            reference=str(entry.get("reference") or ""),
            reference_stricte=bool(entry.get("reference_stricte")),
            universe=str(entry.get("universe") or ""),
            state=_persistable_state(entry),
        )
        # Nettoyage SQLite hors de _cache_lock : une contention disque ne doit
        # jamais bloquer /status ni /api/results pendant le scroll.
        _clean_sessions_disk()
    except Exception:
        app.logger.warning("Session de recherche non persistée: %s", token, exc_info=True)


def _apply_restored_state(token, restored):
    """Réapplique les curseurs/infinite-scroll après une restauration SQLite."""
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return
        for key in (
            "page_state", "page_empty", "recall_limit", "recall_empty",
            "page_exhausted", "discovery_cursor", "discovery_has_more",
            "expansion_round", "expansion_exhausted", "catalog_scanned",
            "source_pages", "completed_sources", "failed_sources",
            "live_added", "duplicates_total", "received_total",
            "generation", "index_hit_count", "index_total",
            "index_age_seconds", "index_mode",
        ):
            if key in restored:
                entry[key] = restored[key]
        entry["index_mode"] = bool(restored.get("index_mode", entry.get("index_mode")))
    _persist_search_session(entry, token, str(entry.get("owner") or ""))


# Verrou anti-double-restauration : si deux requêtes concurrentes découvrent le
# même token manquant, une seule reconstruit le cache. Les autres attendent
# brièvement la fin de cette restauration au lieu de renvoyer un faux 404.
_restore_lock = Lock()
_restore_in_progress: dict[str, Event] = {}
_RESTORE_WAIT_SECONDS = 2.5


def _restore_search_session(token, owner, active_marketplaces):
    """Reconstruit un token disparu du RAM depuis l'état SQLite (après restart).

    Le catalogue exact (résultats + curseurs d'infinite scroll + paramètres)
    est ressemé dans le cache RAM. Les sources encore en attente (ni terminées
    ni en échec) sont relancées pour continuer le pipeline progressif.
    """
    owner = str(owner or "")
    if not owner or not token:
        return None
    wait_event = None
    restore_event = None
    with _restore_lock:
        wait_event = _restore_in_progress.get(token)
        if wait_event is None:
            with _cache_lock:
                _clean_cache()
                existing = _search_cache.get(token)
                if existing is not None and secrets.compare_digest(str(existing.get("owner") or ""), owner):
                    return existing
            restore_event = Event()
            _restore_in_progress[token] = restore_event

    # Une autre requête restaure déjà ce token. Attendre hors du verrou global
    # évite la course status/expand observée après un restart Gunicorn.
    if wait_event is not None:
        wait_event.wait(_RESTORE_WAIT_SECONDS)
        with _cache_lock:
            _clean_cache()
            existing = _search_cache.get(token)
            if existing is not None and secrets.compare_digest(str(existing.get("owner") or ""), owner):
                return existing
        return None

    try:
        try:
            record = search_sessions.load_search_session(token)
        except Exception:
            app.logger.warning("Session SQLite illisible: %s", token, exc_info=True)
            return None
        if record is None:
            return None
        if not secrets.compare_digest(str(record.get("owner") or ""), owner):
            return None
        if (time.time() - float(record.get("updated_at") or 0)) > SEARCH_SESSION_TTL_SECONDS:
            try:
                search_sessions.delete_search_session(token)
            except Exception:
                pass
            return None

        state = dict(record.get("state") or {})
        done = set(str(name) for name in (state.get("completed_sources") or []))
        done.update(str(name) for name in (state.get("failed_sources") or []))
        pending = [str(name) for name in (state.get("pending_sources") or []) if str(name) not in done]
        search_request = str(record.get("search_request") or "").strip()
        if not search_request:
            return None
        price_raw = str(record.get("search_price_raw") or "").strip()
        try:
            price = float(price_raw) if price_raw else 1_000_000.0
        except (TypeError, ValueError):
            price = 1_000_000.0
        selected_platform = str(record.get("selected_platform") or "Toutes")
        reference = str(record.get("reference") or "").strip()
        reference_stricte = bool(record.get("reference_stricte"))
        universe = str(record.get("universe") or "").strip().lower()
        results = [dict(item) for item in (state.get("results") or [])]

        with _cache_lock:
            _clean_cache()
            token_alive = _search_cache.get(token)
        if token_alive is not None:
            return token_alive

        _cache_results(
            results, owner,
            pending_sources=pending,
            completed_sources=list(state.get("completed_sources") or []),
            search_query=search_request, search_price=price,
            index_mode=bool(state.get("index_mode")),
            index_hit_count=int(state.get("index_hit_count", 0)),
            index_total=int(state.get("index_total", 0)),
            index_age_seconds=state.get("index_age_seconds"),
            selected_platform=selected_platform,
            reference=reference,
            reference_stricte=reference_stricte,
            universe=universe,
            reuse_token=token,
        )
        _apply_restored_state(token, state)
        if pending:
            ref = "" if universe else reference
            strict = False if universe else reference_stricte
            skipped = source_health.skipped_sources(pending)
            if skipped:
                _mark_progressive_skipped(token, skipped, owner)
            runnable = [name for name in pending if name not in set(skipped)]
            for source in runnable:
                _progressive_executor.submit(
                    _finish_progressive_source,
                    token, search_request, price, source, ref, strict, owner,
                )
        print(f"[SESSION][RESTORE] {token} {len(results)} résultats restaurés, {len(pending)} source(s) relancée(s)")
        with _cache_lock:
            _clean_cache()
            return _search_cache.get(token)
    finally:
        with _restore_lock:
            done_event = _restore_in_progress.pop(token, None)
            if done_event is not None:
                done_event.set()


def _ensure_search_session(token, owner, active_marketplaces):
    """Retourne l'entrée RAM d'un token, en le restaurant depuis SQLite au besoin."""
    owner = str(owner or "")
    if not token:
        return None
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is not None:
            if entry.get("owner") and not secrets.compare_digest(str(entry.get("owner") or ""), owner):
                return None
            return entry
    if not owner:
        return None
    return _restore_search_session(token, owner, active_marketplaces)


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
    search_query=None, search_price=None, index_mode=False,
    index_hit_count=0, index_total=0, index_age_seconds=None,
    selected_platform=None, reference=None, reference_stricte=False,
    universe="", reuse_token=None,
):
    token = reuse_token or uuid4().hex
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
            if previous is not None and previous_token != token:
                previous["cancelled"] = True
                previous["pending"] = False
                previous["pending_sources"] = []
            if pending_sources:
                _progressive_owner_tokens[owner_key] = token
            else:
                _progressive_owner_tokens.pop(owner_key, None)

        _search_cache[token] = {
            "created_at": monotonic(),
            "started_at": time.time(),
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
            "index_mode": bool(index_mode),
            "index_hit_count": int(index_hit_count or 0),
            "index_total": int(index_total or 0),
            "index_age_seconds": _safe_number(index_age_seconds, None),
            # V4.1 : paramètres d'origine persistés pour restaurer un token.
            "selected_platform": str(selected_platform or ""),
            "reference": str(reference or ""),
            "reference_stricte": bool(reference_stricte),
            "universe": str(universe or ""),
            # Infinite-scroll V3.7 MAX RECALL: every source that can expose a
            # real page gets its own cursor. Non-page connectors use a bounded
            # recall widening pass (initial cap -> 100) instead of fabricating
            # pages that do not exist.
            "page_state": {source: 1 for source in EXPAND_PAGE_SOURCES},
            "page_empty": {source: 0 for source in EXPAND_PAGE_SOURCES},
            "recall_limit": dict(EXPAND_RECALL_INITIAL_LIMITS),
            "recall_empty": {source: 0 for source in EXPAND_RECALL_SOURCES},
            "page_exhausted": [],
            "discovery_cursor": 0,
            "discovery_has_more": True,
            "expansion_round": 0,
            "expansion_inflight": False,
            "expansion_exhausted": False,
            "catalog_scanned": 0,
            # V3.7.x : observabilité [SOURCE][PAGE] et [SEARCH SUMMARY].
            "source_pages": {},
            "live_added": 0,
            "duplicates_total": 0,
            "received_total": 0,
        }
        entry = _search_cache[token]
        _clean_cache()
    _persist_search_session(entry, token, owner_key)
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


def _mark_progressive_skipped(token, skipped, owner):
    """Retire les sources en cooldown de la vague en cours (observabilité)."""
    if not skipped:
        return
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None or entry.get("cancelled"):
            return
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return
        blocked = set(skipped)
        pending = [name for name in entry.get("pending_sources") or [] if name not in blocked]
        entry["pending_sources"] = pending
        entry["pending"] = bool(pending)
        skipped_list = list(entry.get("skipped_sources") or [])
        for name in skipped:
            if name not in skipped_list:
                skipped_list.append(name)
        entry["skipped_sources"] = skipped_list


def _complete_progressive_source(token, source, additions, reference, strict, owner):
    """Fusion atomique d'une source progressive terminée."""
    result = None
    snapshot = None
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

        # Observabilité : reçus / nouveaux / doublons pour la ligne [SOURCE][PAGE].
        received = len(additions or [])
        new_count = max(0, len(results) - len(existing))
        duplicates = max(0, received - new_count)
        entry["received_total"] = int(entry.get("received_total", 0)) + received
        entry["duplicates_total"] = int(entry.get("duplicates_total", 0)) + duplicates
        entry["live_added"] = int(entry.get("live_added", 0)) + new_count
        source_pages = dict(entry.get("source_pages") or {})
        source_pages[source] = int(source_pages.get(source, 0)) + 1
        entry["source_pages"] = source_pages

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
        snapshot = dict(entry)
        result = (len(existing), len(results), bool(pending))
    if result is not None and snapshot is not None:
        _persist_search_session(snapshot, token, owner)
    return result


def _fail_progressive_source(token, source, owner):
    ended = False
    snapshot = None
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
        ended = not entry["pending"]
        snapshot = dict(entry)
    if snapshot is not None:
        _persist_search_session(snapshot, token, owner)
    if ended:
        _log_search_summary(token, source, 0.0)


def _log_search_summary(token, source, source_elapsed):
    """Ligne [SEARCH SUMMARY] enrichie : bilan complet de la recherche."""
    try:
        with _cache_lock:
            _clean_cache()
            entry = _search_cache.get(token)
            if entry is None or entry.get("cancelled"):
                return
            entry["ended_at"] = time.time()
            total_elapsed = max(0.0, entry["ended_at"] - float(entry.get("started_at") or entry["ended_at"]))
            snapshot = {
                "results": [dict(item) for item in entry.get("results") or []],
                "source_counts": dict(entry.get("source_counts") or {}),
                "completed_sources": list(entry.get("completed_sources") or []),
                "failed_sources": list(entry.get("failed_sources") or []),
                "search_query": str(entry.get("search_query") or ""),
                "index_initial": int(entry.get("index_hit_count") or 0),
                "live_added": int(entry.get("live_added") or 0),
                "received_total": int(entry.get("received_total") or 0),
                "duplicates_total": int(entry.get("duplicates_total") or 0),
                "started_at": float(entry.get("started_at") or 0.0),
            }
        results = snapshot["results"]
        identity_counts = {"fort": 0, "possible": 0, "rejet": 0}
        for item in results:
            level = str(item.get("niveau_identite") or "possible")
            if level in identity_counts:
                identity_counts[level] += 1
        sources = list(dict.fromkeys(list(snapshot["completed_sources"]) + [source]))
        failed = list(snapshot["failed_sources"])
        detail = ", ".join(f"{name} {count}" for name, count in snapshot["source_counts"].items()) or "aucune offre"
        identite = f"identité: {identity_counts['fort']} fort / {identity_counts['possible']} possible / {identity_counts['rejet']} rejet"
        intent_line = ""
        try:
            intent = _parse_intent(snapshot["search_query"])
            if intent is not None:
                intent_parts = {
                    key: value for key, value in intent.to_dict().items()
                    if key != "canonical" and value
                }
                if intent_parts:
                    intent_line = " | intent=" + str(intent_parts)
        except Exception:
            intent_line = ""
        print(
            f"[SEARCH SUMMARY] query={snapshot['search_query']!r}"
            f"{intent_line} | {len(results)} résultats | {detail} | "
            f"{len(sources)} source(s) + {len(failed)} échec(s) | "
            f"index_initial={snapshot['index_initial']} live_added={snapshot['live_added']} "
            f"final_total={len(results)} received={snapshot['received_total']} "
            f"duplicates={snapshot['duplicates_total']} rejected=0 | "
            f"{total_elapsed:.1f}s | {identite}"
        )
    except Exception:
        app.logger.exception("Échec de la ligne [SEARCH SUMMARY]")


def _finish_progressive_source(token, query, price, source, reference, strict, owner):
    """Termine UNE source en arrière-plan puis fusionne sans relancer les autres."""
    if not _progressive_task_allowed(token, source, owner):
        return

    started = perf_counter()
    network_elapsed = None
    try:
        # Mono-source : radar_engine exécute désormais directement le connecteur,
        # sans sous-executor impossible à annuler. Les deux connecteurs
        # Playwright partagent en plus un sémaphore afin de ne jamais lancer
        # deux Chromium simultanément sur le petit service Render.
        def _search_source():
            # V2.9.2 : ne pas analyser 100+ cartes par source au premier passage.
            # Le scroll infini demandera les pages suivantes au besoin.
            source_caps = {
                "Zalando": 30,
                "SSENSE": 120,
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
                network_started = perf_counter()
                additions = _search_source()
                network_elapsed = perf_counter() - network_started
        else:
            network_started = perf_counter()
            additions = _search_source()
            network_elapsed = perf_counter() - network_started
        if not _progressive_task_allowed(token, source, owner):
            return
        _index_results_async(additions, query)
        state = _complete_progressive_source(
            token, source, additions, reference, strict, owner
        )
        if state is None:
            return
        before, total, still_pending = state
        elapsed = perf_counter() - started
        with _cache_lock:
            cached = _search_cache.get(token)
            page = int((cached.get("source_pages") or {}).get(source, 1)) if cached else 1
            submitted_at = float((cached.get("submitted_at") or {}).get(source, started) or started) if cached else started
        queue_wait = max(0.0, started - submitted_at)
        received = len(additions or [])
        new_count = max(0, total - before)
        duplicates = max(0, received - new_count)
        source_health.record_outcome(
            source, received, new_count, network_elapsed, queue_wait
        )
        print(
            f"[PROGRESSIF][{source}][PAGE {page}] received={received} relevant={received} "
            f"new={new_count} duplicates={duplicates} rejected=0 "
            f"network={network_elapsed:.2f}s queue={queue_wait:.2f}s "
            f"duration={elapsed:.2f}s total={total}"
        )
        if not still_pending:
            _log_search_summary(token, source, elapsed)
    except Exception:
        app.logger.exception("Échec de la source progressive %s", source)
        source_health.record_exception(source)
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
        # Les anciennes offres indexées peuvent ne pas avoir encore le champ
        # ``niveau_identite``. Partout ailleurs dans le moteur, l'absence de ce
        # champ est traitée comme ``possible`` (jamais comme ``fort``). Garder
        # la même convention ici évite un état incohérent où /status annonce
        # des résultats mais /api/results renvoie une page vide.
        filtered = [item for item in filtered if str(item.get("niveau_identite") or "possible") in {"fort", "possible"}]
    elif identity == "strong":
        filtered = [item for item in filtered if str(item.get("niveau_identite") or "") == "fort"]
    elif identity == "unverified":
        filtered = [item for item in filtered if str(item.get("niveau_identite") or "") == "rejet"]
    identity_order = {"fort": 0, "possible": 1, "rejet": 2}
    keys = {
        # V3.2.0 : même en mode Explorer, une annonce douteuse ne doit jamais
        # passer devant une correspondance exacte simplement parce que sa source
        # a répondu plus vite.
        "relevance": lambda item: (
            identity_order.get(str(item.get("niveau_identite") or "possible"), 1),
            -_safe_number(item.get("score_identite"), 0),
            -_safe_number(item.get("score"), 0),
            item.get("_rank_index", 0),
        ),
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


def _index_spillover(entry, token_results, offset, limit, *, sort="relevance", marketplace="Toutes", price_min=None, price_max=None, price_exact=None, price_tolerance=None, exclude="", risk="all", identity="confirmed"):
    """Suite de la pagination depuis l'index persistant quand le token est vide.

    V3.8 : « tant que l'index contient de vraies offres, les pages continuent ».
    Seules des offres réellement indexées sont servies (aucune fabrication),
    dédupliquées contre les clés déjà affichées dans le token.
    """
    try:
        query = str(entry.get("search_query") or "").strip()
        if not query or not index_engine.index_enabled():
            return None
        # ``index_total`` est un snapshot pris au démarrage de la recherche.
        # Sur un cold start il peut valoir 0, puis l'index async reçoit de vraies
        # offres quelques secondes plus tard. Quand le token est encore vide, ne
        # jamais utiliser ce vieux 0 pour masquer ces offres fraîchement indexées.
        cached_index_total = int(entry.get("index_total") or 0)
        if token_results and cached_index_total <= len(token_results or []):
            return None
        index_offset = max(0, offset - len(token_results or []))
        indexed = index_engine.search(
            query,
            price_max=entry.get("search_price") or None,
            marketplace=marketplace,
            identity="all",
            risk=risk,
            sort=sort,
            limit=min(index_engine.query_limit(), SEARCH_RESULT_LIMIT),
            offset=index_offset,
        )
        if not indexed or not indexed.results:
            return None
        known = {index_engine._offer_key(item) for item in (token_results or [])}
        candidates = _sorted_results(
            indexed.results, sort=sort, marketplace=marketplace,
            price_min=price_min, price_max=price_max, price_exact=price_exact,
            price_tolerance=price_tolerance, exclude=exclude, risk=risk, identity=identity,
        )
        fresh = [item for item in candidates if index_engine._offer_key(item) not in known]
        if not fresh:
            return None
        page_items = [_public_result(item) for item in fresh[:limit]]
        overlap = sum(1 for item in (indexed.results or [])
                      if index_engine._offer_key(item) in known)
        remaining_index = max(0, indexed.total - index_offset - overlap)
        has_more = len(fresh) > len(page_items) or remaining_index > len(fresh)
        return {
            "results": page_items,
            "has_more": has_more,
            "total": len(token_results or []) + remaining_index,
        }
    except Exception:  # pragma: no cover - défensif, jamais sur le chemin de réponse
        return None


def _result_page(token, offset, limit=RESULT_BATCH_SIZE, sort="relevance", marketplace="Toutes", price_min=None, price_max=None, price_exact=None, price_tolerance=None, exclude="", risk="all", identity="confirmed", owner=None):
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), MAX_BATCH_SIZE))
    # Copier uniquement l'état nécessaire sous le verrou RAM. Le fallback index
    # peut toucher SQLite et ne doit jamais empêcher un worker progressif de
    # fusionner ses premiers résultats dans le token.
    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None:
            return None
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, str(owner or "")):
            return None
        entry_snapshot = {
            "search_query": entry.get("search_query"),
            "search_price": entry.get("search_price"),
            "index_total": entry.get("index_total"),
        }
        raw_results = [dict(item) for item in entry.get("results") or []]

    results = _sorted_results(raw_results, sort=sort, marketplace=marketplace, price_min=price_min, price_max=price_max, price_exact=price_exact, price_tolerance=price_tolerance, exclude=exclude, risk=risk, identity=identity)
    per_page = max(1, int(limit))
    page = [_public_result(item) for item in results[offset:offset + limit]]
    next_offset = offset + len(page)
    total = len(results)
    has_more = next_offset < total
    if not has_more:
        spilled = _index_spillover(
            entry_snapshot, results, offset, per_page, sort=sort, marketplace=marketplace,
            price_min=price_min, price_max=price_max, price_exact=price_exact,
            price_tolerance=price_tolerance, exclude=exclude, risk=risk, identity=identity,
        )
        if spilled is not None:
            page = spilled["results"]
            next_offset = offset + len(page)
            total = spilled["total"]
            has_more = spilled["has_more"]
    return {
        "results": page,
        "next_offset": next_offset,
        "has_more": has_more,
        "total": total,
        "page": (offset // per_page) + 1,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "per_page": per_page,
    }


@app.get("/api/results/<token>/status")
def result_status(token):
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        return jsonify({"error": "Identifiant de recherche invalide."}), 400
    _ensure_search_session(token, session.get("csrf_token"), _app_metadata()[1])
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
            "skipped_sources": list(entry.get("skipped_sources") or []),
            "index_mode": bool(entry.get("index_mode")),
            "index_hit_count": int(entry.get("index_hit_count", 0)),
            "index_total": int(entry.get("index_total", 0)),
            "index_age_seconds": entry.get("index_age_seconds"),
        })


_sources_health_cache = {"at": 0.0, "profile": None}


@app.get("/api/sources/health")
def sources_health():
    """Profil de capacités et état de santé des connecteurs actifs.

    Consommé par le panneau « Marchands trouvés » pour afficher la capacité
    réelle de chaque source (pagination, plafonds, pause) sans mensonge.
    """
    now = time.time()
    if _sources_health_cache["profile"] is not None and now - _sources_health_cache["at"] < 120:
        return jsonify({"sources": _sources_health_cache["profile"]})
    _, active_marketplaces = _app_metadata()
    profile = {}
    for name in sorted(active_marketplaces):
        try:
            connector = get_connector(name)
        except Exception:
            connector = None
        if connector is None:
            continue
        health = {}
        try:
            health = connector.health()
        except Exception:
            health = {"ok": False}
        profile[name] = {
            "supports_pagination": bool(getattr(connector, "supports_pagination", False)),
            "expansion_page_size": int(getattr(connector, "expansion_page_size", 50)),
            "expansion_recall_cap": int(getattr(connector, "expansion_recall_cap", 100)),
            "max_pages": int(getattr(connector, "max_pages", 0)),
            "empty_pages_threshold": int(getattr(connector, "empty_pages_threshold", 2)),
            "cooldown_seconds": float(getattr(connector, "cooldown_seconds", 0)),
            "health": health,
        }
    runtime_health = source_health.snapshot(active_marketplaces)
    for name in profile:
        runtime = runtime_health.get(name)
        if runtime:
            profile[name]["runtime"] = runtime
    profile["_diagnostics"] = {
        "env": current_environment(),
        "cooldown_count": source_health.cooldown_count(),
        "queue_p50_ms": source_health.queue_p50(),
        "queue_p95_ms": source_health.queue_p95(),
        "network_p50_ms": source_health.network_p50(),
        "network_p95_ms": source_health.network_p95(),
    }
    _sources_health_cache.update({"at": time.time(), "profile": profile})
    return jsonify({"sources": profile})


@app.post("/api/learn/event")
def learn_event():
    """Collecte anonyme d'événements d'interaction (analytics).

    Feature flag LUXE_RADAR_LEARN_ENABLED doit être actif.
    Sécurité : POST uniquement, CSRF via security_gate, body borné,
    types allowlistés, query_key dérivée côté serveur du search_token.
    """
    if not learn.LEARN_ENABLED:
        return jsonify({"ok": False}), 204

    raw = request.get_data(cache=False, as_text=True)
    if len(raw) > learn.LEARN_MAX_BODY_BYTES:
        return jsonify({"ok": False}), 413

    try:
        payload = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        return jsonify({"ok": False}), 400

    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        return jsonify({"ok": False}), 400
    raw_events = raw_events[: learn.LEARN_MAX_EVENTS_PER_POST]

    search_token = str(payload.get("token") or "")
    learn_sid = session.get("learn_sid")
    if not learn_sid:
        learn_sid = secrets.token_hex(16)
        session["learn_sid"] = learn_sid

    # Derive query_key server-side from search_token when available
    server_query_key = ""
    server_marketplace = ""
    if search_token and len(search_token) == 32:
        with _cache_lock:
            entry = _search_cache.get(search_token)
            if entry is not None:
                owner = str(entry.get("owner") or "")
                if not owner or secrets.compare_digest(owner, str(session.get("csrf_token") or "")):
                    raw_query = str(entry.get("search_query") or "")
                    if raw_query:
                        from index_engine import canonical_query as _cq
                        server_query_key = _cq(raw_query)
                    server_marketplace = str(entry.get("selected_platform") or "")

    pushed = 0
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        event_type = str(ev.get("type") or "").strip()
        if event_type not in learn._LEARN_EVENT_TYPES:
            continue

        # event_id : fourni par le frontend (crypto.randomUUID), stocké tel quel
        event_id = str(ev.get("eid") or "").strip()
        if not event_id or len(event_id) > learn.LEARN_MAX_EVENT_ID_LEN:
            continue

        # query_key : toujours dérivé côté serveur du token quand possible
        client_qk = str(ev.get("qk") or "").strip()[: learn.LEARN_MAX_QK_LEN]
        query_key = server_query_key if server_query_key else client_qk
        if not query_key:
            continue

        offer_key = str(ev.get("ok") or "").strip()[: learn.LEARN_MAX_OK_LEN]
        marketplace = str(ev.get("mp") or server_marketplace or "").strip()[: learn.LEARN_MAX_MP_LEN]
        meta = ev.get("m") if isinstance(ev.get("m"), dict) else {}

        learn.learn_push(
            event_id=event_id,
            session_id=learn_sid,
            event_type=event_type,
            query_key=query_key,
            offer_key=offer_key,
            marketplace=marketplace,
            meta=meta,
        )
        pushed += 1

    return jsonify({"ok": True, "accepted": pushed})


# Sources with a real page argument. retail_public connectors all implement
# search_page and remain fail-fast on 400/403/429/challenges.
EXPAND_PAGE_SOURCES = (
    "eBay", "Zalando", "Vinted",
    "i-Run", "Direct Running", "Alltricks", "Deporvillage",
    "Running Point", "Hardloop", "Ekosport", "Courir", "21RUN",
    "MisterRunning", "Spartoo", "Footshop", "JD Sports",
)

# These connectors do not expose a trustworthy page cursor today. We still
# widen their first public pass up to 100 real candidates when the user reaches
# the end of the catalogue. This is especially useful for SSENSE/ASOS and keeps
# Grailed honest: if its public feed is challenged, it returns 0 and exhausts.
EXPAND_RECALL_INITIAL_LIMITS = {
    "SSENSE": 120,
    "ASOS": 60,
    "AliExpress": 60,
    "DHgate": 50,
    "Cdiscount": 40,
    "67behaviour": 30,
    "1688": 30,
    "Grailed": 36,
}
EXPAND_RECALL_SOURCES = tuple(EXPAND_RECALL_INITIAL_LIMITS)
EXPAND_ALL_SOURCES = EXPAND_PAGE_SOURCES + EXPAND_RECALL_SOURCES

# Rotate through every existing marketplace before moving on. __catalog__ then
# discovers additional non-blocked public shops from sites.json in small waves.
EXPAND_WAVE_ORDER = (
    "eBay", "Vinted", "SSENSE", "ASOS", "Zalando", "Grailed",
    "Courir", "i-Run", "Direct Running", "21RUN", "Running Point",
    "MisterRunning", "Hardloop", "Spartoo", "Footshop", "JD Sports",
    "Alltricks", "Deporvillage", "Ekosport", "AliExpress", "DHgate",
    "Cdiscount", "67behaviour", "1688", "__catalog__",
)


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


def _expand_in_background(token, owner, target, query, price, page_state,
                          page_empty, recall_limit, recall_empty,
                          discovery_cursor, discovery_has_more, round_index):
    """Worker asynchrone : exécute le réseau + fusionne dans le token.

    Tourne dans ``_progressive_executor`` pour ne jamais bloquer un
    thread Gunicorn. Le flag ``expansion_inflight`` est posé par
    l'appelant et réinitialisé ici, même en cas d'erreur.
    """
    additions = []
    scanned = 0
    next_discovery_cursor = discovery_cursor
    next_discovery_has_more = discovery_has_more
    next_page = None
    next_recall_limit = None
    error = None
    try:
        if target == "__catalog__":
            raw_items, next_discovery_cursor, next_discovery_has_more, scanned = discover_catalog_wave(
                query=query,
                price_max=price,
                cursor=discovery_cursor,
                site_limit=8 if IS_RENDER_RUNTIME else 14,
                per_site_limit=16 if IS_RENDER_RUNTIME else 24,
            )
            additions = _analyse_discovery_items(raw_items, query, price)
        elif target in EXPAND_RECALL_SOURCES:
            current_limit = int(recall_limit.get(target, EXPAND_RECALL_INITIAL_LIMITS.get(target, 30)))
            next_recall_limit = min(100, max(current_limit + 25, current_limit * 2))
            additions = rechercher_multi_marketplaces(
                marque=query,
                prix_max=price,
                plateformes=[target],
                limite=min(next_recall_limit, SEARCH_RESULT_LIMIT),
                page=1,
            )
        else:
            next_page = int(page_state.get(target, 1)) + 1
            page_limits = {
                "eBay": 100, "Zalando": 60, "Vinted": 80,
                "i-Run": 100, "Direct Running": 100, "Alltricks": 100,
                "Deporvillage": 100, "Running Point": 100, "Hardloop": 100,
                "Ekosport": 100, "Courir": 100, "21RUN": 100,
                "MisterRunning": 100, "Spartoo": 100, "Footshop": 100,
                "JD Sports": 100,
            }
            additions = rechercher_multi_marketplaces(
                marque=query,
                prix_max=price,
                plateformes=[target],
                limite=min(page_limits.get(target, 100), SEARCH_RESULT_LIMIT),
                page=next_page,
            )
    except Exception as exc:
        error = str(exc)[:240]
        additions = []

    _index_results_async(additions, query)

    with _cache_lock:
        _clean_cache()
        entry = _search_cache.get(token)
        if entry is None or entry.get("cancelled"):
            return
        expected_owner = str(entry.get("owner") or "")
        if expected_owner and not secrets.compare_digest(expected_owner, owner):
            return

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
        elif target in EXPAND_RECALL_SOURCES:
            limits = dict(entry.get("recall_limit") or EXPAND_RECALL_INITIAL_LIMITS)
            empty = dict(entry.get("recall_empty") or {})
            limits[target] = int(next_recall_limit or limits.get(target, 100))
            if added == 0:
                empty[target] = int(empty.get(target, 0)) + 1
            else:
                empty[target] = 0
            if empty[target] >= 1 or limits[target] >= 100:
                exhausted.add(target)
            entry["recall_limit"] = limits
            entry["recall_empty"] = empty
            entry["page_exhausted"] = sorted(exhausted)
        else:
            state = dict(entry.get("page_state") or {})
            empty = dict(entry.get("page_empty") or {})
            state[target] = next_page or int(state.get(target, 1))
            if added == 0:
                empty[target] = int(empty.get(target, 0)) + 1
            else:
                empty[target] = 0
            connector = get_connector(target)
            empty_threshold = int(getattr(connector, "empty_pages_threshold", 2) or 2) if connector is not None else 2
            max_pages = int(getattr(connector, "max_pages", 0) or 0) if connector is not None else 0
            if empty[target] >= empty_threshold:
                exhausted.add(target)
            if max_pages > 0 and int(next_page or 1) >= max_pages:
                exhausted.add(target)
            if target == "Zalando" and IS_RENDER_RUNTIME and int(next_page or 1) >= 2:
                exhausted.add(target)
            entry["page_state"] = state
            entry["page_empty"] = empty
            entry["page_exhausted"] = sorted(exhausted)

        entry["expansion_inflight"] = False
        entry["expansion_exhausted"] = (
            all(source in exhausted for source in EXPAND_ALL_SOURCES)
            and not bool(entry.get("discovery_has_more", True))
        )
        persist_snapshot = dict(entry)
    _persist_search_session(persist_snapshot, token, owner)


def _expand_search_once(token, owner, marketplace="Toutes"):
    """Planifie une nouvelle vague asynchrone dans le catalogue d'une recherche.

    La requête réseau est soumise à ``_progressive_executor`` et ne bloque
    JAMAIS un thread Gunicorn. Le flag ``expansion_inflight`` empêche les
    appels multiples pendant l'exécution.
    """
    owner = str(owner or "")
    entry = _ensure_search_session(token, owner, _app_metadata()[1])
    if entry is None:
        return None, 404
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
        existing_count = len(entry.get("results") or [])

        requested = str(marketplace or "Toutes")
        if requested == "Toutes" and initial_pipeline_pending and existing_count == 0:
            return {
                "accepted": True, "busy": True, "added": 0,
                "exhausted": False, "source": "sources en cours",
                "retry_after_ms": 1500,
            }, 202
        target = None
        if requested != "Toutes":
            if requested in EXPAND_ALL_SOURCES and requested not in exhausted:
                target = requested
            else:
                return {"accepted": False, "busy": False, "added": 0, "exhausted": True, "source": requested}, 200
        else:
            wave_order = ("eBay",) if initial_pipeline_pending else EXPAND_WAVE_ORDER
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
        page_state = dict(entry.get("page_state") or {})
        page_empty = dict(entry.get("page_empty") or {})
        recall_limit = dict(entry.get("recall_limit") or EXPAND_RECALL_INITIAL_LIMITS)
        recall_empty = dict(entry.get("recall_empty") or {})
        discovery_cursor = int(entry.get("discovery_cursor", 0))

    _progressive_executor.submit(
        _expand_in_background,
        token, owner, target, query, price,
        page_state, page_empty, recall_limit, recall_empty,
        discovery_cursor, discovery_has_more, round_index,
    )
    return {
        "accepted": True, "busy": True, "added": 0,
        "exhausted": False,
        "source": "catalogue" if target == "__catalog__" else target,
        "retry_after_ms": 2000,
    }, 202


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


def _trigger_collector_wave(token, offset):
    """Vague de fond quand l'utilisateur approche de la fin d'un token.

    Ne fait rien si le collecteur est désactivé, si la requête est vide ou si
    une collecte du même seed est récente/déjà en file (dédupe interne).
    Best-effort hors hot path : un échec n'affecte jamais la réponse.
    """
    try:
        if not collector.COLLECTOR_ENABLED:
            return
        with _cache_lock:
            entry = _search_cache.get(token)
            if entry is None:
                return
            query = str(entry.get("search_query") or "").strip()
            results = entry.get("results") or []
            total = len(results)
            if not query or total == 0:
                return
            near_end = offset >= total - RESULT_BATCH_SIZE
        if not near_end:
            return
        _collector.enqueue(query, entry.get("search_price"))
    except Exception:  # pragma: no cover - jamais sur le chemin de réponse
        pass


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
    _ensure_search_session(token, session.get("csrf_token"), _app_metadata()[1])
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
    identity = request.args.get("identity", "all")
    if identity not in {"all", "confirmed", "strong", "unverified"}:
        identity = "all"
    page = _result_page(token, offset, limit=parsed_limit, sort=sort, marketplace=marketplace, price_min=price_min, price_max=price_max, price_exact=price_exact, price_tolerance=price_tolerance, exclude=exclude, risk=risk, identity=identity, owner=session.get("csrf_token"))
    if page is None:
        return jsonify({"error": "Recherche expirée. Relance le radar."}), 404
    _trigger_collector_wave(token, offset)
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
    _ensure_search_session(token, session.get("csrf_token"), _app_metadata()[1])
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


def _search_signature(recherche, prix_saisi, selected_platform, reference_saisie, reference_stricte):
    """Empreinte stable des entrées de recherche pour réutiliser le token de
    session (évite de re-frapper les marketplaces quand on change de page)."""
    payload = "|".join([
        str(recherche or "").strip(),
        str(prix_saisi or "").strip(),
        str(selected_platform or "Toutes"),
        str(reference_saisie or "").strip(),
        "1" if reference_stricte else "0",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_radar_search(recherche, prix_saisi, selected_platform, reference_saisie, reference_stricte, initial_lots, active_marketplaces, universe=""):
    """Exécute une recherche radar réelle (index instantané + workers progressifs).

    Réutilisé par le POST / (formulaire) et le GET /search (URL paginée).
    Renvoie un dict avec tout l'état nécessaire au rendu serveur.
    """
    recherche = str(recherche or "").strip()[:120]
    prix_saisi = str(prix_saisi or "").strip()[:30]
    selected_platform = str(selected_platform or "Toutes").strip()[:80]
    reference_saisie = str(reference_saisie or "").strip()[:60]
    reference_stricte = bool(reference_stricte)
    universe = str(universe or "").strip().lower()[:30]
    if universe not in {"luxury"}:
        universe = ""
    universe_active = bool(universe)

    try:
        if initial_lots:
            initial_lots = max(1, min(int(initial_lots), MAX_BATCH_SIZE))
        else:
            initial_lots = 0
    except (TypeError, ValueError):
        initial_lots = 0
    if not initial_lots:
        initial_lots = MOBILE_INITIAL_RESULTS if _is_mobile_request() else INITIAL_RESULTS

    state = {
        "recherche": recherche,
        "prix_saisi": prix_saisi,
        "selected_platform": selected_platform,
        "reference_saisie": reference_saisie,
        "reference_stricte": reference_stricte,
        "initial_lots": initial_lots,
        "universe_active": universe_active,
        "erreur": None,
        "search_token": None,
        "total_results": 0,
        "search_pending": False,
        "search_generation": 0,
        "interpreted_query": None,
        "index_mode": False,
        "index_hit_count": 0,
        "index_age_seconds": None,
        "annonces": [],
    }

    understood = understand_query(recherche) if recherche else None
    state["interpreted_query"] = understood.canonical if understood and understood.corrected else None

    allowed_platforms = {"Toutes", *active_marketplaces}
    if state["selected_platform"] not in allowed_platforms:
        state["erreur"] = _message("invalid_marketplace")
        state["selected_platform"] = "Toutes"

    normalized_reference = _normalized_reference(reference_saisie)
    state["reference_stricte"] = reference_stricte and bool(normalized_reference)
    if reference_saisie and len(normalized_reference) < 3:
        state["erreur"] = _message("invalid_reference")

    def _submit_progressive(search_token, connector_query, prix):
        owner = session.get("csrf_token")
        state["search_pending"] = bool(progressive_sources)
        print(
            f"[{'UNIVERSE' if universe_active else 'INDEX' if state['index_mode'] else 'COLD'}] "
            f"rendu immédiat: {state['index_hit_count']}/{indexed.total} offres | "
            f"rafraîchissement: {', '.join(progressive_sources)}"
        )
        # Sources en cooldown/blocage pour l'environnement courant : on ne les
        # re-planifie pas, aucun worker n'est consommé ni réseau répété.
        skipped = source_health.skipped_sources(active_marketplaces)
        if skipped:
            _mark_progressive_skipped(search_token, skipped, owner)
            print(
                f"[PROGRESSIF][SKIP] cooldown environnement "
                f"({current_environment()}) -> {', '.join(skipped)}"
            )
        for source in progressive_sources:
            _progressive_executor.submit(
                _finish_progressive_source,
                search_token, connector_query, prix, source,
                "" if universe_active else reference_saisie,
                False if universe_active else state["reference_stricte"],
                owner,
            )
        if progressive_sources:
            try:
                with _cache_lock:
                    cached = _search_cache.get(search_token)
                    if cached is not None:
                        stamped = dict(cached.get("submitted_at") or {})
                        for source in progressive_sources:
                            stamped[source] = perf_counter()
                        cached["submitted_at"] = stamped
            except Exception:
                pass

    def _finalize_first_page():
        first_page = (
            _result_page(state["search_token"], 0, state["initial_lots"], identity="all", owner=session.get("csrf_token"))
            if state["search_token"] else None
        )
        total_n = first_page["total"] if first_page else 0
        state["annonces"] = first_page["results"] if first_page else []
        state["total_results"] = total_n
        state["page"] = 1
        state["total_pages"] = max(1, (total_n + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)

    if recherche:
        try:
            prix = float(prix_saisi) if prix_saisi else 1_000_000.0
            if not (0 < prix <= 1_000_000):
                state["erreur"] = _message("price_range")
            elif state["erreur"] is None:
                plateformes = None if state["selected_platform"] == "Toutes" else [state["selected_platform"]]
                base_connector_query = _canonicalize_search_query(recherche)
                connector_query = (
                    base_connector_query
                    if not normalized_reference or normalized_reference in _normalized_reference(base_connector_query)
                    else f"{base_connector_query} {reference_saisie}"
                )
                if universe_active:
                    indexed = index_engine.search_universe(
                        _LUXURY_FASHION_BRANDS, connector_query,
                        price_max=(prix if prix_saisi else None),
                        marketplace=state["selected_platform"],
                        identity="confirmed",
                        limit=min(index_engine.query_limit(), SEARCH_RESULT_LIMIT),
                    ) if index_engine.index_enabled() else index_engine.IndexSearch([], 0, None, "")
                else:
                    indexed = index_engine.search(
                        connector_query,
                        price_max=(prix if prix_saisi else None),
                        marketplace=state["selected_platform"],
                        identity="all",
                        limit=min(index_engine.query_limit(), SEARCH_RESULT_LIMIT),
                    ) if index_engine.index_enabled() else index_engine.IndexSearch([], 0, None, "")
                indexed_results = _rank_by_reference(indexed.results, reference_saisie, state["reference_stricte"])
                indexed_confirmed = _sorted_results(indexed_results, identity="confirmed")
                state["index_hit_count"] = len(indexed_confirmed)
                state["index_age_seconds"] = indexed.age_seconds
                state["index_mode"] = state["index_hit_count"] > 0

                if plateformes is None:
                    progressive_sources = _progressive_source_order(connector_query, active_marketplaces)
                else:
                    progressive_sources = [name for name in plateformes if name in active_marketplaces]

                state["search_token"] = _cache_results(
                    indexed_results, session.get("csrf_token"),
                    pending_sources=progressive_sources,
                    completed_sources=[],
                    search_query=connector_query, search_price=prix,
                    index_mode=state["index_mode"], index_hit_count=state["index_hit_count"],
                    index_total=indexed.total, index_age_seconds=state["index_age_seconds"],
                    selected_platform=state["selected_platform"],
                    reference=reference_saisie,
                    reference_stricte=state["reference_stricte"],
                    universe=universe,
                )
                _submit_progressive(state["search_token"], connector_query, prix)
                _finalize_first_page()
        except ValueError:
            state["erreur"] = _message("invalid_price")
        except Exception:
            app.logger.exception("Échec contrôlé d'une recherche radar")
            state["erreur"] = _message("search_error")
    elif universe == "luxury":
        try:
            prix = float(prix_saisi) if prix_saisi else 1_000_000.0
            if not (0 < prix <= 1_000_000):
                state["erreur"] = _message("price_range")
            elif state["erreur"] is None:
                plateformes = None if state["selected_platform"] == "Toutes" else [state["selected_platform"]]
                connector_query = _rotating_luxury_query()
                indexed = index_engine.search_universe(
                    _LUXURY_FASHION_BRANDS, connector_query,
                    price_max=(prix if prix_saisi else None),
                    marketplace=state["selected_platform"],
                    identity="confirmed",
                    limit=min(index_engine.query_limit(), SEARCH_RESULT_LIMIT),
                ) if index_engine.index_enabled() else index_engine.IndexSearch([], 0, None, "")
                indexed_results = list(indexed.results)
                indexed_confirmed = _sorted_results(indexed_results, identity="confirmed")
                state["index_hit_count"] = len(indexed_confirmed)
                state["index_age_seconds"] = indexed.age_seconds
                state["index_mode"] = state["index_hit_count"] > 0

                if plateformes is None:
                    progressive_sources = _progressive_source_order(connector_query, active_marketplaces)
                else:
                    progressive_sources = [name for name in plateformes if name in active_marketplaces]

                state["search_token"] = _cache_results(
                    indexed_results, session.get("csrf_token"),
                    pending_sources=progressive_sources,
                    completed_sources=[],
                    search_query=connector_query, search_price=prix,
                    index_mode=state["index_mode"], index_hit_count=state["index_hit_count"],
                    index_total=indexed.total, index_age_seconds=state["index_age_seconds"],
                    selected_platform=state["selected_platform"],
                    reference=reference_saisie,
                    reference_stricte=state["reference_stricte"],
                    universe=universe,
                )
                _submit_progressive(state["search_token"], connector_query, prix)
                _finalize_first_page()
        except ValueError:
            state["erreur"] = _message("invalid_price")
        except Exception:
            app.logger.exception("Échec contrôlé du clic univers Luxe")
            state["erreur"] = _message("search_error")
    else:
        state["erreur"] = _message("missing_fields")
    return state


@app.route("/", methods=["GET", "POST"])
def accueil():
    catalog_sites, active_marketplaces = _app_metadata()
    state = {
        "annonces": [],
        "recherche": None,
        "erreur": None,
        "search_token": None,
        "total_results": 0,
        "selected_platform": "Toutes",
        "prix_saisi": "",
        "prefill_query": "",
        "reference_saisie": "",
        "reference_stricte": False,
        "initial_lots": MOBILE_INITIAL_RESULTS if _is_mobile_request() else INITIAL_RESULTS,
        "search_pending": False,
        "search_generation": 0,
        "interpreted_query": None,
        "index_mode": False,
        "index_hit_count": 0,
        "index_age_seconds": None,
        "universe_active": False,
        "page": 1,
        "total_pages": 1,
    }

    if request.method == "GET":
        state["prefill_query"] = request.args.get("q", "").strip()[:120]
        state["prix_saisi"] = request.args.get("price", "").strip()[:30]
        requested_platform = request.args.get("marketplace", "Toutes").strip()[:80]
        state["selected_platform"] = requested_platform if requested_platform in {"Toutes", *active_marketplaces} else "Toutes"
        state["reference_saisie"] = request.args.get("ref", "").strip()[:60]
        state["reference_stricte"] = request.args.get("strict") == "1" and bool(_normalized_reference(state["reference_saisie"]))

    elif request.method == "POST":
        recherche = request.form.get("marque", "").strip()[:120]
        universe = request.form.get("universe", "").strip().lower()[:30]
        prix_saisi = request.form.get("prix", "").strip()[:30]
        selected_platform = request.form.get("plateforme", "Toutes").strip()[:80]
        reference_saisie = request.form.get("reference_exacte", "").strip()[:60]
        reference_stricte = request.form.get("reference_stricte") == "1"
        lots_raw = request.form.get("lots", "").strip()
        default_initial_lots = MOBILE_INITIAL_RESULTS if _is_mobile_request() else INITIAL_RESULTS
        try:
            initial_lots = max(1, min(int(lots_raw), MAX_BATCH_SIZE)) if lots_raw else default_initial_lots
        except (TypeError, ValueError):
            initial_lots = default_initial_lots
        state.update(_run_radar_search(
            recherche, prix_saisi, selected_platform, reference_saisie,
            reference_stricte, initial_lots, active_marketplaces, universe=universe,
        ))
        if state["search_token"] and not state["erreur"]:
            session["lr_search_token"] = state["search_token"]
            session["lr_search_signature"] = _search_signature(
                recherche, prix_saisi, state["selected_platform"], reference_saisie, reference_stricte
            )

    return _render_search_page(state, catalog_sites, active_marketplaces)


def _render_search_page(state, catalog_sites, active_marketplaces):
    return render_template(
        "index.html",
        marques=MARQUES,
        marketplaces=list(active_marketplaces),
        catalog_site_count=len(catalog_sites),
        annonces=state["annonces"],
        recherche=state["recherche"],
        prefill_query=state["prefill_query"],
        erreur=state["erreur"],
        prix_saisi=state["prix_saisi"],
        selected_platform=state["selected_platform"],
        reference_saisie=state["reference_saisie"],
        reference_stricte=state["reference_stricte"],
        search_token=state["search_token"],
        total_results=state["total_results"],
        initial_results=state["initial_lots"],
        search_pending=state["search_pending"],
        search_generation=state["search_generation"],
        interpreted_query=state["interpreted_query"],
        index_mode=state["index_mode"],
        index_hit_count=state["index_hit_count"],
        index_age_seconds=state["index_age_seconds"],
        universe_active=state["universe_active"],
        page=state.get("page", 1),
        total_pages=state.get("total_pages", 1),
        page_size=SEARCH_PAGE_SIZE,
        app_version=APP_VERSION,
        asset_version=ASSET_VERSION,
        mobile_request=_is_mobile_request(),
        subscription_plans=SUBSCRIPTION_PLANS,
        billing_ready=_billing_ready(),
        csrf_token=session["csrf_token"],
        csp_nonce=g.csp_nonce,
        learn_enabled=learn.LEARN_ENABLED,
    )


@app.get("/search")
def search_share_page():
    """Rendu serveur d'une page de résultats partageable.

    URL : /search?q=&price=&marketplace=&ref=&strict=&page=N
    Quand la recherche correspond à celle de la session, le token est réutilisé
    (aucune nouvelle frappe des marketplaces). Sinon une recherche réelle est
    relancée puis la page demandée est affichée.
    """
    catalog_sites, active_marketplaces = _app_metadata()
    recherche = request.args.get("q", "").strip()[:120]
    prix_saisi = request.args.get("price", "").strip()[:30]
    requested_platform = request.args.get("marketplace", "Toutes").strip()[:80]
    selected_platform = requested_platform if requested_platform in {"Toutes", *active_marketplaces} else "Toutes"
    reference_saisie = request.args.get("ref", "").strip()[:60]
    reference_stricte = request.args.get("strict") == "1" and bool(_normalized_reference(reference_saisie))
    try:
        page = max(1, min(int(request.args.get("page", "1")), 500))
    except (TypeError, ValueError):
        page = 1
    offset = (page - 1) * SEARCH_PAGE_SIZE

    state = {
        "annonces": [],
        "recherche": recherche or None,
        "erreur": None,
        "search_token": None,
        "total_results": 0,
        "selected_platform": selected_platform,
        "prix_saisi": prix_saisi,
        "prefill_query": recherche,
        "reference_saisie": reference_saisie,
        "reference_stricte": reference_stricte,
        "initial_lots": SEARCH_PAGE_SIZE,
        "search_pending": False,
        "search_generation": 0,
        "interpreted_query": None,
        "index_mode": False,
        "index_hit_count": 0,
        "index_age_seconds": None,
        "universe_active": False,
        "page": page,
        "total_pages": 1,
    }

    if not recherche:
        state["erreur"] = _message("missing_fields")
        return _render_search_page(state, catalog_sites, active_marketplaces)

    signature = _search_signature(recherche, prix_saisi, selected_platform, reference_saisie, reference_stricte)
    owner = session.get("csrf_token")
    cached_token = None
    cached_entry = None
    if session.get("lr_search_signature") == signature and session.get("lr_search_token"):
        restored_entry = _ensure_search_session(
            str(session.get("lr_search_token")), owner, active_marketplaces
        )
        if restored_entry is not None:
            cached_token = str(session.get("lr_search_token"))
            cached_entry = restored_entry

    if cached_token and cached_entry is not None:
        first_page = _result_page(cached_token, offset, limit=SEARCH_PAGE_SIZE, identity="confirmed", owner=owner)
        state["search_token"] = cached_token
        state["search_pending"] = bool(cached_entry.get("pending"))
        state["search_generation"] = int(cached_entry.get("generation", 0))
        state["index_mode"] = bool(cached_entry.get("index_mode"))
        state["index_hit_count"] = int(cached_entry.get("index_hit_count", 0))
        state["index_age_seconds"] = cached_entry.get("index_age_seconds")
    else:
        initial = _run_radar_search(
            recherche, prix_saisi, selected_platform, reference_saisie,
            reference_stricte, SEARCH_PAGE_SIZE, active_marketplaces,
        )
        state.update({key: value for key, value in initial.items() if key in state})
        if state["search_token"] and not state["erreur"]:
            session["lr_search_token"] = state["search_token"]
            session["lr_search_signature"] = signature
        first_page = (
            _result_page(state["search_token"], offset, limit=SEARCH_PAGE_SIZE, identity="confirmed", owner=owner)
            if state["search_token"] else None
        )

    if first_page:
        if state["page"] > first_page["total_pages"] and not state.get("search_pending"):
            state["page"] = first_page["total_pages"]
            first_page = _result_page(
                state["search_token"], (state["page"] - 1) * SEARCH_PAGE_SIZE,
                limit=SEARCH_PAGE_SIZE, identity="confirmed", owner=owner,
            )
        state["annonces"] = first_page["results"]
        state["total_results"] = first_page["total"]
        state["initial_lots"] = (state["page"] - 1) * SEARCH_PAGE_SIZE + len(first_page["results"])
        state["page"] = first_page["page"]
        state["total_pages"] = first_page["total_pages"]
    else:
        state["annonces"] = []
    return _render_search_page(state, catalog_sites, active_marketplaces)


if __name__ == "__main__":
    _start_background_workers()
    app.run(debug=False, use_reloader=False, host="127.0.0.1", port=5000)
