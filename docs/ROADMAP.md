# ROADMAP.md — Desired product evolution

## État au 14 août 2026

Les priorités A et B sont implémentées : lots réglables (50 / 100 / 200),
scroll automatique, déduplication, tri sans relancer les connecteurs, filtres
(prix min, prix max, prix exact avec tolérance ± 0 / ± 2 / ± 5 €, marketplace,
mots exclus) et comptage affiché/trouvé. Si le chargement automatique est
désactivé, un bouton permet toujours de charger le lot suivant et le réglage
peut être réactivé sans recharger la page. Le comparateur de la priorité D accepte
jusqu’à quatre annonces, montre uniquement les champs disponibles (prix, total,
score, confiance, état, taille et vendeur) et marque les minima/maxima comparables
sans inventer une valeur manquante. Un explorateur séparé expose désormais le
catalogue de 1 218 sites par lots de 50, avec filtres de statut et de catégorie,
sans activer les sites non testés. Les sections ci-dessous restent les critères
de non-régression et les pistes d’approfondissement.

La priorité E (recherche par image) est implémentée de bout en bout : le moteur
de similarité d’image existant est de nouveau branché dans l’interface via le
bouton « Rechercher par image » de la barre de filtres. L’image choisie est
comparée aux photos des annonces déjà trouvées, les résultats sont re-triés par
ressemblance et chaque carte affiche son degré « Image X % ».

Le paiement Stripe (Checkout en mode abonnement) est branché côté serveur via
`billing_stripe.py` (API HTTP directe, sans dépendance) : les prix sont créés
automatiquement au premier appel avec un `lookup_key` stable. Il reste inactif
tant que `STRIPE_SECRET_KEY` n’est pas renseignée dans `.env`. Les webhooks de
confirmation et le verrouillage des fonctionnalités côté serveur restent des
pistes futures.

La priorité C est désormais implémentée : une référence exacte optionnelle est
normalisée sans tenir compte de la casse et des séparateurs, ajoutée à la requête
des connecteurs si nécessaire, puis utilisée soit comme boost stable, soit comme
filtre strict. Le score original des annonces n’est jamais réécrit.
Le boost ne permet pas non plus à une annonce « A IGNORER » ou douteuse de
passer devant une catégorie saine uniquement grâce à sa référence.

Les alertes locales comptent aussi les correspondances présentes dans les
résultats déjà affichés. Elles ne lancent volontairement aucun scan caché : le
libellé précise que ce comptage porte uniquement sur le lot actuellement chargé.
Les collections sont des instantanés explicitement nommés des favoris courants ;
les doublons de nom et collections vides sont refusés. Le suivi de prix et
l’inventaire bornent et valident désormais chaque valeur avant stockage local.
L’inventaire accepte une recherche par nom et un filtre par statut, tout en
conservant les totaux globaux investis et réalisés.

## Priority A — Large result browsing
Goal: stop behaving like a tiny TOP-10 page.

Desired UX:
- load 30–50 best results first;
- user scrolls down;
- automatically append the next chunk;
- continue until no more available results;
- best results appear first;
- lower quality/relevance results remain lower in the list rather than being hidden.

Implementation guidance:
- do not fetch thousands of listings synchronously in one Flask request;
- use batching/pagination;
- cache search state when useful;
- expose a lightweight JSON "load more" route if needed;
- keep UI responsive.

## Priority B — Sorting and filters after search
Add client-side or server-assisted controls for:
- best match;
- price ascending;
- price descending;
- best deal score;
- confidence;
- marketplace;
- possibly newest if connector data supports it.

Price filters:
- maximum price;
- minimum price;
- exact price;
- exact price tolerance, e.g. ±0 / ±2 / ±5 EUR.

Marketplace filters:
- toggle eBay, Vinted, Grailed, 67behaviour, etc.

Do not re-scrape all marketplaces for every trivial sort change if the current result set already contains the needed data.

## Priority C — Exact reference search (implémentée)
Add a dedicated field:
`Référence exacte`

Examples:
- `DM4652-040`
- `FQ3916-010`

Modes:
1. normal search + reference boost;
2. strict exact-reference mode.

Exact reference matching should be normalized safely:
- case-insensitive;
- tolerate harmless separators/spaces if configured;
- never convert it into broad unrelated keyword matching.

The UI should clearly show when strict reference mode is active.

## Priority D — Product comparator
Allow selecting 2–4 listings.

Comparison columns:
- image;
- marketplace;
- title;
- price;
- shipping;
- total price;
- condition;
- size if available;
- seller/rating if available;
- radar score;
- confidence;
- deal score;
- alerts;
- direct listing link.

Highlight:
- cheapest;
- best total price;
- best confidence;
- best radar score.

Do not claim "best" on missing/non-comparable values.

## Priority E — Search by image
Goal:
user uploads a product photo and LUXE RADAR ranks candidate listings by visual similarity.

Suggested architecture:
1. secure upload endpoint;
2. validate MIME/size and store temporarily;
3. extract a visual embedding using a suitable local model;
4. obtain candidate listing images from working marketplaces;
5. compute embeddings for candidate images;
6. cosine-similarity ranking;
7. optionally combine visual similarity with text/reference/price relevance;
8. cache embeddings to avoid repeated downloads/computation.

Important:
- image search should rank candidates from marketplaces the app can already access;
- it is not a magical unrestricted web reverse-image search;
- handle remote image download failures safely;
- set timeouts and file size limits;
- do not execute uploaded files;
- delete temporary uploads when appropriate.

Potential future scoring:
`final_score = text_match + visual_similarity + deal_score + confidence`
with explicit weights and tests.

## Priority F — Dynamic marketplace UI
Avoid hard-coded marketplace dropdown entries.
Prefer generating active choices from the connector registry.

" Toutes " should automatically include active connectors.

## Priority G — Performance
Measure before optimizing.

Likely improvements:
- concurrent marketplace searches where safe;
- per-connector timeout;
- cached FX rates;
- cached search results;
- pagination;
- avoid opening one browser/product page per result;
- lazy image loading;
- browser lifecycle reuse only if stable.

Never trade correctness/access compliance for scraping speed.

## Priority H — More marketplaces
Add one at a time.
For each:
1. probe compliant public access;
2. implement;
3. normalize;
4. test real search;
5. enable only after real success.

Status:
- SSENSE (dedicated HTTP connector, JSON-LD): implemented, real-tested and active since 2026-08-14 (46 candidates on `Arc'teryx`, 25 in the TOP 50).
- Depop: probed 403 -> remains OFF (no bypass attempted).
- DHgate / GOAT / StockX: probed blocked or 403 -> remain OFF.

Do not claim "all marketplaces" literally.
