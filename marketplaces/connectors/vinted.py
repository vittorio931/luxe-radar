from marketplaces.connectors.base import MarketplaceConnector


class VintedConnector(MarketplaceConnector):
    name = "Vinted"

    supports_pagination = True
    expansion_page_size = 50
    expansion_recall_cap = 150
    max_pages = 4
    empty_pages_threshold = 3
    cooldown_seconds = 0.5

    def search(self, query, price_max, limit=20, page=1, newest_first=False):
        from radar_engine import rechercher_vinted

        annonces = rechercher_vinted(
            query,
            price_max,
            limite=limit,
            headless=True,
            page=page,
            newest_first=newest_first,
        )

        resultats = []

        for annonce in annonces or []:
            item = dict(annonce)

            # On conserve EXACTEMENT les champs du moteur Vinted.
            item["marketplace"] = "Vinted"
            item["plateforme"] = "Vinted"

            # Compatibilité avec l'interface commune.
            item.setdefault("title", item.get("titre", ""))
            item.setdefault("price", item.get("prix"))
            item.setdefault("url", item.get("lien"))
            item.setdefault("image", item.get("photo"))
            item.setdefault("brand", item.get("marque"))
            item.setdefault("seller", item.get("vendeur"))

            resultats.append(item)

        return resultats

    def enrich_listing_dates(self, offers):
        """Lit au plus cinq fiches publiques via le navigateur habituel.

        Aucun fallback après un refus : challenge, 403 ou 429 arrêtent le lot.
        Une date absente reste absente et ne permet pas une alerte récente.
        """
        from playwright.sync_api import sync_playwright
        from marketplaces.listing_dates import vinted_publication
        from quick_buy import safe_vinted_url
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=15000)
            try:
                page = browser.new_page(locale="fr-FR")
                for offer in offers[:5]:
                    url = safe_vinted_url(offer.get("lien") or offer.get("url"))
                    if not url:
                        continue
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=7000)
                        offer["listing_date_status"] = f"HTTP {response.status}" if response else "Aucune réponse"
                        if response and response.status in (401, 403, 429):
                            break
                        if not response or response.status != 200:
                            continue
                        text = page.locator("body").inner_text(timeout=1500)
                        if any(term in text.casefold() for term in ("verify you are human", "captcha", "access denied")):
                            break
                        # Le champ officiel est dans le résumé, hors description
                        # du vendeur : un texte libre ne peut pas simuler une date.
                        summary = page.get_by_test_id("item-page-summary-plugin")
                        summary.wait_for(state="visible", timeout=2000)
                        labels = summary.locator("span").all_text_contents()
                        offer.update(vinted_publication(" ".join(labels)))
                        if not offer.get("listed_at"):
                            offer["listing_date_status"] = "Date absente : " + " | ".join(labels)[-160:]
                    except Exception as exc:
                        offer["listing_date_status"] = type(exc).__name__
                        continue
            finally:
                browser.close()
        return offers

    def search_page(self, query, price_max=None, limit=20, page=1):
        return self.search(query=query, price_max=price_max, limit=limit, page=page)
