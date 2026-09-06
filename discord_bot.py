"""Bot Discord officiel de LUXE RADAR.

Les commandes pilotent la surveillance locale. Aucun identifiant Vinted ni
moyen de paiement n'est collecté, et l'achat final reste effectué sur Vinted.
"""

from __future__ import annotations

import asyncio
import os
import re
from threading import BoundedSemaphore

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from marketplaces.connectors import get_connector
from product_catalog import correct_query, suggestions
import quick_buy
import vinted_watch
import discord_radar


# Plafond commun aux salons, alertes personnelles et commandes manuelles.
# Il protège le PC même lorsque plusieurs files demandent un scan en même temps.
_SCAN_SLOTS = BoundedSemaphore(2)


def discord_identity(user_id: int) -> str:
    return f"discord:{int(user_id)}"


def configured_token() -> str:
    return str(os.environ.get("LUXE_RADAR_DISCORD_BOT_TOKEN") or "").strip()


def _scan(criteria: dict) -> list[dict]:
    with _SCAN_SLOTS:
        offers = discord_radar.scan(criteria, connector_factory=get_connector)
        if not criteria.get("_automatic"):
            discord_radar.enrich_dates(offers[:5])
        return offers


def _offer_embed(offer: dict, index: int = 1) -> discord.Embed:
    url = discord_radar.safe_offer_url(offer)
    source = offer.get("marketplace", "Vinted")
    title = str(offer.get("titre") or offer.get("title") or f"Annonce {source}")[:256]
    price = offer.get("prix", "—")
    currency = str(offer.get("devise") or "EUR")[:12]
    embed = discord.Embed(
        title=title, url=url, color=0xD4AF37,
        description=f"💶 **{price} {currency}** · résultat n°{index}",
    )
    published = discord_radar.timestamp(offer.get("listed_at"))
    detected = discord_radar.timestamp(offer.get("detected_at"))
    if published:
        label = "Mise en vente (approx.)" if offer.get("listed_at_approximate") else "Mise en vente"
        embed.add_field(name=label, value=f"<t:{int(published)}:R> · <t:{int(published)}:f>", inline=False)
    else:
        embed.add_field(name="Mise en vente", value="Date non disponible — ancienneté non vérifiée", inline=False)
    if detected:
        embed.add_field(name="Détectée par le bot", value=f"<t:{int(detected)}:R>", inline=False)
    for label, value in (
        ("Marque", offer.get("marque") or offer.get("brand")),
        ("Taille", offer.get("taille") or offer.get("size")),
        ("État", offer.get("etat") or offer.get("condition")),
        ("Score", offer.get("score") or offer.get("match_score")),
    ):
        if value not in (None, ""):
            embed.add_field(name=label, value=str(value)[:1024], inline=True)
    image = str(offer.get("image") or offer.get("image_url") or "").strip()
    if image.startswith("https://"):
        embed.set_thumbnail(url=image)
    embed.set_footer(text=f"LUXE RADAR · {source} · vérifie l’état, les frais et la disponibilité")
    return embed


def _price_number(offer: dict) -> float | None:
    value = offer.get("prix") if offer.get("prix") is not None else offer.get("price")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"\d+(?:[.,]\d+)?", str(value or "").replace(" ", ""))
        if not match:
            return None
        number = float(match.group(0).replace(",", "."))
    return number if 0 < number <= 1_000_000 else None


def negotiation_plan(offer: dict) -> dict | None:
    """Prépare une proposition prudente ; aucun message n'est envoyé à Vinted."""
    price = _price_number(offer)
    if price is None:
        return None
    suggested = max(1, round(price * 0.90, 2))
    return {
        "price": price,
        "suggested": suggested,
        "alternatives": (max(1, round(price * 0.95, 2)), max(1, round(price * 0.85, 2))),
        "message": (
            "Bonjour, votre article m’intéresse. "
            f"Accepteriez-vous {suggested:.2f} € ? "
            "Je peux finaliser rapidement via Vinted. Merci !"
        ),
    }


class NegotiateButton(discord.ui.Button):
    def __init__(self, offer: dict, *, row: int):
        super().__init__(label="🤝 Négocier", style=discord.ButtonStyle.primary, row=row)
        self.offer = dict(offer)

    async def callback(self, interaction: discord.Interaction) -> None:
        plan = negotiation_plan(self.offer)
        url = discord_radar.safe_offer_url(self.offer)
        if not plan:
            await interaction.response.send_message(
                "Le prix de cette annonce n’est pas exploitable. Ouvre l’annonce pour proposer ton montant.",
                ephemeral=True,
            )
            return
        alternate_low, alternate_strong = plan["alternatives"]
        link_view = discord.ui.View()
        if url:
            link_view.add_item(discord.ui.Button(
                label="Ouvrir la discussion Vinted", style=discord.ButtonStyle.link, url=url,
            ))
        await interaction.response.send_message(
            f"🤝 **Proposition conseillée : {plan['suggested']:.2f} €** "
            f"(prix affiché : {plan['price']:.2f} €)\n"
            f"Autres choix : douce **{alternate_low:.2f} €** · forte **{alternate_strong:.2f} €**\n\n"
            f"Message prêt à copier :\n```\n{plan['message']}\n```\n"
            "Vérifie puis envoie toi-même l’offre sur Vinted.",
            view=link_view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


def _offers_view(offers: list[dict]) -> discord.ui.View:
    view = discord.ui.View(timeout=3600)
    for index, offer in enumerate(offers[:5], 1):
        url = discord_radar.safe_offer_url(offer)
        if url:
            view.add_item(discord.ui.Button(
                label=f"🛒 BUY #{index} · {offer.get('marketplace', 'Vinted')}",
                style=discord.ButtonStyle.link, url=url, row=index - 1,
            ))
            if "vinted." in url.lower():
                view.add_item(NegotiateButton(offer, row=index - 1))
    return view


async def _run_rules_bounded(rules, runner, concurrency: int = 3) -> int:
    """Traite toute une vague avec une concurrence strictement bornée."""
    semaphore = asyncio.Semaphore(max(1, min(int(concurrency), 2)))

    async def run_one(rule):
        async with semaphore:
            await runner(rule)

    if rules:
        await asyncio.gather(*(run_one(rule) for rule in rules))
    return len(rules)


class LuxeRadarDiscord(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.radar_store = discord_radar.RuleStore()
        self.personal_watch_worker = vinted_watch.WatchWorker(
            _scan, notifier=self._queue_personal_offer,
        )
        self._discord_loop = None
        self._fixed_radars_bootstrapped = False

    async def setup_hook(self) -> None:
        self._discord_loop = asyncio.get_running_loop()
        await self.tree.sync()
        self.channel_radars.start()
        self.ebay_radars.start()
        self.personal_watch_worker.start()

    async def close(self) -> None:
        self.channel_radars.cancel()
        self.ebay_radars.cancel()
        self.personal_watch_worker.stop_event.set()
        await super().close()

    def _queue_personal_offer(self, owner: str, offer: dict, query: str) -> None:
        """Planifie depuis le worker une notification privée Discord."""
        if not self._discord_loop or not owner.startswith("discord:"):
            return
        try:
            user_id = int(owner.removeprefix("discord:"))
        except ValueError:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_personal_offer(user_id, offer, query), self._discord_loop,
        )

    async def _send_personal_offer(self, user_id: int, offer: dict, query: str) -> None:
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            await user.send(
                content=f"🚨 Nouvelle annonce pour ton alerte **{discord.utils.escape_markdown(query)}**",
                embed=_offer_embed(offer), view=_offers_view([offer]),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            # L'annonce reste dans /alertes si les messages privés sont fermés.
            return

    @tasks.loop(seconds=5)
    async def channel_radars(self):
        # Deux workers au maximum : la file reste équitable sans saturer le PC.
        try:
            due = [r for r in self.radar_store.rules(due=True) if r["criteria"]["source"] == "Vinted"]
            concurrency = 2
            rules = due[:concurrency * 2]
            await _run_rules_bounded(
                rules,
                lambda rule: discord_radar.run_rule(
                    rule, self.radar_store, _scan, self.send_radar_offer,
                ),
                concurrency=concurrency,
            )
        except Exception as exc:
            print(f"[Radar Discord] {type(exc).__name__}")

    @tasks.loop(seconds=5)
    async def ebay_radars(self):
        # eBay n'attend pas les chargements navigateur de Vinted.
        try:
            for rule in [r for r in self.radar_store.rules(due=True) if r["criteria"]["source"] == "eBay"][:2]:
                await discord_radar.run_rule(rule, self.radar_store, _scan, self.send_radar_offer)
        except Exception as exc:
            print(f"[Radar eBay] {type(exc).__name__}")

    @ebay_radars.before_loop
    async def before_ebay_radars(self):
        await self.wait_until_ready()

    @channel_radars.before_loop
    async def before_channel_radars(self):
        await self.wait_until_ready()

    async def send_radar_offer(self, rule, offer):
        channel = self.get_channel(int(rule["channel_id"]))
        if channel is None or str(channel.guild.id) != rule["guild_id"]:
            raise LookupError("Salon inaccessible")
        await channel.send(
            embed=_offer_embed(offer), view=_offers_view([offer]),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_ready(self) -> None:
        await self._bootstrap_fixed_radars()
        print(f"[OK] Bot Discord connecté : {self.user}")

    async def _bootstrap_fixed_radars(self) -> None:
        """Restaure les radars fixes après un redémarrage à stockage éphémère."""
        if self._fixed_radars_bootstrapped:
            return
        guild_id = str(os.environ.get("LUXE_RADAR_DISCORD_GUILD_ID") or "").strip()
        if not guild_id.isdigit():
            self._fixed_radars_bootstrapped = True
            return
        guild = self.get_guild(int(guild_id))
        if guild is None:
            return
        from discord_server_cleanup import KEEP_RADARS
        from discord_server_setup import DEFAULT_RADARS
        channels = {channel.name: channel for channel in guild.text_channels}
        for name in KEEP_RADARS:
            channel = channels.get(name)
            if channel is not None:
                self.radar_store.save(
                    guild.id, channel.id, DEFAULT_RADARS[name], only_if_missing=True,
                )
        self._fixed_radars_bootstrapped = True


client = LuxeRadarDiscord()


@client.tree.command(name="radar", description="Créer ou remplacer ton Radar Vinted")
@app_commands.describe(
    produit="Produit, marque ou référence exacte",
    prix_max="Prix maximum en euros",
    prix_min="Prix minimum en euros",
    taille="Taille facultative",
    couleur="Couleur obligatoire facultative",
    etat="État obligatoire facultatif",
    mots_obligatoires="Mots séparés par des virgules",
    mots_exclus="Mots à refuser, séparés par des virgules",
    frequence="Intervalle en secondes (minimum 30)",
)
async def radar(
    interaction: discord.Interaction,
    produit: str,
    prix_max: app_commands.Range[float, 1, 1_000_000],
    prix_min: app_commands.Range[float, 0, 1_000_000] = 0,
    taille: str = "",
    couleur: str = "",
    etat: str = "",
    mots_obligatoires: str = "",
    mots_exclus: str = "",
    frequence: app_commands.Range[int, 30, 3600] = 30,
) -> None:
    if prix_min > prix_max:
        await interaction.response.send_message("Le prix minimum doit être inférieur au prix maximum.", ephemeral=True)
        return
    required_parts = [part.strip() for part in (mots_obligatoires, couleur, etat) if part.strip()]
    corrected_product = correct_query(produit)
    watch = vinted_watch.save_watch(discord_identity(interaction.user.id), {
        "query": corrected_product, "price_min": prix_min, "price_max": prix_max,
        "size": taille, "required": ",".join(required_parts),
        "excluded": mots_exclus, "interval": frequence,
    })
    await interaction.response.send_message(
        f"✅ Radar **{watch['query']}** enregistré · ≤ **{watch['price_max']} €**. "
        "Le bot le surveille automatiquement et t’enverra les nouveautés en message privé. "
        "Utilise `/scanner` pour chercher maintenant ou `/alertes` si tes messages privés sont fermés."
        + (f"\n🪄 Saisie reconnue : `{produit}` → `{corrected_product}`" if corrected_product.casefold() != produit.strip().casefold() else ""),
        ephemeral=True,
    )


@radar.autocomplete("produit")
async def radar_product_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    del interaction
    return [app_commands.Choice(name=item[:100], value=item[:100]) for item in suggestions(current)]


@client.tree.command(name="statut", description="Afficher l’état de ton Radar Vinted")
async def statut(interaction: discord.Interaction) -> None:
    watch = vinted_watch.get_watch(discord_identity(interaction.user.id))
    if not watch:
        await interaction.response.send_message("Aucun Radar configuré. Utilise `/radar`.", ephemeral=True)
        return
    state = "🟢 actif" if watch["active"] else "⏸️ en pause"
    await interaction.response.send_message(
        f"{state} · **{watch['query']}** · ≤ **{watch['price_max']} €** · toutes les **{watch['interval']} s** · {watch['unread']} alerte(s) non lue(s).",
        ephemeral=True,
    )


@client.tree.command(name="pause", description="Mettre ton Radar Vinted en pause")
async def pause(interaction: discord.Interaction) -> None:
    changed = vinted_watch.set_active(discord_identity(interaction.user.id), False)
    message = "⏸️ Radar mis en pause." if changed else "Aucun Radar à mettre en pause."
    await interaction.response.send_message(message, ephemeral=True)


@client.tree.command(name="reprendre", description="Réactiver ton Radar Vinted en pause")
async def reprendre(interaction: discord.Interaction) -> None:
    changed = vinted_watch.set_active(discord_identity(interaction.user.id), True)
    message = "🟢 Radar réactivé. Utilise `/scanner` pour chercher maintenant." if changed else "Aucun Radar à réactiver. Utilise `/radar`."
    await interaction.response.send_message(message, ephemeral=True)


@client.tree.command(name="supprimer", description="Supprimer ton Radar et son historique")
async def supprimer(interaction: discord.Interaction) -> None:
    changed = vinted_watch.delete_watch(discord_identity(interaction.user.id))
    message = "🗑️ Radar et historique supprimés." if changed else "Aucun Radar à supprimer."
    await interaction.response.send_message(message, ephemeral=True)


@client.tree.command(name="alertes", description="Voir les dernières annonces détectées")
async def alertes(
    interaction: discord.Interaction,
    nombre: app_commands.Range[int, 1, 10] = 5,
) -> None:
    offers = vinted_watch.alerts(
        discord_identity(interaction.user.id), mark_read=True, limit=nombre,
    )
    offers = [offer for offer in offers if quick_buy.safe_vinted_url(offer.get("lien") or offer.get("url"))]
    if not offers:
        await interaction.response.send_message("Aucune alerte enregistrée pour le moment.", ephemeral=True)
        return
    shown = offers[:5]
    await interaction.response.send_message(
        embeds=[_offer_embed(offer, index) for index, offer in enumerate(shown, 1)],
        view=_offers_view(shown), ephemeral=True,
    )


@client.tree.command(name="aide", description="Afficher les commandes de Luxe Radar")
async def aide(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="🤖 LUXE RADAR · Commandes", color=0xD4AF37)
    embed.description = (
        "`/surveiller` alertes automatiques Vinted/eBay dans ce salon (gestionnaire)\n"
        "`/arreter-surveillance` mettre ce salon en pause\n"
        "`/etat-surveillance` état des scans automatiques\n"
        "`/ebay` recherche eBay immédiate\n"
        "`/radar` créer ou modifier ton Radar Vinted personnel (accessible à tous)\n"
        "`/scanner` rechercher immédiatement\n"
        "`/alertes` consulter les dernières détections\n"
        "`/statut` afficher les réglages et alertes\n"
        "`/pause` suspendre les scans\n"
        "`/reprendre` relancer les scans\n"
        "`/supprimer` effacer le Radar et son historique"
    )
    embed.set_footer(text="Paiement sur la marketplace officielle · le bot doit rester en ligne")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="scanner", description="Scanner Vinted maintenant avec ton Radar")
async def scanner(interaction: discord.Interaction) -> None:
    watch = vinted_watch.get_watch(discord_identity(interaction.user.id))
    if not watch:
        await interaction.response.send_message("Configure d’abord ton Radar avec `/radar`.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        offers = await asyncio.to_thread(_scan, watch)
    except Exception:
        await interaction.followup.send("Vinted est temporairement indisponible. Réessaie dans quelques instants.", ephemeral=True)
        return
    if not offers:
        await interaction.followup.send("Aucune nouvelle annonce correspondante sur ce scan.", ephemeral=True)
        return
    shown = offers[:5]
    await interaction.followup.send(
        content=f"⚡ **{len(offers)} annonce(s) trouvée(s)** · voici les meilleures :",
        embeds=[_offer_embed(offer, index) for index, offer in enumerate(shown, 1)],
        view=_offers_view(shown), ephemeral=True,
    )


@client.tree.command(name="ebay", description="Chercher sur eBay : mode, sneakers ou processeurs")
@app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
async def ebay(
    interaction: discord.Interaction, produit: str,
    prix_max: app_commands.Range[float, 1, 1_000_000] = 300,
) -> None:
    query = correct_query(produit)
    if len(query.strip()) < 2:
        await interaction.response.send_message("Indique un produit ou une référence.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        offers = await asyncio.to_thread(_scan, {"source": "eBay", "query": query, "price_max": prix_max})
    except Exception:
        await interaction.followup.send("eBay indisponible : vérifie la configuration API et réessaie plus tard.", ephemeral=True)
        return
    if not offers:
        await interaction.followup.send("Aucune correspondance retournée par eBay sur ce scan.", ephemeral=True)
        return
    shown = offers[:5]
    await interaction.followup.send(
        content=f"**{len(offers)} annonce(s) eBay** · aperçu de {len(shown)} résultat(s)",
        embeds=[_offer_embed(offer, index) for index, offer in enumerate(shown, 1)],
        view=_offers_view(shown), ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )


@client.tree.command(name="surveiller", description="Envoyer automatiquement les nouvelles annonces dans ce salon")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.choices(source=[app_commands.Choice(name=s, value=s) for s in discord_radar.SOURCES])
async def surveiller(
    interaction: discord.Interaction, source: str, produit: str,
    prix_max: app_commands.Range[float, 1, 1_000_000] = 300,
    frequence: app_commands.Range[int, 120, 86400] = 120,
    anciennete_max: app_commands.Range[int, 1, 60] = 15,
    mots_exclus: str = "",
) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Utilise un salon textuel du serveur.", ephemeral=True)
        return
    permissions = interaction.channel.permissions_for(interaction.guild.me)
    if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
        await interaction.response.send_message("Le bot doit pouvoir voir ce salon, envoyer des messages et intégrer des liens.", ephemeral=True)
        return
    try:
        client.radar_store.save(interaction.guild_id, interaction.channel_id, {
            "source": source, "query": correct_query(produit), "price_max": prix_max,
            "interval": frequence, "excluded": mots_exclus[:120],
            "max_age_seconds": anciennete_max * 60,
        })
    except ValueError:
        await interaction.response.send_message("Indique un produit valide et une source Vinted ou eBay.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"✅ Alertes **{source}** configurées dans ce salon · ≤ **{prix_max} €**. "
        f"Intervalle minimum : **{frequence} s**, selon la disponibilité de la source. "
        f"Publication vérifiée de moins de **{anciennete_max} minutes** ; dates inconnues écartées. "
        "Maximum 5 annonces par passage, sans mention de rôle. Le bot doit rester en ligne.",
        ephemeral=True,
    )


@client.tree.command(name="arreter-surveillance", description="Mettre les alertes automatiques de ce salon en pause")
@app_commands.guild_only()
@app_commands.default_permissions(manage_channels=True)
@app_commands.checks.has_permissions(manage_channels=True)
async def arreter_surveillance(interaction: discord.Interaction) -> None:
    changed = client.radar_store.pause(interaction.guild_id, interaction.channel_id)
    await interaction.response.send_message("⏸️ Alertes en pause." if changed else "Aucune surveillance ici.", ephemeral=True)


@client.tree.command(name="etat-surveillance", description="Voir les critères et le dernier scan de ce salon")
@app_commands.guild_only()
async def etat_surveillance(interaction: discord.Interaction) -> None:
    rules = [r for r in client.radar_store.rules(interaction.guild_id) if r["channel_id"] == str(interaction.channel_id)]
    if not rules:
        await interaction.response.send_message("Aucune surveillance ici. Un gestionnaire peut utiliser `/surveiller`.", ephemeral=True)
        return
    rule = rules[0]
    criteria = rule["criteria"]
    await interaction.response.send_message(
        f"{'🟢 Active' if rule['active'] else '⏸️ En pause'} · **{criteria['source']}** · "
        f"{discord.utils.escape_markdown(criteria['query'])} · ≤ {criteria['price_max']} €\n"
        f"Intervalle minimum : {criteria['interval']} s · publication ≤ {criteria.get('max_age_seconds', 900) // 60} min\nDernier état : {rule['status']}",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )


ebay.autocomplete("produit")(radar_product_autocomplete)
surveiller.autocomplete("produit")(radar_product_autocomplete)


@client.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"Attends {int(error.retry_after) + 1} secondes avant le prochain scan."
    elif isinstance(error, app_commands.MissingPermissions):
        message = "La permission Gérer les salons est nécessaire."
    else:
        message = "La commande a échoué. Réessaie plus tard."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    load_dotenv()
    token = configured_token()
    if not token:
        raise SystemExit("LUXE_RADAR_DISCORD_BOT_TOKEN est manquant dans .env")
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
