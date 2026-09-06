"""Contrats des dates et des tris source, sans réseau."""
import asyncio
import unittest
from unittest.mock import patch, MagicMock

from marketplaces.listing_dates import timestamp, vinted_publication, is_recent
from marketplaces.connectors.ebay import EbayConnector
from marketplaces.connectors.vinted import VintedConnector
import discord_bot


class ListingDateTests(unittest.TestCase):
    def test_relative_age_is_conservative(self):
        for label in ("Ajouté a l'instant", "Ajouté à l’instant", "Ajouté il y a moins d’une minute"):
            self.assertEqual(vinted_publication(label, now=2000000000)["listed_at"], 1999999940)
        value = vinted_publication("Ajouté\nIl y a 14 minutes", now=2000000000)
        self.assertEqual(value["listed_at"], 2000000000 - 15 * 60)
        self.assertTrue(value["listed_at_approximate"])
        self.assertTrue(is_recent(value, 900, now=2000000000))
        self.assertFalse(is_recent(vinted_publication("Ajouté il y a 15 minutes", now=2000000000), 900, now=2000000000))
        self.assertFalse(is_recent(vinted_publication("Ajouté il y a 2 mois", now=2000000000), 900, now=2000000000))

    def test_no_guess_from_detection_or_seller_activity(self):
        self.assertEqual(vinted_publication("Vu la dernière fois il y a 2 minutes"), {})
        self.assertFalse(is_recent({"detected_at": 2000000000}, now=2000000000))
        for value in ("invalid", "2026-09-06T10:00:00", float("nan"), None):
            self.assertIsNone(timestamp(value))
        self.assertEqual(timestamp("2026-09-06T10:00:00Z"), timestamp("2026-09-06T12:00:00+02:00"))

    def test_embed_displays_source_date_and_buy(self):
        offer = {"marketplace": "eBay", "titre": "CPU", "lien": "https://www.ebay.fr/itm/123456789012", "prix": 50, "listed_at": "2026-09-06T10:00:00Z", "detected_at": 2000000000}
        embed = discord_bot._offer_embed(offer)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("<t:", fields["Mise en vente"])
        self.assertIn("Détectée par le bot", fields)
        view = discord_bot._offers_view([offer])
        self.assertIn("BUY", view.children[0].label)
        self.assertEqual(view.children[0].url, offer["lien"])
        self.assertIsNone(view.timeout)
        offer.pop("listed_at")
        self.assertIn("non disponible", discord_bot._offer_embed(offer).fields[0].value)

    def test_vinted_passes_optional_newest_sort(self):
        with patch("radar_engine.rechercher_vinted", return_value=[]) as search:
            VintedConnector().search("Nike", 100, newest_first=True)
            self.assertTrue(search.call_args.kwargs["newest_first"])
            VintedConnector().search("Nike", 100)
            self.assertFalse(search.call_args.kwargs["newest_first"])

    def test_vinted_reads_official_summary_not_description(self):
        page = MagicMock()
        page.goto.return_value.status = 200
        page.locator.return_value.inner_text.return_value = "Description vendeur : Ajouté à l'instant"
        summary = page.get_by_test_id.return_value
        summary.locator.return_value.all_text_contents.return_value = ["Bon état", "Ajouté il y a 2 jours"]
        playwright = MagicMock()
        playwright.chromium.launch.return_value.new_page.return_value = page
        item = {"lien": "https://www.vinted.fr/items/1234567890-test"}
        with patch("playwright.sync_api.sync_playwright") as factory:
            factory.return_value.__enter__.return_value = playwright
            VintedConnector().enrich_listing_dates([item])
        self.assertFalse(is_recent(item))
        page.get_by_test_id.assert_called_with("item-page-summary-plugin")

    def test_vinted_access_refusal_stops_batch(self):
        page = MagicMock()
        page.goto.return_value.status = 403
        playwright = MagicMock()
        playwright.chromium.launch.return_value.new_page.return_value = page
        items = [{"lien": f"https://www.vinted.fr/items/{1234567890 + i}-test"} for i in range(2)]
        with patch("playwright.sync_api.sync_playwright") as factory:
            factory.return_value.__enter__.return_value = playwright
            VintedConnector().enrich_listing_dates(items)
        page.goto.assert_called_once()
        self.assertFalse(any(item.get("listed_at") for item in items))

    def test_ebay_newest_request_and_publication_preserved(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"itemSummaries": [
            {"itemId": "v1|123456789012|0", "title": "AMD Ryzen 5 5600", "price": {"value": "80", "currency": "EUR"}, "itemWebUrl": "https://www.ebay.fr/itm/123456789012", "itemCreationDate": "2026-09-06T10:00:00Z"},
            {"itemId": "v1|123456789013|0", "title": "AMD Ryzen 5 5600", "price": {"value": "20", "currency": "EUR"}, "itemWebUrl": "https://www.ebay.fr/itm/123456789013", "itemCreationDate": "2026-09-06T09:00:00Z"},
        ]}
        with patch("marketplaces.connectors.ebay.obtenir_token_ebay", return_value="test-token"), patch("marketplaces.connectors.ebay._SESSION.get", return_value=response) as request:
            results = EbayConnector().search("AMD Ryzen 5 5600", 300, newest_first=True)
            self.assertEqual(request.call_args.kwargs["params"]["sort"], "newlyListed")
            self.assertEqual([r["prix"] for r in results], [80, 20])
            self.assertEqual(results[0]["listed_at"], "2026-09-06T10:00:00Z")
            EbayConnector().search("AMD Ryzen 5 5600", 300)
            self.assertNotIn("sort", request.call_args.kwargs["params"])


if __name__ == "__main__":
    unittest.main()
