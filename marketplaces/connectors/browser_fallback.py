"""Fallback navigateur partage via Playwright pour les sources JS-rendered.

Chaque connecteur qui ne peut pas servir en HTTP pur fournit des
attributs de classe pour que _PublicRetailBase.search() puisse
tenter un fallback Playwright.
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False
    sync_playwright = None

try:
    from .source_health import SourceHealthRegistry
    _source_health = SourceHealthRegistry.instance()
except Exception:
    _source_health = None

_REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_ANTI_DETECT_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


def browser_available() -> bool:
    return _HAS_PLAYWRIGHT


def search_via_browser(
    connector,
    query: str,
    price_max=None,
    limit: int = 20,
    *,
    extra_wait_ms: int = 0,
) -> list:
    """Lance une recherche Playwright headless pour un connecteur.

    Le connecteur peut exposer :
      - browser_search_template   (str avec {q})  OU
      - browser_search_input_sel  (CSS selector d'une barre de recherche)
      - browser_card_sel          (CSS selector pour les cartes produit)
      - browser_title_sel         (CSS selector dans la carte)
      - browser_price_sel         (CSS selector dans la carte)
      - browser_link_sel          (optionnel, defaut: "a")
      - browser_image_sel         (optionnel)
      - browser_wait_ms           (optionnel, defaut 3000)
      - browser_timeout_ms        (optionnel, defaut 25000)
    """
    if not _HAS_PLAYWRIGHT:
        return []

    tpl = getattr(connector, "browser_search_template", "")
    input_sel = getattr(connector, "browser_search_input_sel", "")
    card_sel = getattr(connector, "browser_card_sel", "")
    title_sel = getattr(connector, "browser_title_sel", "")
    price_sel = getattr(connector, "browser_price_sel", "")
    link_sel = getattr(connector, "browser_link_sel", "a")
    image_sel = getattr(connector, "browser_image_sel", "")
    wait_ms = int(getattr(connector, "browser_wait_ms", 3000)) + extra_wait_ms
    timeout_ms = int(getattr(connector, "browser_timeout_ms", 15000))
    base_url = getattr(connector, "base_url", "")
    name = getattr(connector, "name", "Browser")

    if not card_sel or not title_sel or not price_sel:
        return []

    results = []
    try:
        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                ctx = browser.new_context(
                    viewport={"width": 1360, "height": 900},
                    user_agent=_REAL_UA,
                    locale="fr-FR",
                )
                page = ctx.new_page()
                page.add_init_script(_ANTI_DETECT_JS)

                navigated = False
                if tpl:
                    url = tpl.format(q=quote(query), query_raw=query)
                    print(f"[{name}] Fallback navigateur -> {url[:100]}")
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(wait_ms)
                    _scroll_and_settle(page, card_sel, timeout_ms)
                    navigated = True
                elif input_sel and base_url:
                    print(f"[{name}] Fallback navigateur (input) -> {base_url}")
                    page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(3000)
                    try:
                        inp = page.locator(input_sel).first
                        inp.fill(query)
                        inp.press("Enter")
                        page.wait_for_timeout(wait_ms)
                        _scroll_and_settle(page, card_sel, timeout_ms)
                        navigated = True
                    except Exception as e:
                        print(f"[{name}] Erreur input recherche: {e}")

                if not navigated:
                    return []

                # Retry: si aucune carte trouvée après le premier settle, scroll
                # une dernière fois avant d'abandonner (certains sites SPA ont un
                # délai de hydratation après le dernier réseau idle).
                cards = page.locator(card_sel)
                if cards.count() == 0:
                    page.wait_for_timeout(2000)
                    _scroll_and_settle(page, card_sel, timeout_ms)
                    cards = page.locator(card_sel)

                count = min(cards.count(), max(limit * 3, 60))
                print(f"[{name}] Navigateur: {cards.count()} cartes trouvees, {count} analysees")

                seen = set()
                for i in range(count):
                    card = cards.nth(i)
                    try:
                        title_el = card.locator(title_sel).first
                        # Pour les <img>, on utilise l'attribut alt
                        try:
                            tag = title_el.evaluate("el => el.tagName", timeout=500)
                        except Exception:
                            tag = ""
                        if tag and tag.lower() == "img":
                            title = (title_el.get_attribute("alt", timeout=500) or "").strip()
                        else:
                            title = title_el.inner_text(timeout=500).strip()
                        # Nettoie les titres multi-lignes (prend les 2 premières lignes non vides)
                        if title and "\n" in title:
                            lines = [l.strip() for l in title.split("\n") if l.strip()]
                            title = " ".join(lines[:2])
                        if not title or len(title) < 3:
                            continue

                        # Essaie plusieurs sélecteurs de prix séparés par une virgule
                        price = None
                        for psel in [s.strip() for s in price_sel.split(",")]:
                            try:
                                price_text = card.locator(psel).first.inner_text(timeout=500)
                                price = _parse_price(price_text)
                                if price and price > 0:
                                    break
                            except Exception:
                                continue
                        if price is None or price <= 0:
                            continue

                        link = None
                        if link_sel:
                            try:
                                link = card.locator(link_sel).first.get_attribute("href", timeout=500)
                            except Exception:
                                pass
                        if not link:
                            try:
                                link = card.locator("a").first.get_attribute("href", timeout=500)
                            except Exception:
                                pass
                        if link and not link.startswith("http"):
                            link = urljoin(base_url, link)

                        image = None
                        if image_sel:
                            try:
                                img = card.locator(image_sel).first
                                image = img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("srcset")
                                if image and "," in image:
                                    image = image.split(",")[0].strip().split()[0]
                            except Exception:
                                pass
                        if not image:
                            try:
                                img = card.locator("img").first
                                image = img.get_attribute("src") or img.get_attribute("data-src")
                            except Exception:
                                pass

                        key = (title[:50].lower(), round(price, 2))
                        if key in seen:
                            continue
                        seen.add(key)

                        results.append({
                            "marketplace": name,
                            "titre": title,
                            "prix": round(price, 2),
                            "prix_original": round(price, 2),
                            "prix_compare_original": None,
                            "devise_originale": "EUR",
                            "devise": "EUR",
                            "lien": link or base_url,
                            "image": image,
                            "modele": None,
                            "reference": None,
                            "vendor": None,
                            "type_produit_site": None,
                            "disponible": True,
                            "reduction_pourcent": None,
                            "categorie": "A VERIFIER",
                            "score": 72,
                            "score_match": 78,
                            "score_confiance": 58,
                            "score_affaire": 52,
                            "alertes": ["Verifier disponibilite, taille et frais sur le site marchand"],
                            "raisons": ["Carte produite lue via fallback navigateur"],
                        })
                        if len(results) >= limit:
                            break
                    except Exception:
                        continue

            except Exception as exc:
                print(f"[{name}] Erreur navigateur: {exc}")
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
    except Exception as exc:
        print(f"[{name}] Playwright indisponible: {exc}")

    if _source_health and results:
        _source_health.record_outcome(name, "browser_fallback_ok", count=len(results))

    print(f"[{name}] Navigateur: {len(results)} resultats")
    return results


def _parse_price(text):
    """Parse un prix depuis du texte brut."""
    if not text:
        return None
    text = text.replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
    m = re.search(r"(\d+\.?\d*)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _scroll_and_settle(page, card_sel: str, timeout_ms: int):
    """Scroll progressif + attente réseau pour sites SPA.

    Les sites React/Next.js hydratent après le premier paint. On scrolle
    progressivement pour déclencher le lazy loading puis on attend que le
    réseau soit inactif (plus fiable qu'un simple timeout fixe).
    """
    max_scrolls = 4
    for _ in range(max_scrolls):
        try:
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)", timeout=3000)
        except Exception:
            break
        page.wait_for_timeout(800)
        try:
            cards = page.locator(card_sel)
            if cards.count() > 0:
                break
        except Exception:
            pass
    # Network settle : attend que les requêtes XHR/fetch soient terminées.
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
    except Exception:
        pass
    page.wait_for_timeout(1000)
