from pathlib import Path
from collections import Counter
from io import BytesIO
import json
import os
import re
import subprocess
import sys

from PIL import Image

from app_web import _app_metadata, _billing_ready, _public_result, _rate_allowed, _rate_buckets, app, invalidate_app_metadata
from luxe_radar_manager import _assert_public_http_url, iter_backup_files


ROOT = Path(__file__).resolve().parent


def _relative_luminance(hex_color):
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first, second):
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def main():
    client = app.test_client()
    root = client.get("/")
    html = root.get_data(as_text=True)

    headers = root.headers
    assert root.status_code == 200
    api_missing = client.get("/api/does-not-exist")
    page_missing = client.get("/does-not-exist")
    assert api_missing.status_code == 404 and api_missing.is_json and api_missing.get_json()["error"] == "Ressource introuvable."
    assert page_missing.status_code == 404 and page_missing.mimetype == "text/plain"
    assert client.post("/api/health").status_code == 403
    assert len(root.data) < 60_000
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Permitted-Cross-Domain-Policies"] == "none"
    assert headers["X-Download-Options"] == "noopen"
    assert headers["Origin-Agent-Cluster"] == "?1"
    assert headers["X-DNS-Prefetch-Control"] == "off"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "media-src 'self'" in headers["Content-Security-Policy"]
    assert "worker-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    assert headers.get("X-Request-ID")
    assert "app;dur=" in headers.get("Server-Timing", "")
    versioned_css = client.get("/static/app.css?v=20260814-38")
    assert versioned_css.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert client.get("/static/app-icon-192.png").headers["Cache-Control"] == "public, max-age=3600"
    assert client.get("/static/app-icon-192.png?v=20260814-2").headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert client.get("/sw.js").headers["Cache-Control"] == "no-cache"
    robots = client.get("/robots.txt")
    sitemap = client.get("/sitemap.xml")
    assert robots.status_code == 200 and "Disallow: /api/" in robots.get_data(as_text=True)
    assert "Sitemap: http://localhost/sitemap.xml" in robots.get_data(as_text=True)
    assert sitemap.status_code == 200 and "<loc>http://localhost/</loc>" in sitemap.get_data(as_text=True)
    assert "<loc>http://localhost/confiance</loc>" in sitemap.get_data(as_text=True)
    trust_fr = client.get("/confiance")
    trust_en = client.get("/confiance?lang=en")
    trust_css = client.get("/static/trust.css?v=20260814-3")
    assert trust_fr.status_code == 200 and "Clair sur ce qui fonctionne" in trust_fr.get_data(as_text=True)
    assert trust_en.status_code == 200 and "Clear about what works" in trust_en.get_data(as_text=True)
    trust_fr_text = re.sub(r"<[^>]+>", "", trust_fr.get_data(as_text=True))
    assert "7 connecteurs test\u00e9s" in trust_fr_text
    trust_en_text = re.sub(r"<[^>]+>", "", trust_en.get_data(as_text=True))
    assert "1218 catalogued sites" in trust_en_text
    assert "webhook confirmation" in trust_en.get_data(as_text=True)
    assert trust_css.status_code == 200 and len(trust_css.data) < 10_000
    assert trust_css.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert trust_fr.headers["X-Frame-Options"] == "DENY"
    assert robots.headers["Cache-Control"] == "public, max-age=3600"

    with client.session_transaction() as browser_session:
        csrf = browser_session["csrf_token"]
    assert csrf and f'name="csrf_token" value="{csrf}"' in html
    wrong_method = client.post("/api/health", headers={"X-CSRF-Token": csrf})
    assert wrong_method.status_code == 405 and wrong_method.is_json
    assert app.config["SESSION_COOKIE_NAME"] == "luxe_radar_session"
    assert client.post("/", data={"marque": "Nike", "prix": "50"}).status_code == 403
    english_error = client.post("/", data={
        "csrf_token": csrf, "language": "en", "marque": "", "prix": "", "plateforme": "Toutes",
    }).get_data(as_text=True)
    assert "Enter a product and a maximum price." in english_error
    xss_payload = '<img src=x onerror=alert(1)>'
    escaped_search = client.post("/", data={
        "csrf_token": csrf, "language": "fr", "marque": xss_payload, "prix": "", "plateforme": "Toutes",
    }).get_data(as_text=True)
    assert xss_payload not in escaped_search and "&lt;img src=x onerror=alert(1)&gt;" in escaped_search
    assert client.get("/", headers={"Host": "attacker.invalid"}).status_code == 400
    assert client.get("/", headers={"Host": "localhost.:5000"}).status_code == 200

    oversized_client = app.test_client()
    oversized_client.get("/", environ_overrides={"REMOTE_ADDR": "198.51.100.88"})
    with oversized_client.session_transaction() as oversized_session:
        oversized_csrf = oversized_session["csrf_token"]
    oversized = oversized_client.post(
        "/",
        data={"csrf_token": oversized_csrf, "marque": "Nike", "prix": "50", "image": (BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "large.png")},
        environ_overrides={"REMOTE_ADDR": "198.51.100.88"},
    )
    assert oversized.status_code == 413 and oversized.headers["Cache-Control"] == "no-store"

    invalid_image = client.post("/api/results/" + "0" * 32 + "/image-rank", data={"image": (BytesIO(b"not-an-image"), "bad.png")}, headers={"X-CSRF-Token": csrf})
    assert invalid_image.status_code == 400 and "image" in invalid_image.get_json()["error"].lower()

    rate_client = app.test_client()
    rate_client.get("/", environ_overrides={"REMOTE_ADDR": "203.0.113.77"})
    with rate_client.session_transaction() as rate_session:
        rate_csrf = rate_session["csrf_token"]
    for _ in range(12):
        allowed = rate_client.post(
            "/",
            data={"csrf_token": rate_csrf, "marque": "", "prix": ""},
            environ_overrides={"REMOTE_ADDR": "203.0.113.77"},
        )
        assert allowed.status_code == 200
    limited = rate_client.post(
        "/",
        data={"csrf_token": rate_csrf, "marque": "", "prix": ""},
        environ_overrides={"REMOTE_ADDR": "203.0.113.77"},
    )
    assert limited.status_code == 429 and limited.headers["Cache-Control"] == "no-store"
    assert limited.headers["Retry-After"] == "60"

    now = __import__("time").monotonic()
    _rate_buckets.clear()
    for index in range(1100):
        _rate_buckets[(f"stale-{index}", "global")].append(now - 120)
    assert _rate_allowed(("fresh", "global"), 1)
    assert list(_rate_buckets) == [("fresh", "global")]
    _rate_buckets.clear()
    os.environ.pop("STRIPE_SECRET_KEY", None)
    assert _billing_ready() is False
    checkout = client.post("/api/billing/checkout", json={"plan": "pro", "cycle": "monthly"}, headers={"X-CSRF-Token": csrf})
    assert checkout.status_code == 503 and checkout.get_json()["code"] == "billing_not_configured"

    unsafe = _public_result({
        "lien": "javascript:alert(1)",
        "url": "file:///etc/passwd",
        "image": "data:text/html,boom",
        "titre": "A" * 700,
        "raw_secret": "must-not-leak",
        "score": float("nan"),
    })
    assert unsafe == {"lien": "", "url": "", "image": "", "titre": "A" * 500}

    assert 'id="language-toggle"' in html
    assert "connecteurs configurés" in html and "connecteurs opérationnels" not in html
    assert "1218 sites au catalogue" in html
    assert "data-reseller-nav" in html
    assert 'class="skip-link"' in html
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert "catalog_sites" not in template
    identifiers = re.findall(r'\bid="([^"]+)"', template)
    duplicates = sorted(name for name, count in Counter(identifiers).items() if count > 1)
    assert not duplicates, f"Identifiants HTML dupliqués : {duplicates}"
    nav_views = set(re.findall(r'data-view="([^"]+)"', template))
    content_views = set(re.findall(r'id="view-([^"]+)"', template))
    assert nav_views == content_views
    assert 'aria-live="polite"' in template
    assert 'viewport-fit=cover' in html
    assert 'rel="apple-touch-icon"' in html and 'apple-mobile-web-app-capable' in html
    assert 'property="og:image"' in html and 'name="twitter:card" content="summary_large_image"' in html
    assert Image.open(ROOT / "static" / "social-card.png").size == (1200, 630)
    assert 'id="app-announcer" role="status" aria-live="polite"' in template
    assert 'type="application/ld+json" nonce="{{ csp_nonce }}"' in template
    assert 'rel="canonical"' in template and 'property="og:url"' in template
    assert template.count('role="dialog"') >= 3
    assert template.count('aria-modal="true"') >= 3
    assert "onclick=" not in template

    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    assert len(script.encode("utf-8")) < 100_000
    assert len(styles.encode("utf-8")) < 50_000
    assert _contrast("8d96a8", "101319") >= 4.5
    assert _contrast("667085", "ffffff") >= 4.5
    assert _contrast("a8ff3e", "07090d") >= 7
    worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "luxe-radar-shell-v47" in worker and "app.js?v=20260814-42" in worker
    assert "app.js?v=20260814-42" in html and "app.css?v=20260814-38" in html
    assert "localizeActivityText" in script and "'Favoris':'Favorites'" in script
    assert 'id="catalog-filter-form"' in template and 'id="catalog-list"' in template
    assert "function loadCatalog" in script and ".catalog-explorer" in styles
    assert 'id="favorite-query"' in template and 'id="favorite-sort"' in template
    assert "function renderFavorites" in script and ".favorites-tools" in styles
    assert "function trackerRowNode" in script and ".price-sparkline" in styles
    assert "function comparisonCardNode" in script and ".comparison-metrics" in styles
    assert 'id="image-search-file"' in template and "/image-rank" in script and 'id="image-search-btn"' in template
    assert "listingUrl==='#'" in script and ".product-link.disabled" in styles
    assert "media.replaceChildren(none)" in script and "Image indisponible" in script
    assert "metricParts.join(' · ')" in script and "Données partielles" in script
    assert "function resetSearchButton" in script and "aria-busy" in script
    assert ".primary-btn.searching" in styles and "@keyframes search-spin" in styles
    assert 'id="manual-load-more"' in template and "function syncLoadControls" in script
    assert "loadFailed?tr('Réessayer le chargement'" in script
    assert "entries[0].isIntersecting&&settings.autoLoad" in script
    assert "loadSwitch?.setAttribute('aria-pressed'" in script
    assert 'id="reference-exacte"' in template and 'id="reference-stricte"' in template
    assert "strictReference" in script and ".reference-options" in styles
    assert "submit.textContent='Analyse" not in script
    assert "Meilleur total" in script and "Meilleure confiance" in script
    assert "Créer depuis les favoris" in template and "Ajoute au moins un favori" in script
    assert "Une collection porte déjà ce nom." in script and "favorites.slice(0,200)" in script
    assert "Article ou prix invalide." in script and "trackers.slice(0,200)" in script
    assert "Données d’inventaire invalides." in script and "inventory.slice(0,1000)" in script
    assert 'id="inventory-query"' in template and 'id="inventory-filter"' in template
    assert "visibleInventory" in script and ".inventory-tools" in styles
    assert "function matchesAlert" in script and ".alert-match.has-match" in styles
    assert "Cette alerte existe déjà." in script and "price>1000000" in script
    assert "window.history.pushState" in script and "addEventListener('popstate'" in script
    assert ".billing-toggle button{min-height:44px}" in styles
    assert ".pricing-detail summary{display:flex;align-items:center;min-height:44px}" in styles
    assert ".icon-btn,.favorite-btn,.modal-close{min-width:44px;min-height:44px}" in styles
    assert ".product-link,.compare-action,.track-action{min-height:44px}" in styles
    assert "height:calc(70px + env(safe-area-inset-bottom))" in styles
    assert "scroll-snap-type:x proximity" in styles
    manifest = (ROOT / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
    manifest_data = json.loads(manifest)
    assert manifest_data["display"] == "standalone" and manifest_data["id"] == "/" and len(manifest_data["icons"]) == 3
    assert "self.skipWaiting()" in worker and "self.clients.claim()" in worker
    assert "app-icon-192.png?v=20260814-2" in manifest and "app-icon-512.png?v=20260814-2" in manifest
    assert Image.open(ROOT / "static" / "app-icon-192.png").size == (192, 192)
    assert Image.open(ROOT / "static" / "app-icon-512.png").size == (512, 512)
    assert 'id="app-toast" role="status"' in template and "function toast(text)" in script
    assert "alert(" not in script
    assert "const portableLimits=" in script and "function sanitiseLocalValue" in script
    assert "['__proto__','prototype','constructor']" in script and "value.slice(0,2000)" in script
    assert "experienceMode" in script and "mode-reseller" in script
    assert "prefers-color-scheme: light" in script and "['dark','light','system']" in script
    assert "pageTitles" in script and "textEn" in script
    assert "aria-pressed" in script and "referrerPolicy='no-referrer'" in script
    assert 'referrerpolicy="no-referrer"' in template and 'decoding="async"' in template
    assert "initialiseDialogs" in script and "MutationObserver" in script
    assert "portableKeys" in script and "luxe-radar-backup" in script
    assert "'discover','studio'" in script
    assert 'id="view-discover"' in html and html.count("<video controls") == 6
    assert "syncVideoCaptions" in script and "video=>video.pause()" in script
    assert "hydrateCampaignVideos" in script and 'data-poster=' in template
    assert "video.ariaLabel" in script and "addEventListener('play'" in script
    assert 'id="result-insights"' in template and "function renderInsights()" in script
    assert 'id="share-search"' in template and "navigator.share" in script and "navigator.clipboard" in script
    assert "/^[=+\\-@]/" in script
    assert 'id="reset-advanced"' in template and "$('#apply-advanced')?.click()" in script
    assert 'id="network-status"' in template and "beforeinstallprompt" in script
    assert "Service indépendant, non affilié" in template
    assert "OFFRE EN PRÉPARATION · PAIEMENT DÉSACTIVÉ" in template
    assert "10 favoris et 3 alertes" not in template
    assert 'id="target-result"' in template and "const target=(buy+profit)/rate" in script
    assert "function recordListingPrice(item)" in script and "track-action" in script
    assert 'id="recent-viewed"' in template and "recentlyViewed" in script and "recordViewed" in script
    assert 'data-price="{{ annonce.prix }}"' in template and "relative-price-flag" in script
    assert 'id="install-row" hidden' in template and "navigator.onLine" in script
    assert "fetchWithTimeout(url)" in script and "fetchWithTimeout(`/api/results/" in script
    assert 'id="export-all-data"' in template and 'id="delete-all-data"' in template
    assert 'id="local-data-size"' in template and "function renderDataSummary()" in script
    assert "prefers-reduced-motion" in styles and "safe-area-inset-bottom" in styles
    assert "luxe-radar-shell-v47" in worker
    assert "url.pathname.startsWith('/static/campaign/')" in worker
    assert "'/static/offline.html'" in worker and "event.request.mode === 'navigate'" in worker
    offline = (ROOT / "static" / "offline.html").read_text(encoding="utf-8")
    assert "<style" not in offline and "onclick=" not in offline
    assert "/static/offline.css" in offline and "/static/offline.js" in offline
    assert "url.pathname.startsWith('/static/')" in worker

    health = client.get("/api/health").get_json()
    assert health["status"] == "ok" and health["connectors"] >= 4
    for protected_path in (
        "/api/health",
        "/api/billing/plans",
        "/api/account/capabilities",
        "/api/results/00000000000000000000000000000000",
    ):
        protected_response = client.get(protected_path)
        assert protected_response.headers["Cache-Control"] == "no-store"
        assert protected_response.headers["X-Frame-Options"] == "DENY"
        assert protected_response.headers["X-Content-Type-Options"] == "nosniff"
        assert protected_response.headers.get("X-Request-ID")
    invalidate_app_metadata()
    first_metadata = _app_metadata()
    second_metadata = _app_metadata()
    assert first_metadata[0] is second_metadata[0] and first_metadata[1] is second_metadata[1]

    production_env = dict(os.environ)
    production_env["LUXE_RADAR_ENV"] = "production"
    production_env.pop("LUXE_RADAR_SECRET_KEY", None)
    refused = subprocess.run(
        [sys.executable, "-c", "import app_web"],
        cwd=ROOT,
        env=production_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert refused.returncode != 0
    assert "au moins 32" in (refused.stdout + refused.stderr)
    render_env = dict(os.environ)
    render_env.update({
        "LUXE_RADAR_ENV": "production",
        "LUXE_RADAR_SECRET_KEY": "render-test-secret-that-is-at-least-32-chars",
        "LUXE_RADAR_ALLOWED_HOSTS": "localhost",
        "RENDER_EXTERNAL_HOSTNAME": "luxe-radar-test.onrender.com",
        "LUXE_RADAR_TRUST_PROXY": "1",
    })
    render_host = subprocess.run(
        [sys.executable, "-c", "from app_web import app; r=app.test_client().get('/', headers={'Host':'luxe-radar-test.onrender.com','X-Forwarded-Proto':'https'}); cookie=r.headers.get('Set-Cookie',''); print(r.status_code, bool(r.headers.get('Strict-Transport-Security')), 'Secure' in cookie, '__Host-luxe_radar_session=' in cookie)"],
        cwd=ROOT,
        env=render_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert render_host.returncode == 0 and render_host.stdout.strip().endswith("200 True True True")
    assert (ROOT / "Procfile").read_text(encoding="utf-8").startswith("web: gunicorn")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in gitignore and "backups/" in gitignore and ".venv/" in gitignore
    assert ".env" in dockerignore and "backups" in dockerignore and ".venv" in dockerignore
    backed_up_names = {rel.as_posix() for _, rel in iter_backup_files(ROOT)}
    assert {".gitignore", ".dockerignore", ".env.example", "Procfile"} <= backed_up_names
    assert "luxe_radar_manager.py" in backed_up_names
    assert {"static/app.js", "static/app.css", "static/sw.js", "static/manifest.webmanifest"} <= backed_up_names
    assert "static/campaign/luxe_radar_revendeur_60s.mp4" in backed_up_names
    assert not any(name.endswith("_silencieux.mp4") or name.endswith("_voix.wav") for name in backed_up_names)
    assert ".env" not in backed_up_names
    gunicorn_source = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
    assert '"WEB_CONCURRENCY", "1"' in gunicorn_source and 'threads = 4' in gunicorn_source
    assert "wsgi:application" in (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    render_blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "plan: free" in render_blueprint and "generateValue: true" in render_blueprint
    assert "wsgi:application" in render_blueprint and "/api/health" in render_blueprint
    print("OK - Sécurité, i18n, modes, accessibilité, mobile et PWA validés.")


    for unsafe_probe_url in ("file:///etc/passwd", "http://127.0.0.1:5000", "http://user:pass@example.com"):
        try:
            _assert_public_http_url(unsafe_probe_url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"URL de probe dangereuse acceptée: {unsafe_probe_url}")


if __name__ == "__main__":
    main()
