"""Tests hors réseau : CPU, sources, permissions, envoi et reprise des radars."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import time
from unittest.mock import AsyncMock, MagicMock

import discord_bot
import discord_radar as radar
from discord_server_setup import setup_guild, DEFAULT_RADARS, validate_structure
from product_catalog import correct_query, suggestions


def offer(source="eBay", title="AMD Ryzen 7 7800X3D", price=250, number=123456789012):
    url = f"https://www.ebay.fr/itm/{number}" if source == "eBay" else f"https://www.vinted.fr/items/{number}-cpu"
    return {"marketplace": source, "titre": title, "prix": price, "lien": url, "listed_at": time.time() - 60}


class ProcessorTests(unittest.TestCase):
    def test_exact_models(self):
        for query, title, expected in (
            ("7800X3D", "Processeur AMD Ryzen 7 7800X3D", True),
            ("AMD Ryzen 7 7800X3D", "AMD Ryzen 7 7800X", False),
            ("Ryzen 5 5600", "Ryzen 5 5600X", False),
            ("Intel Core i5-12400F", "Processeur Intel Core i5 12400F", True),
            ("Intel Core i5-12400F", "Processeur Intel Core i5 12400", False),
            ("AMD Ryzen", "AMD Ryzen 5 5600X en boîte", True),
            ("Intel Core", "AMD Ryzen 5 5600X", False),
            ("CPU", "Sac à main", False),
        ):
            with self.subTest(query=query, title=title):
                self.assertEqual(radar.cpu_matches(query, title), expected)

    def test_accessories_and_broken_items(self):
        for prefix in ("Ventirad pour", "Carte mère pour", "PC gamer", "PC", "Boîte vide", "HS", "Cooler for", "Gaming PC", "For parts", "Défaut", "Defekt"):
            self.assertFalse(radar.cpu_matches("7800X3D", prefix + " AMD Ryzen 7 7800X3D"))
        self.assertTrue(radar.cpu_matches("7800X3D", "AMD Ryzen 7 7800X3D sans défaut"))
        self.assertFalse(radar.cpu_matches("AMD Ryzen", "Mini Lenovo M715q Tiny AMD Ryzen 3 PRO Ram 8 Go NVME 128 Win 11 pro WIFI"))

    def test_preserve_reference_and_fashion(self):
        self.assertEqual(correct_query("Intel i5 12400F"), "Intel i5 12400F")
        self.assertEqual(correct_query("Ryzen 5 5600"), "Ryzen 5 5600")
        self.assertEqual(correct_query("Nike phenome elite"), "Nike Phenom Elite")
        self.assertTrue(any("7800X3D" in value for value in suggestions("7800X3D")))
        self.assertTrue(radar.cpu_matches("Nike Trail", "Nike Trail running shirt"))

    def test_scans_both_connectors(self):
        for source in radar.SOURCES:
            sample = offer(source)
            connector = MagicMock()
            connector.search.return_value = [sample, sample, offer(source, price=301), offer(source, price=float("nan")), offer(source, title="Ventirad pour AMD Ryzen 7 7800X3D")]
            factory = MagicMock(return_value=connector)
            results = radar.scan({"source": source, "query": "7800X3D", "price_max": 300}, factory)
            self.assertEqual(len(results), 1)
            factory.assert_called_once_with(source)
            self.assertEqual(results[0]["marketplace"], source)

    def test_safe_links_and_stable_keys(self):
        good = offer()
        self.assertTrue(radar.safe_offer_url(good))
        for url in ("https://ebay.fr.evil.test/itm/123", "https://evil.test/itm/123", "https://user@ebay.fr/itm/123", "http://ebay.fr/itm/123", "https://ebay.fr:bad/itm/123"):
            self.assertFalse(radar.safe_offer_url(dict(good, lien=url)))
        self.assertEqual(radar.offer_key(good), radar.offer_key(dict(good, lien=good["lien"] + "?tracking=changed")))
        self.assertEqual(discord_bot._offer_embed(good).url, good["lien"])


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = TemporaryDirectory()
        self.path = Path(self.folder.name) / "radars.sqlite3"
        self.store = radar.RuleStore(self.path)
        self.store.save(1, 2, {"source": "eBay", "query": "7800X3D", "price_max": 300, "interval": 120})

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def test_delivery_and_restart_dedupe(self):
        rule = self.store.rules(1)[0]
        send = AsyncMock()
        await radar.run_rule(rule, self.store, lambda _: [offer(), offer()], send)
        self.assertEqual(send.await_count, 1)
        fresh = radar.RuleStore(self.path)
        await radar.run_rule(rule, fresh, lambda _: [offer()], send)
        self.assertEqual(send.await_count, 1)
        self.assertFalse(fresh.rules(due=True))

    async def test_failure_retries_unsent(self):
        rule = self.store.rules(1)[0]
        await radar.run_rule(rule, self.store, lambda _: [offer()], AsyncMock(side_effect=OSError))
        self.assertFalse(self.store.seen(2, offer()))
        self.assertIn("Erreur", self.store.rules(1)[0]["status"])
        send = AsyncMock()
        await radar.run_rule(rule, self.store, lambda _: [offer()], send)
        send.assert_awaited_once()

    async def test_pause_and_changes_during_scan(self):
        rule = self.store.rules(1)[0]
        def scanner(_):
            self.store.pause(1, 2)
            return [offer()]
        send = AsyncMock()
        await radar.run_rule(rule, self.store, scanner, send)
        send.assert_not_awaited()
        self.assertFalse(self.store.pause(999, 2))

    async def test_changed_rule_invalidates_inflight_scan(self):
        rule = self.store.rules(1)[0]
        self.store.save(1, 2, {"query": "Intel Core", "price_max": 300})
        send = AsyncMock()
        await radar.run_rule(rule, self.store, lambda _: [offer()], send)
        send.assert_not_awaited()

    async def test_batch_cap(self):
        send = AsyncMock()
        await radar.run_rule(self.store.rules(1)[0], self.store,
                            lambda _: [offer(number=123456789000 + i) for i in range(10)], send)
        self.assertEqual(send.await_count, 5)

    async def test_no_old_or_unknown_publications(self):
        recent = offer(number=111111111111)
        old = dict(offer(number=222222222222), listed_at=time.time() - 86400)
        unknown = dict(offer(number=333333333333), listed_at=None)
        future = dict(offer(number=444444444444), listed_at=time.time() + 3600)
        send = AsyncMock()
        await radar.run_rule(self.store.rules(1)[0], self.store,
                            lambda _: [old, unknown, future, recent], send)
        send.assert_awaited_once()
        self.assertEqual(send.call_args.args[1]["lien"], recent["lien"])

    async def test_old_backlog_cannot_hide_fresh_offer(self):
        old = [dict(offer(number=555555550000 + i), listed_at=time.time() - 86400) for i in range(10)]
        recent = offer(number=777777777777)
        send = AsyncMock()
        await radar.run_rule(self.store.rules(1)[0], self.store, lambda _: old + [recent], send)
        send.assert_awaited_once()

    async def test_vinted_date_enrichment_before_alert(self):
        undated = dict(offer("Vinted"), listed_at=None)
        def enrich(offers):
            offers[0]["listed_at"] = time.time() - 30
        send = AsyncMock()
        await radar.run_rule(self.store.rules(1)[0], self.store, lambda _: [undated], send, date_enricher=enrich)
        send.assert_awaited_once()

    async def test_unknown_date_is_alerted_after_reference_scan(self):
        undated = dict(offer("Vinted", number=919191919191), listed_at=None)
        send = AsyncMock()
        await radar.run_rule(self.store.rules(1)[0], self.store, lambda _: [undated], send, date_enricher=lambda _: None)
        send.assert_not_awaited()
        with self.store.connect() as db:
            db.execute("UPDATE deferred_offers SET retry_after=0")
            db.execute("UPDATE channel_rules SET next_run=0")
        await radar.run_rule(self.store.rules(1)[0], self.store, lambda _: [undated], send, date_enricher=lambda _: None)
        send.assert_awaited_once()

    async def test_setup_idempotent_preserves_pause(self):
        guild = MagicMock()
        guild.id = 10
        guild.text_channels, guild.categories = [], []
        guild.me.guild_permissions.manage_channels = True
        async def create_category(name, **kwargs):
            category = SimpleNamespace(name=name)
            guild.categories.append(category)
            return category
        async def create_channel(name, **kwargs):
            channel = SimpleNamespace(name=name, id=1000 + len(guild.text_channels), send=AsyncMock())
            guild.text_channels.append(channel)
            return channel
        guild.create_category = AsyncMock(side_effect=create_category)
        guild.create_text_channel = AsyncMock(side_effect=create_channel)
        result = await setup_guild(guild, self.store)
        self.assertEqual((result["categories"], result["channels"]), validate_structure())
        self.assertEqual(result["radars"], len(DEFAULT_RADARS))
        first = self.store.rules(10)[0]
        self.store.pause(10, first["channel_id"])
        self.assertEqual(await setup_guild(guild, self.store), {"categories": 0, "channels": 0, "radars": 0})
        self.assertFalse(next(r for r in self.store.rules(10) if r["channel_id"] == first["channel_id"])["active"])

    async def test_command_permissions(self):
        for name in ("surveiller", "arreter-surveillance"):
            command = discord_bot.client.tree.get_command(name)
            self.assertTrue(command.guild_only)
            self.assertTrue(command.default_permissions.manage_channels)
            self.assertTrue(command.checks)


if __name__ == "__main__":
    unittest.main()
