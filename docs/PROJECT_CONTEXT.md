# PROJECT_CONTEXT.md — LUXE RADAR

## 1. What the project is
LUXE RADAR is a local Flask application that searches multiple marketplaces for fashion, sportswear, sneakers, vintage and luxury products.

Local app:
`http://127.0.0.1:5000`

Project directory:
`C:\Users\aless\vinted-radar ancien`

The user wants the project to evolve from a small "top deals" page into a broad multi-marketplace shopping/search engine and comparator.

## 2. Main files
Core:
- `app_web.py`
- `radar_engine.py`
- `modeles.py`
- `templates/index.html`

Marketplace infrastructure:
- `marketplaces/registry.py`
- `marketplaces/sites.json`
- `marketplaces/connectors/base.py`
- `marketplaces/connectors/__init__.py`
- `marketplaces/connectors/quality_filters.py`
- `marketplaces/connectors/universal.py`

Dedicated connectors include:
- `marketplaces/connectors/vinted.py`
- `marketplaces/connectors/ebay.py`
- `marketplaces/connectors/behaviour67.py`
- `marketplaces/connectors/grailed.py`
- `marketplaces/connectors/vestiaire.py`
- `marketplaces/connectors/ali1688.py`

Manager:
- `luxe_radar_manager.py`

## 3. Manager role
`luxe_radar_manager.py` is the project safety/maintenance tool.

Known commands include:
- `setup`
- `doctor`
- `test`
- `backup`
- `backups`
- `sites`
- `sync-registry`
- `run`
- `restore`
- `probe`
- `add-site`
- `enable-site`
- `disable-site`
- `remove-site`
- `network-test`

It can:
- create backups;
- compile Python;
- run import/smoke checks;
- sync registry;
- discover connectors;
- run the app;
- restore backups;
- probe/configure generic sites.

Use it instead of bypassing it when practical.

## 4. Existing connector interface
The shared connector concept is roughly:

```python
class MarketplaceConnector:
    name = "Marketplace"

    def search(self, query, price_max, limit=20):
        raise NotImplementedError

    @staticmethod
    def normalize(item):
        return {
            "marketplace": item.get("marketplace"),
            "title": item.get("title", ""),
            "brand": item.get("brand"),
            "model": item.get("model"),
            "price": item.get("price"),
            "shipping": item.get("shipping"),
            "condition": item.get("condition"),
            "size": item.get("size"),
            "image": item.get("image"),
            "url": item.get("url"),
            "seller": item.get("seller"),
            "seller_rating": item.get("seller_rating"),
        }
```

In practice, legacy engine-facing fields also commonly include:
- `titre`
- `prix`
- `lien`
- `image`
- `score`
- `score_match`
- `score_confiance`
- `score_affaire`
- `categorie`
- `alertes`
- `raisons`

Be careful about field aliases and normalize consistently.

## 5. Current working marketplace state

### Vinted
Uses Playwright/search logic and feeds the universal engine.
It has existing relevance/type filters and low-price safeguards.

### eBay
Production OAuth works.
Secrets live in `.env` and must never be exposed.

Real direct test for query `Nike Trail`, `price_max=50`, `limit=50`:
- 167 listings received;
- 50 retained internally;
- 45 returned through the quality wrapper.

Examples included relevant Nike Trail shirts, shorts, pants and trail shoes.

Real direct test for query `Nike`, `price_max=50`, `limit=50`:
- 200 listings received;
- 50 retained;
- 50 returned.

Conclusion:
eBay connector volume is healthy.
If eBay is scarce in "Toutes", investigate global ranking/display limits rather than assuming eBay search is broken.

Le plafond interne a ensuite été relevé de 50 à 100 résultats par recherche.
Cette borne reste volontairement inférieure au maximum de 200 candidats demandé
à l’API : elle augmente la profondeur de la liste sans charger des milliers
d’annonces dans une requête.

Test réel après ce changement (`Nike Trail`, 50 EUR, limite 100) : 165 annonces
reçues, 74 retenues par le connecteur puis 63 renvoyées après le filtre qualité,
avec 63 URL uniques, aucune URL vide, aucun « Trail Blazers » et aucun prix
supérieur à 50 EUR. Ce nombre dépend naturellement de l’inventaire en direct.

### 67behaviour
Old browser-heavy connector was replaced by an HTTP-first implementation.

Real test:
- 28 product links found through HTTP;
- 16 results retained for `Nike Trail`.

Examples included Nike Dri-FIT Trail Running T-Shirt and Nike Juniper Trail 2.

### Grailed
Grailed public browse pages can show a dynamic "Loading the Feed" skeleton.
Headless Chromium receives a challenge on tested routes.

The current successful approach:
- generate candidate public browse routes;
- validate routes;
- try headless;
- if challenged/empty, fall back to visible Chromium;
- read listing cards directly from the rendered feed;
- filter cards locally;
- avoid visiting dozens of individual product pages.

Real successful test for `Nike Trail`, `price_max=50`, `limit=10`:
- visible browser loaded 20 pertinent cards;
- 10 results retained;
- TOTAL: 10.

Examples:
- Nike Trail Flex Stride Dri-FIT Running Shorts
- Nike Trail Running Dri-Fit T-Shirt
- Nike Trail ADV Second Sunrise shorts
- Nike ACG Trail shorts

This is the first confirmed working Grailed version. Do not regress it to the slower product-page-per-link approach unless necessary.

### SSENSE
HTTP-only connector, no browser needed.
Products are read from the public JSON-LD markup on `https://www.ssense.com/en-fr/men?q=...`.

Real verified facts (2026-08-14):
- the public search is genuinely query-sensitive ("nike trail" returned only Nike trail items, "arc'teryx" only Arc'teryx items), contrary to an older note in `sites.json`;
- prices are native EUR with a product SKU reference in the JSON-LD;
- titles are prefixed with the brand because SSENSE product names do not include it;
- images use Cloudinary URLs containing a `__IMAGE_PARAMS__` placeholder that the connector replaces with `f_auto,q_auto,w_900` (verified HTTP 200);
- the `Accept` request header must be omitted: SSENSE serves a variant without SKUs when it is present.

Real engine test (`Arc'teryx`, 400 EUR, all marketplaces):
- 46 SSENSE candidates -> 25 in the TOP 50, balanced with eBay;
- global sweep completed in ~24 s.

Note: most SSENSE retail prices exceed 50 EUR, so a typical 50 EUR budget search will legitimately return 0 SSENSE results.

## 6. Known ranking issue
`radar_engine.py` contains `_selection_diversifiee`.

The current logic applies:
- when `diversifie=False` (default for the global ranking): pure score order — best matches first whatever the marketplace;
- when `diversifie=True`: very light departure only in the first 50 items (~0.05 score per listing beyond 7 for a marketplace, capped at 0.25).

No result is ever discarded; the underlying scores are preserved.

Status: the diversification is now optional and disabled by default in `rechercher_multi_marketplaces` so that a strong marketplace (e.g. eBay) can keep many listings in the results. Marketplace variety is available through the post-search marketplace filter.

## 7. Current result-limit issue
`rechercher_multi_marketplaces` historically defaults to a small `limite` (around 10).
It retrieves more candidates per marketplace using a multiplier, then returns only the final limited selection.

The desired redesign is NOT "request infinite results all at once".
Instead:
- retrieve reasonable batches;
- cache/session-store the current search result set;
- render an initial chunk;
- load more on scroll;
- eventually request additional marketplace pages/batches if connectors support pagination.

Status: the initial chunk and the scroll batches are now configurable via the
`lots` POST parameter and the `limit` query parameter (50 / 100 / 200, capped at
`MAX_BATCH_SIZE = 200`). The post-search endpoint `/api/results/<token>` also
accepts an exact price with a tolerance: `price_exact` (0–1 000 000) combined
with `price_tolerance` (0–100 €, e.g. 0 / 2 / 5). The `/api/results/<token>/image-rank`
endpoint re-sorts the cached results by image similarity and is exposed in the UI
through the « Rechercher par image » / « Classer par image » controls.

## 8. Quality filters / false positives
Known concern:
keyword stuffing can create false positives.

Historical eBay example:
`tshirt nike trail NEW hoka under adidas L asics puma armour balance timberland`

Quality filters were added to reduce these.

Also reject obvious unrelated "Portland Trail Blazers" false positives for searches such as Nike Trail.

Do not loosen relevance so much that unrelated listings flood results.

## 9. Access / anti-bot policy
Do not bypass:
- CAPTCHA;
- login wall;
- access-control page;
- 403 block;
- private endpoint restrictions.

Previous probes:
- Depop homepage returned 403 -> leave generic integration OFF.
- Vestiaire Collective returned 403 -> OFF unless a compliant public/official method is found.
- StockX returned 403 in a probe -> do not bypass.
- GOAT homepage returned 200 but search was not proven.
- ASOS probe timed out (early probe); the dedicated ASOS connector is now active. Images come from the embedded JSON state (`window.asos.plp`) because most product cards are lazy-loaded without a server-side `<img src>`.
A homepage 200 does not prove search integration works.

## 10. Secrets / security
- `.env` exists.
- Do not display `.env`.
- Do not commit `.env`.
- Never print eBay client secret / OAuth credentials.
- Flask dev server is for local development only.
- Later production hardening may use a production WSGI server, authentication, validation, CSRF/rate limits, HTTPS/reverse proxy, secure headers and logging.

## 11. User's preferred workflow
The user wants the agent to do as much as possible itself:
- inspect files;
- edit files;
- run commands;
- compile;
- test;
- diagnose failures;
- fix failures;
- keep backups.

Avoid making the user manually copy many tiny patches.
Prefer full coherent edits and automatic validation.

## 12. Important Windows detail
If PowerShell already shows:
`PS C:\Users\aless\vinted-radar ancien>`
the user is already in the project folder.

Do not tell them to type:
`C:\Users\aless\vinted-radar ancien`
as a bare command.

Correct navigation from elsewhere:
`cd "C:\Users\aless\vinted-radar ancien"`

Correct launch:
`.\.venv\Scripts\python.exe app_web.py`

## 13. Web-app vs collecteur (architecture)

Deux rôles distincts, aujourd'hui regroupés dans le même processus, mais
conceptuellement séparés pour la suite :

### Web-app (le rôle rapide)
- Répond vite aux requêtes de l'utilisateur : rendu de la page, POST de
  recherche, polling des résultats, filtres, tri, comparaison.
- Sur Render : un seul processus `gunicorn` (`WEB_CONCURRENCY=1`) qui doit
  rester léger et réactif. `app_web.py` ne doit jamais faire de travail lent
  dans le chemin de requête (pas de scraping synchrone dans le render).

### Collecteur (le rôle lent)
- Fait le travail de recherche/collecte en arrière-plan : interroger chaque
  marketplace, lancer Chromium quand nécessaire, parser les cartes, appliquer
  les filtres de pertinence/qualité.
- Coûts typiques observés en production : eBay ~20-50 s réseau, AliExpress
  ~30-45 s, Vinted ~34 s (démarrage Chromium + navigation bornée), une vague
  de recherche progressive ~80-100 s au froid.

### État actuel (V4, recherche progressive)
- `app_web.py` lance la recherche en arrière-plan progressif : les sources
  sont ordonnées par intention de l'utilisateur + santé de la source
  (`_progressive_source_order`), exécutées par des workers
  (`LUXE_RADAR_PROGRESSIVE_WORKERS`, 4 sur Render), sous un sémaphore Chromium
  partagé (`_browser_progressive_semaphore`, 1 navigateur à la fois pour
  Vinted/Grailed).
- `marketplaces/source_health.py` est le registre de santé/cooldown : une
  source bloquée (403/429/challenge) ou vide-lente répétée est mise en
  cooldown (aucun worker consommé) ; en production les sources connues
  bloquées sont pré-déclarées via `LUXE_RADAR_PRESEED_BLOCKED`.
- Les résultats sont stockés en mémoire par token (cache progressif) puis
  persistés dans l'index local par un thread d'arrière-plan
  (`_index_results_async`, `_persist_index`) ; `warm_index` pré-remplit l'index
  au démarrage.

### Direction (séparation future)
- Le web-app doit devenir une lecture rapide sur un stockage/index persistant
  partagé (offres déjà collectées), pendant que le collecteur — processus ou
  worker asynchrone distinct — fait le travail lent et alimente ce stockage.
- Les deux rôles ne doivent pas tourner dans le même chemin de requête : si la
  collecte lente bloque le processus web, l'utilisateur attend et Render
  signale une instance non réactive.
- Une source lente ou vide ne doit jamais retarder le rendu des premiers
  résultats pertinents (le cooldown vide-lent et le preseed protègent déjà ce
  contrat en V4).

## 14. Collecteur de catalogue profond (V3.8)

Objectif : passer d'un catalogue "fin de la vague" à une profondeur réelle par
source, hors hot path, sans jamais inventer d'annonce.

### Architecture
- `collector.py` : orchestrateur en thread daemon (démarré par `wsgi.py` en
  prod et par `app_web.py` en local). S'il meurt ou n'est pas démarré, la web
  app continue de servir l'index déjà persisté sur disque : la lecture ne
  dépend jamais du collecteur.
- Seeds par défaut (recherches populaires du cahier des charges) configurables
  via `LUXE_RADAR_COLLECTOR_SEEDS` (`"query|prix"` ou JSON `[[q,prix]]`) ;
  fraîcheur de re-collecte via `LUXE_RADAR_COLLECTOR_FRESHNESS_SECONDS`.
- Marche par source : page 1..N en appelant directement le connecteur
  (`search_page`), avec `page_size` profond par source (eBay 200/page car le
  clamp du connecteur a été relevé de 100 à 200 et le pageSize API à 200 ;
  Vinted 50 ; Zalando 100 ; retail paginé 100 ; non paginé = taille
  `expansion_page_size`). Arrêt sur `max_pages`, sur seuil de pages vides
  consécutives ou sur budget temps (`LUXE_RADAR_COLLECTOR_SEED_BUDGET_SECONDS`).
- Déduplication stable : clé `offer_key` identique à l'index live, croisée
  contre `indexed_results(query)` et `catalogue` via `known_offer_keys`.
- Cooldowns : les sources en cooldown (source_health) sont sautées ; chaque
  passe alimente `source_health.registry.record_outcome` pour que le vide lent
  et les blocages soient appris exactement comme dans le chemin live.
- Traçabilité : chaque page écrit une ligne `collector_runs` dans l'index DB
  (raw/parsed/relevant/rejected/new/duplicates/has_more/blocked/latency).
  `index_engine.collector_stats()` agrège ; `count_query_offers()` donne le
  avant/après par seed pour le benchmark.

### Web-app
- `/api/collector/status` : queue, passe en cours, dernières exécutions, totaux.
- Pagination profonde : quand un token utilisateur est épuisé mais que l'index
  persistant a encore des offres (`index_total > len(token)`), `_result_page`
  continue sur l'index (`_index_spillover`), dédupliqué contre les clés déjà
  affichées.
- Vague déclenchée par l'utilisateur : quand l'utilisateur approche de la fin
  (`more_results`), `_trigger_collector_wave` enfile le seed courant (dédupe
  interne de fenêtre `LUXE_RADAR_COLLECTOR_TRIGGER_WINDOW_SECONDS`).

### CLI
- `python collector.py --seed "Nike P-6000|250" --dry-run` : audit de
  profondeur sans écrire (raw/parsed/relevant/new par page et par source).
- `python collector.py --stats` / `manager collect --stats` : totaux.
- `manager collect --seed "On Cloud 5|250" --sources "eBay"` : collecte réelle.
- Sur Render, l'index n'est pas sur disque persistant (plan free) : le
  collecteur ré-ensemence à chaque déploiement, ce qui joue le rôle de la
  pré-indexation au démarrage. En local l'index est réellement persistant.

