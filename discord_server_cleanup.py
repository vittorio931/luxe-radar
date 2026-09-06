"""Réduit prudemment les radars Discord aux douze recherches prioritaires."""

from __future__ import annotations

import argparse
import os

import discord
from dotenv import load_dotenv

from discord_radar import RuleStore
from discord_server_setup import DEFAULT_RADARS


KEEP_RADARS = frozenset({
    "vinted-nike", "vinted-adidas", "vinted-ralph-lauren", "vinted-lacoste",
    "luxe-louis-vuitton", "luxe-gucci", "luxe-dior", "luxe-moncler",
    "outdoor-columbia", "nike-running-pegasus", "nike-running-vomero",
    "nike-running-phenom-elite",
})


class CleanupClient(discord.Client):
    def __init__(self, guild_id: int, apply: bool):
        super().__init__(intents=discord.Intents.default())
        self.guild_id = guild_id
        self.apply = apply
        self.completed = False

    async def on_ready(self):
        try:
            guild = self.get_guild(self.guild_id)
            if guild is None:
                raise RuntimeError("Le bot n'a pas accès au serveur demandé")
            by_name = {channel.name: channel for channel in guild.text_channels}
            managed_names = set(DEFAULT_RADARS)
            removable = sorted(managed_names.intersection(by_name) - KEEP_RADARS)
            present_kept = sorted(KEEP_RADARS.intersection(by_name))
            print(f"Serveur : {guild.name}")
            print(f"Radars conservés : {len(present_kept)}")
            for name in present_kept:
                print(f"  GARDER  #{name}")
            print(f"Radars à supprimer : {len(removable)}")
            for name in removable:
                print(f"  RETIRER #{name}")
            if not self.apply:
                print("APERÇU UNIQUEMENT : aucun salon supprimé.")
                self.completed = True
                return
            store = RuleStore()
            deleted = 0
            for name in removable:
                channel = by_name[name]
                channel_id = channel.id
                await channel.delete(reason="Allègement des radars LUXE RADAR")
                store.delete_channel(guild.id, channel_id)
                deleted += 1
            # Les catégories de radars devenues vides ne servent plus à rien.
            removed_categories = 0
            for category in list(guild.categories):
                if category.name in {"🔎 EBAY · PAR MARQUE", "🧠 PROCESSEURS · 300 € MAX"} and not category.channels:
                    await category.delete(reason="Catégorie de radars désormais vide")
                    removed_categories += 1
            print(f"[OK] {deleted} salon(s) supprimé(s), {removed_categories} catégorie(s) vide(s) supprimée(s).")
            self.completed = True
        finally:
            await self.close()


def main():
    parser = argparse.ArgumentParser(description="Conserver seulement les douze radars Discord prioritaires")
    parser.add_argument("--guild-id", required=True, type=int)
    parser.add_argument("--apply", action="store_true", help="Supprimer réellement les radars secondaires")
    args = parser.parse_args()
    load_dotenv()
    token = str(os.environ.get("LUXE_RADAR_DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Token Discord manquant")
    client = CleanupClient(args.guild_id, args.apply)
    client.run(token, log_handler=None)
    if not client.completed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
