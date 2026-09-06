"""Tests locaux sans connexion à Discord."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import asyncio

import discord_bot
from product_catalog import catalog_entries, correct_query, suggestions


def main():
    assert discord_bot.discord_identity(123) == "discord:123"
    assert discord_bot.client.personal_watch_worker.notifier == discord_bot.client._queue_personal_offer
    assert len(catalog_entries()) >= 250
    assert correct_query("Nike phenome elite") == "Nike Phenom Elite"
    assert "Nike Phenom Elite" in suggestions("phenome elite")
    assert correct_query("Marque totalement nouvelle ZX42") == "Marque totalement nouvelle ZX42"
    with TemporaryDirectory() as folder:
        path = Path(folder) / "watch.sqlite3"
        with patch.object(discord_bot.vinted_watch, "db_path", return_value=path):
            watch = discord_bot.vinted_watch.save_watch("discord:123", {
                "query": "On Cloud 5", "price_max": 80, "interval": 30,
            })
            assert watch["active"] and watch["interval"] == 30
            assert discord_bot.vinted_watch.get_watch("discord:123")["query"] == "On Cloud 5"
            assert discord_bot.vinted_watch.set_active("discord:123", False)
            assert discord_bot.vinted_watch.set_active("discord:123", True)
            assert discord_bot.vinted_watch.delete_watch("discord:123")
            assert discord_bot.vinted_watch.get_watch("discord:123") is None
    offer = {"titre": "On Cloud 5", "prix": 55, "devise": "EUR", "lien": "https://www.vinted.fr/items/123-on-cloud-5"}
    embed = discord_bot._offer_embed(offer)
    assert embed.title == "On Cloud 5" and embed.url == offer["lien"]
    view = discord_bot._offers_view([offer])
    assert len(view.children) == 2
    assert any(child.label == "🤝 Négocier" for child in view.children)
    plan = discord_bot.negotiation_plan(offer)
    assert plan["suggested"] == 49.5 and "49.50 €" in plan["message"]
    assert discord_bot.negotiation_plan({"prix": "inconnu"}) is None
    active = maximum = 0
    lock = asyncio.Lock()
    async def runner(rule):
        nonlocal active, maximum
        async with lock:
            active += 1
            maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
    assert asyncio.run(discord_bot._run_rules_bounded(range(8), runner, concurrency=99)) == 8
    assert maximum == 2
    print("[OK] Commandes Discord reliées à la surveillance Vinted")


if __name__ == "__main__":
    main()
