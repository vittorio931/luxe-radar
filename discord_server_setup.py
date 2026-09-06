"""Organisation idempotente du serveur Discord LUXE RADAR.

Le script ajoute seulement les catégories et salons absents. Il ne supprime,
ne renomme et ne déplace aucun élément déjà présent.
"""

from __future__ import annotations

import os
import argparse

import discord
from dotenv import load_dotenv
from discord_radar import RuleStore


BASE_STRUCTURE = (
    ("📌 ACCUEIL", (
        ("bienvenue", "Bienvenue sur Luxe Radar : commence ici."),
        ("reglement", "Règles et sécurité de la communauté."),
        ("comment-utiliser-le-bot", "Guide rapide des commandes du Bot Vinted."),
        ("annonces-officielles", "Nouveautés et annonces officielles de Luxe Radar."),
    )),
    ("⚡ RADARS VINTED", (
        ("alertes-vinted", "Nouvelles annonces détectées par les Radars."),
        ("commandes-bot", "Configure ton Radar avec les commandes slash."),
        ("resultats-et-bons-plans", "Partage et analyse les meilleures annonces."),
        ("aide-configuration", "Aide pour créer des filtres précis."),
    )),
    ("👕 COMMUNAUTÉ", (
        ("discussion", "Discussion générale de la communauté."),
        ("sneakers", "Sneakers, running et références recherchées."),
        ("streetwear", "Streetwear et tendances."),
        ("luxe", "Mode luxe et authentification prudente."),
        ("verification-authenticite", "Avis communautaires : aucune garantie d’authenticité."),
    )),
    ("💰 ACHAT-REVENTE", (
        ("estimations", "Estimations de prix et de marge."),
        ("reussites-des-membres", "Partage tes meilleures trouvailles."),
        ("conseils-revente", "Conseils responsables pour l’achat-revente."),
    )),
    ("🛟 SUPPORT", (
        ("signaler-un-probleme", "Signale un problème avec le site ou le bot."),
        ("suggestions", "Propose une fonctionnalité ou une amélioration."),
    )),
)

# Un salon = une source et une recherche. Les budgets sont modifiables avec
# /surveiller ; seule la limite processeur de 300 EUR a été choisie par l'utilisateur.
BRANDS = (
    ("nike", "Nike"), ("adidas", "Adidas"), ("ralph-lauren", "Ralph Lauren"),
    ("lacoste", "Lacoste"), ("the-north-face", "The North Face"),
    ("carhartt", "Carhartt"), ("stone-island", "Stone Island"), ("stussy", "Stussy"),
)
LUXURY_BRANDS = (
    ("louis-vuitton", "Louis Vuitton"), ("gucci", "Gucci"),
    ("prada", "Prada"), ("dior", "Dior"),
    ("balenciaga", "Balenciaga"), ("moncler", "Moncler"),
    ("burberry", "Burberry"), ("saint-laurent", "Saint Laurent"),
    ("fendi", "Fendi"), ("givenchy", "Givenchy"),
    ("valentino", "Valentino"), ("versace", "Versace"),
    ("jacquemus", "Jacquemus"), ("celine", "Celine"),
    ("loewe", "Loewe"), ("bottega-veneta", "Bottega Veneta"),
    ("maison-margiela", "Maison Margiela"), ("ami-paris", "Ami Paris"),
    ("cp-company", "C.P. Company"), ("canada-goose", "Canada Goose"),
    ("arcteryx", "Arc'teryx"), ("off-white", "Off-White"),
    ("palm-angels", "Palm Angels"),
)
NIKE_RUNNING = (
    ("pegasus", "Nike Pegasus"), ("vomero", "Nike Vomero"),
    ("vaporfly", "Nike Vaporfly"), ("alphafly", "Nike Alphafly"),
    ("zoom-fly", "Nike Zoom Fly"), ("invincible", "Nike Invincible"),
    ("infinityrn", "Nike InfinityRN"), ("structure", "Nike Structure"),
    ("streakfly", "Nike Streakfly"), ("pegasus-trail", "Nike Pegasus Trail"),
    ("zegama", "Nike Zegama"), ("terra-kiger", "Nike Terra Kiger"),
    ("wildhorse", "Nike Wildhorse"), ("juniper-trail", "Nike Juniper Trail"),
    ("phenom-elite", "Nike Phenom Elite"),
)
DEFAULT_RADARS = {
    **{f"vinted-{slug}": {"source": "Vinted", "query": brand, "price_max": 100, "interval": 120}
       for slug, brand in BRANDS},
    **{f"ebay-{slug}": {"source": "eBay", "query": brand, "price_max": 100, "interval": 120}
       for slug, brand in BRANDS[:4]},
    **{f"{slug}-cpu-{family}": {"source": source, "query": query, "price_max": 300, "interval": 120}
       for slug, source in (("vinted", "Vinted"), ("ebay", "eBay"))
       for family, query in (("amd", "AMD Ryzen"), ("intel", "Intel Core"))},
    **{f"luxe-{slug}": {"source": "Vinted", "query": brand, "price_max": 2500, "interval": 120}
       for slug, brand in LUXURY_BRANDS},
    "outdoor-columbia": {"source": "Vinted", "query": "Columbia", "price_max": 500, "interval": 120},
    **{f"nike-running-{slug}": {"source": "Vinted", "query": query, "price_max": 500, "interval": 120}
       for slug, query in NIKE_RUNNING},
}


def radar_topic(name):
    rule = DEFAULT_RADARS[name]
    return f"Alertes {rule['source']} · {rule['query']} · ≤ {rule['price_max']} EUR · /etat-surveillance pour le dernier scan."


STRUCTURE = BASE_STRUCTURE + (
    ("🏷️ VINTED · PAR MARQUE", tuple((f"vinted-{slug}", radar_topic(f"vinted-{slug}")) for slug, _ in BRANDS)),
    ("🔎 EBAY · PAR MARQUE", tuple((f"ebay-{slug}", radar_topic(f"ebay-{slug}")) for slug, _ in BRANDS[:4])),
    ("🧠 PROCESSEURS · 300 € MAX", tuple((name, radar_topic(name)) for name in DEFAULT_RADARS if "-cpu-" in name)),
    ("📡 PILOTAGE DES ALERTES", (
        ("guide-alertes", "Configurer les alertes automatiques Vinted/eBay et consulter leur état."),
    )),
    ("💎 VINTED · LUXE", tuple((f"luxe-{slug}", radar_topic(f"luxe-{slug}")) for slug, _ in LUXURY_BRANDS)),
    ("🏔️ VINTED · OUTDOOR", (("outdoor-columbia", radar_topic("outdoor-columbia")),)),
    ("🏃 NIKE RUNNING", tuple((f"nike-running-{slug}", radar_topic(f"nike-running-{slug}")) for slug, _ in NIKE_RUNNING)),
)

LUXURY_STRUCTURE = (
    ("💎 VINTED · LUXE", tuple((f"luxe-{slug}", radar_topic(f"luxe-{slug}")) for slug, _ in LUXURY_BRANDS)),
)
SPORT_STRUCTURE = (
    ("🏔️ VINTED · OUTDOOR", (("outdoor-columbia", radar_topic("outdoor-columbia")),)),
    ("🏃 NIKE RUNNING", tuple((f"nike-running-{slug}", radar_topic(f"nike-running-{slug}")) for slug, _ in NIKE_RUNNING)),
)


STARTER_MESSAGES = {
    "guide-alertes": (
        "# 📡 Tes radars automatiques\n"
        "Les salons par marque reçoivent les annonces de leur marketplace. "
        "Les salons CPU AMD/Intel recherchent les processeurs seuls jusqu’à **300 €**.\n\n"
        "**Gestionnaires :** `/surveiller` règle la source, le produit, le prix et l’intervalle du salon ; "
        "`/arreter-surveillance` le met en pause. `/etat-surveillance` affiche le dernier résultat.\n"
        "**Recherche immédiate :** `/ebay` ou `/radar` puis `/scanner` pour Vinted.\n\n"
        "Le bot doit rester en ligne. Les scans peuvent être retardés ou indisponibles selon la source. "
        "Les alertes exigent une date de publication vérifiée de moins de 15 minutes, "
        "réglable avec anciennete_max. Le bouton BUY ouvre l’annonce officielle. "
        "Au plus 5 annonces par passage ; les annonces déjà envoyées sont mémorisées après redémarrage. "
        "Un résultat vide ne prouve pas que la source fonctionne.\n"
        "Les prix affichés ne garantissent ni une bonne affaire ni l’authenticité. "
        "Vérifie l’état, la disponibilité et les frais sur la marketplace avant l’achat."
    ),
    "bienvenue": (
        "# ⚡ Bienvenue sur LUXE RADAR\n"
        "Configure un Radar avec `/radar`, vérifie son état avec `/statut`, "
        "puis lance un scan avec `/scanner`. Utilise `/aide` pour toutes les commandes."
    ),
    "reglement": (
        "# 📜 Règlement\n"
        "1. Respect obligatoire : aucun harcèlement ni discrimination.\n"
        "2. Aucun spam, démarchage privé ou publicité sauvage.\n"
        "3. Aucune contrefaçon, arnaque ou lien suspect.\n"
        "4. Ne partage jamais mot de passe, token, cookie, code 2FA ou donnée bancaire.\n"
        "5. Les achats et paiements se terminent uniquement sur la marketplace officielle.\n"
        "6. Aucun contournement des protections ou restrictions des marketplaces."
    ),
    "comment-utiliser-le-bot": (
        "# 🤖 Utiliser le Bot Vinted\n"
        "`/radar` créer ou modifier une surveillance\n"
        "`/scanner` chercher maintenant\n"
        "`/alertes` consulter les détections\n"
        "`/pause` et `/reprendre` contrôler le Radar\n"
        "Le bouton Acheter ouvre Vinted : vérifie toujours l’article avant de payer."
    ),
    "commandes-bot": "Tape `/aide` pour afficher les commandes, puis `/radar` pour commencer.",
}


def validate_structure() -> tuple[int, int]:
    categories = [name for name, _ in STRUCTURE]
    channels = [name for _, items in STRUCTURE for name, _ in items]
    if len(categories) != len(set(categories)) or len(channels) != len(set(channels)):
        raise ValueError("Structure Discord dupliquée")
    return len(categories), len(channels)


async def setup_guild(guild: discord.Guild, store=None, structure=None) -> dict:
    store = store or RuleStore()
    structure = structure or STRUCTURE
    member = guild.me
    if not member or not member.guild_permissions.manage_channels:
        raise PermissionError("La permission Gérer les salons est requise")
    created_categories = created_channels = configured_radars = 0
    existing_channels = {channel.name.casefold(): channel for channel in guild.text_channels}
    existing_categories = {category.name.casefold(): category for category in guild.categories}
    for category_name, channel_specs in structure:
        category = existing_categories.get(category_name.casefold())
        if category is None:
            category = await guild.create_category(category_name, reason="Installation LUXE RADAR")
            existing_categories[category_name.casefold()] = category
            created_categories += 1
        for channel_name, topic in channel_specs:
            channel = existing_channels.get(channel_name.casefold())
            if channel is None:
                options = {}
                if channel_name in DEFAULT_RADARS:
                    options["overwrites"] = {
                        guild.default_role: discord.PermissionOverwrite(send_messages=False),
                        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
                    }
                channel = await guild.create_text_channel(
                    channel_name, category=category, topic=topic,
                    reason="Installation LUXE RADAR", **options,
                )
                existing_channels[channel_name.casefold()] = channel
                created_channels += 1
                starter = STARTER_MESSAGES.get(channel_name)
                if starter:
                    await channel.send(starter, allowed_mentions=discord.AllowedMentions.none())
            if channel_name in DEFAULT_RADARS:
                configured_radars += int(store.save(guild.id, channel.id, DEFAULT_RADARS[channel_name], only_if_missing=True))
    return {"categories": created_categories, "channels": created_channels, "radars": configured_radars}


class SetupClient(discord.Client):
    def __init__(self, guild_id, structure=None):
        super().__init__(intents=discord.Intents.default())
        self.target_guild_id = guild_id
        self.structure = structure or STRUCTURE
        self.completed = False

    async def on_ready(self):
        try:
            guild = self.get_guild(self.target_guild_id)
            if guild is None:
                print("[ERREUR] Le bot n’a pas accès au serveur demandé.")
                return
            result = await setup_guild(guild, structure=self.structure)
            self.completed = True
            print(f"[OK] {guild.name}: {result['categories']} catégorie(s), {result['channels']} salon(s), {result['radars']} radar(s) configurés")
        finally:
            await self.close()


def main() -> None:
    validate_structure()
    parser = argparse.ArgumentParser(description="Organisation et radars Discord LUXE RADAR")
    parser.add_argument("--guild-id", type=int, help="Identifiant du seul serveur à modifier")
    parser.add_argument("--apply", action="store_true", help="Appliquer la structure sur ce serveur")
    parser.add_argument("--base-only", action="store_true", help="Installer seulement les catégories communautaires de base")
    parser.add_argument("--luxury-only", action="store_true", help="Installer seulement les Radars Vinted luxe")
    parser.add_argument("--sports-only", action="store_true", help="Installer seulement Columbia et Nike Running")
    args = parser.parse_args()
    if not args.apply:
        for category, channels in STRUCTURE:
            print(category)
            for name, topic in channels:
                print(f"  #{name} — {topic}")
        print("Aucun changement appliqué. Utiliser --apply --guild-id IDENTIFIANT pour installer.")
        return
    if not args.guild_id or args.guild_id <= 0:
        parser.error("--guild-id est obligatoire avec --apply")
    load_dotenv()
    token = str(os.environ.get("LUXE_RADAR_DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Token Discord manquant")
    selected = SPORT_STRUCTURE if args.sports_only else LUXURY_STRUCTURE if args.luxury_only else BASE_STRUCTURE if args.base_only else STRUCTURE
    client = SetupClient(args.guild_id, selected)
    client.run(token, log_handler=None)
    if not client.completed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
