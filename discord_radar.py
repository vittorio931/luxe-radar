"""Scans Discord multi-source et règles de salons persistantes.

Les annonces viennent des connecteurs existants. Aucun achat automatique.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from urllib.parse import urlsplit
from uuid import uuid4

import quick_buy
from marketplaces.listing_dates import timestamp, is_recent

SOURCES = ("Vinted", "eBay")
CPU_EXAMPLES = (
    "AMD Ryzen 5 5600", "AMD Ryzen 5 5600X", "AMD Ryzen 5 7600",
    "AMD Ryzen 7 5700X3D", "AMD Ryzen 7 5800X3D", "AMD Ryzen 7 7800X3D",
    "Intel Core i5-12400F", "Intel Core i5-13600K", "Intel Core i7-12700K",
    "Intel Core i7-13700K", "Intel Core i9-13900K",
)


def normalise(value):
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def is_cpu_query(query):
    text = normalise(query)
    return bool(re.search(r"\b(cpu|processeurs?|processors?|ryzen|xeon|threadripper|intel|pentium|celeron|i[3579])\b|\b\d{4,5}(?:x3d|kf|ks|k|f|x)\b", text))


def cpu_matches(query, title):
    """Filtre conservateur pour processeurs seuls, référence suffixe inclus."""
    if not is_cpu_query(query):
        return True
    q, t = normalise(query), normalise(title)
    if not is_cpu_query(t):
        return False
    condition_text = re.sub(r"\b(sans defaut|aucun defaut|no defects)\b", "", t)
    # Les mini-PC n'emploient pas toujours le mot « PC » dans leur titre.
    # Exemple réel : « Mini Lenovo M715q Tiny AMD Ryzen ... RAM ... NVME ».
    if re.search(r"\b(thinkcentre|thinkpad|optiplex|elitedesk|prodesk|ideacentre|tiny|nuc|minipc)\b|\bmini\s+(lenovo|dell|hp|asus|acer)\b", t):
        return False
    if re.search(r"\b(ventirad|cooler|heatsink|watercooling|ventilateur|motherboard|carte mere|pate thermique|boite vide|empty box|box only|boite seule|emballage seul|pc gamer|pc complet|gaming pc|ordinateur|laptop|portable|mini pc|desktop|tour gaming|hs|defaut|defauts|defectueux|defect|defective|defekt|broken|for parts|pour pieces|ne fonctionne pas|not working|non fonctionnel)\b", condition_text) or re.match(r"pc\b", t):
        return False
    for brand in ("amd", "intel", "ryzen", "xeon", "threadripper"):
        if re.search(r"\b" + brand + r"\b", q) and not re.search(r"\b" + brand + r"\b", t):
            return False
    # 5600 != 5600X ; 7800X3D != 7800X. Ne corrige jamais ces suffixes.
    pattern = r"\b(\d{4,5})\s*(x3d|kf|ks|xt|k|f|x|g|t|u|h|hx)?\b"
    refs = {n + suffix for n, suffix in re.findall(pattern, q)}
    found = {n + suffix for n, suffix in re.findall(pattern, t)}
    if refs and not refs <= found:
        return False
    family = re.search(r"\b(i[3579]|ryzen\s+[3579])\b", q)
    return not family or bool(re.search(r"\b" + re.escape(family[0]) + r"\b", t))


def safe_offer_url(offer):
    value = str(offer.get("lien") or offer.get("url") or "").strip()
    source = offer.get("marketplace", "Vinted")
    if source == "Vinted":
        return quick_buy.safe_vinted_url(value)
    if source != "eBay":
        return ""
    try:
        parsed = urlsplit(value)
        domains = {"ebay.fr", "ebay.com", "ebay.de", "ebay.co.uk", "ebay.it", "ebay.es", "ebay.be", "ebay.nl"}
        host = (parsed.hostname or "").removeprefix("www.")
        if parsed.scheme != "https" or host not in domains or parsed.username or parsed.password or parsed.port not in (None, 443):
            return ""
        if not re.fullmatch(r"/itm/(?:[^/]+/)?\d+/?", parsed.path):
            return ""
        return value
    except ValueError:
        return ""


def offer_key(offer):
    url = safe_offer_url(offer)
    path = urlsplit(url).path
    pattern = r"/items/(\d+)" if offer.get("marketplace", "Vinted") == "Vinted" else r"/(\d+)/?$"
    match = re.search(pattern, path)
    return f"{offer.get('marketplace', 'Vinted')}:{match[1]}" if match else url


def scan(criteria, connector_factory=None):
    if connector_factory is None:
        from marketplaces.connectors import get_connector
        connector_factory = get_connector
    source = criteria.get("source", "Vinted")
    if source not in SOURCES:
        raise ValueError("Source inconnue")
    results = connector_factory(source).search(query=criteria["query"], price_max=criteria["price_max"], limit=50, page=1, newest_first=True) or []
    required = [normalise(w) for w in criteria.get("required", "").split(",") if w.strip()]
    excluded = [normalise(w) for w in criteria.get("excluded", "").split(",") if w.strip()]
    accepted, seen = [], set()
    for original in results:
        offer = dict(original, marketplace=source)
        try:
            price = float(offer.get("prix") if offer.get("prix") is not None else offer.get("price"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or not criteria.get("price_min", 0) <= price <= criteria["price_max"]:
            continue
        title = offer.get("titre") or offer.get("title") or ""
        text = normalise(" ".join(str(offer.get(k) or "") for k in ("titre", "title", "taille", "size", "etat", "condition")))
        size = normalise(criteria.get("size", ""))
        if size and size not in text or any(w not in text for w in required) or any(w in text for w in excluded):
            continue
        cpu_text = f"{title} {offer.get('etat') or offer.get('condition') or ''}"
        if not cpu_matches(criteria["query"], cpu_text) or not safe_offer_url(offer):
            continue
        key = offer_key(offer)
        if key in seen:
            continue
        seen.add(key)
        offer.update(prix=price, titre=title, detected_at=time.time())
        accepted.append(offer)
    return accepted


def enrich_dates(offers):
    pending = [offer for offer in offers[:5] if offer.get("marketplace") == "Vinted" and timestamp(offer.get("listed_at")) is None]
    if pending:
        from marketplaces.connectors import get_connector
        try:
            get_connector("Vinted").enrich_listing_dates(pending)
        except Exception:
            pass  # Une date inconnue reste inconnue ; aucune fraîcheur inventée.
    return offers


class RuleStore:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("DISCORD_RADAR_DB") or Path(__file__).parent / "instance" / "discord_radar.sqlite3")

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS channel_rules (
                    channel_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL,
                    revision TEXT NOT NULL, criteria TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, next_run REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'En attente', last_run REAL
                );
                CREATE TABLE IF NOT EXISTS sent_offers (
                    channel_id TEXT NOT NULL, offer_key TEXT NOT NULL,
                    PRIMARY KEY(channel_id, offer_key)
                );
                CREATE TABLE IF NOT EXISTS deferred_offers (
                    channel_id TEXT NOT NULL, offer_key TEXT NOT NULL,
                    retry_after REAL NOT NULL,
                    PRIMARY KEY(channel_id, offer_key)
                );
            """)
            yield db
            db.commit()
        finally:
            db.close()

    def save(self, guild_id, channel_id, criteria, *, only_if_missing=False):
        source = criteria.get("source", "Vinted")
        query = str(criteria.get("query", "")).strip()[:120]
        maximum = float(criteria.get("price_max", 0))
        if source not in SOURCES or len(query) < 2 or not math.isfinite(maximum) or not 0 < maximum <= 1_000_000:
            raise ValueError("Critères invalides")
        clean = dict(criteria, source=source, query=query, price_max=maximum,
                     interval=max(120, min(int(criteria.get("interval", 120)), 86400)),
                     max_age_seconds=max(60, min(int(criteria.get("max_age_seconds", 900)), 3600)))
        with self.connect() as db:
            if only_if_missing and db.execute("SELECT 1 FROM channel_rules WHERE channel_id=?", (str(channel_id),)).fetchone():
                return False
            db.execute("""INSERT INTO channel_rules(channel_id,guild_id,revision,criteria) VALUES(?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET guild_id=excluded.guild_id, revision=excluded.revision,
                criteria=excluded.criteria,active=1,next_run=0,status='En attente',last_run=NULL""",
                (str(channel_id), str(guild_id), uuid4().hex, json.dumps(clean)))
        return True

    def rules(self, guild_id=None, *, due=False):
        with self.connect() as db:
            clauses, args = [], []
            if guild_id is not None:
                clauses.append("guild_id=?")
                args.append(str(guild_id))
            if due:
                clauses.append("active=1 AND next_run<=?")
                args.append(time.time())
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute("SELECT * FROM channel_rules" + where + " ORDER BY next_run,channel_id", args).fetchall()
        return [dict(row, criteria=json.loads(row["criteria"])) for row in rows]

    def current(self, rule):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM channel_rules WHERE channel_id=? AND revision=? AND active=1",
                                   (rule["channel_id"], rule["revision"])).fetchone())

    def pause(self, guild_id, channel_id):
        with self.connect() as db:
            return bool(db.execute("UPDATE channel_rules SET active=0,status='En pause' WHERE guild_id=? AND channel_id=?",
                                   (str(guild_id), str(channel_id))).rowcount)

    def delete_channel(self, guild_id, channel_id):
        """Supprime une règle de salon et son historique local associé."""
        guild_id, channel_id = str(guild_id), str(channel_id)
        with self.connect() as db:
            exists = db.execute(
                "SELECT 1 FROM channel_rules WHERE guild_id=? AND channel_id=?",
                (guild_id, channel_id),
            ).fetchone()
            if not exists:
                return False
            db.execute("DELETE FROM sent_offers WHERE channel_id=?", (channel_id,))
            db.execute("DELETE FROM deferred_offers WHERE channel_id=?", (channel_id,))
            db.execute(
                "DELETE FROM channel_rules WHERE guild_id=? AND channel_id=?",
                (guild_id, channel_id),
            )
        return True

    def seen(self, channel_id, offer):
        with self.connect() as db:
            key = (str(channel_id), offer_key(offer))
            sent = db.execute("SELECT 1 FROM sent_offers WHERE channel_id=? AND offer_key=?", key).fetchone()
            deferred = db.execute("SELECT 1 FROM deferred_offers WHERE channel_id=? AND offer_key=? AND retry_after>?", (*key, time.time())).fetchone()
            return bool(sent or deferred)

    def defer_offer(self, channel_id, offer):
        # Un article ancien ne rajeunit pas ; une date manquante est réessayable.
        delay = 86400 if timestamp(offer.get("listed_at")) is not None else 30
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO deferred_offers VALUES(?,?,?)",
                       (str(channel_id), offer_key(offer), time.time() + delay))

    def mark_sent(self, channel_id, offer):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO sent_offers VALUES(?,?)", (str(channel_id), offer_key(offer)))

    def finish(self, rule, status, *, failed=False):
        delay = max(rule["criteria"]["interval"], 900 if failed else 120)
        with self.connect() as db:
            db.execute("UPDATE channel_rules SET status=?,last_run=?,next_run=? WHERE channel_id=? AND revision=?",
                       (status[:160], time.time(), time.time() + delay, rule["channel_id"], rule["revision"]))


async def run_rule(rule, store, scanner, sender, date_enricher=enrich_dates):
    """Un scan à la fois ; on confirme le dédoublonnage après l'envoi réussi."""
    import asyncio
    try:
        offers = await asyncio.to_thread(scanner, dict(rule["criteria"], _automatic=True))
        if not store.current(rule):
            return
        candidates = [offer for offer in offers if not store.seen(rule["channel_id"], offer)]
        sent = ignored = 0
        # Dates connues (eBay) : écarter les anciennes avant le lot de cinq.
        undated_or_recent = []
        max_age = rule["criteria"].get("max_age_seconds", 900)
        for offer in candidates:
            if timestamp(offer.get("listed_at")) is not None and not is_recent(offer, max_age):
                store.defer_offer(rule["channel_id"], offer)
                ignored += 1
            else:
                undated_or_recent.append(offer)
        candidates = undated_or_recent[:5]
        await asyncio.to_thread(date_enricher, candidates)
        for offer in candidates:
            if not store.current(rule):
                return
            if store.seen(rule["channel_id"], offer):
                continue
            published = timestamp(offer.get("listed_at"))
            # Au tout premier passage, une date absente n'est pas une preuve de
            # nouveauté. Après ce passage de référence, une clé jamais vue est
            # bien une nouvelle détection et peut être signalée sans inventer
            # une heure de publication.
            if published is None and rule.get("last_run") is None:
                store.defer_offer(rule["channel_id"], offer)
                ignored += 1
                continue
            if published is not None and not is_recent(offer, max_age):
                store.defer_offer(rule["channel_id"], offer)
                ignored += 1
                continue
            await sender(rule, offer)
            store.mark_sent(rule["channel_id"], offer)
            sent += 1
            if sent >= 5:
                break
        store.finish(rule, f"{len(offers)} résultat(s), {sent} alerte(s) récente(s), {ignored} ancienne(s)/date inconnue" if offers else "Aucune correspondance retournée (vide ou source indisponible)")
    except Exception as exc:
        store.finish(rule, f"Erreur {type(exc).__name__} — nouvel essai différé", failed=True)
