# LUXE RADAR 3.7.0 — INSTANT SEARCH OS

LUXE RADAR centralise la recherche de produits mode et sneakers sur plusieurs sources, filtre le bruit, compare les prix et charge de nouvelles vagues au fil du scroll.

## V3.2.0 NOVA

- Interface entièrement remaniée : dock horizontal sur PC, navigation basse sur mobile, nouveau dashboard et nouveau Search Desk.
- Mode **Précis** par défaut : correspondances fortes + probables uniquement. Les annonces rejetées restent accessibles via le mode Explorer, mais ne polluent plus le flux principal.
- Mode **Ultra strict** : correspondances fortes uniquement.
- Mode **Explorer** : inclut volontairement les cartes « à vérifier », rangées après les meilleures correspondances.
- Bouton **Hors sujet** sur chaque carte : l’annonce est masquée localement et restaurable.
- **Comparateur live flottant** jusqu’à 4 annonces.
- Bloc **Pourquoi ce résultat ?** avec une explication courte de la correspondance.
- 9 couleurs d’accent, thème sombre / clair / automatique, vue grille / liste / dense.
- Lot eBay initial élargi pour les recherches de modèle précis afin d’éviter un premier écran de 2–3 cartes seulement.
- Ordre progressif recentré sur les sources retail/seconde main avant les grossistes très bruyants.
- Budgets Vinted bornés plus agressivement ; Zalando conserve son fast-fail sans contournement.
- Scroll infini et anti-spam 202/429 conservés.

## Sources

24 connecteurs actifs/configurés : eBay, Vinted, Zalando, ASOS, SSENSE, Cdiscount, Spartoo, Footshop, JD Sports, AliExpress, DHgate, 67behaviour, 1688, Grailed, i-Run, Direct Running, Alltricks, Deporvillage, Running Point, 21RUN, MisterRunning, Hardloop, Ekosport et Courir. Le catalogue exploratoire contient plus de 1 200 sites, distincts des connecteurs actifs.

Aucune annonce n’est fabriquée. Une source bloquée, modifiée ou nécessitant une connexion peut retourner 0 résultat. Aucun CAPTCHA, challenge ou contrôle d’accès n’est contourné.

## Render

Build command :

```text
python -m pip install -r requirements.txt && python -m playwright install chromium
```

Start command :

```text
gunicorn --config gunicorn.conf.py wsgi:application
```

Health check : `/api/health`  
Version : `/api/version`

## Secrets

Ne jamais committer `.env`. Les identifiants eBay/Stripe et la clé de session restent dans les variables d’environnement. Le checkout Stripe reste désactivé par défaut tant qu’un provisioning serveur complet n’est pas validé.


## V3.4.0
Architecture Gratuit / Premium / Pro, Radar automatique local (bêta honnête), billing désactivé par défaut et correction de migration uiVersion.



## V3.7.0 — Instant Search OS

- Le POST de recherche ne bloque plus le premier HTML sur eBay : une recherche froide ouvre immédiatement la page et les workers ajoutent les offres en arrière-plan.
- Toute offre fraîche déjà indexée est affichée immédiatement, même si le cache n'en contient qu'une seule.
- Nouveau catalogue global SQLite réutilisable : `Stone Island` pré-indexé peut aussi répondre à `Stone Island veste`, à une requête partielle et aux aides de recherche. FTS5 est utilisé quand SQLite le fournit, avec fallback LIKE.
- Aide à la recherche sur la barre globale, l'accueil et le Radar : corrections, modèles connus, suggestions issues de vraies offres indexées, compte d'offres et meilleur prix observé.
- Le prix maximum devient facultatif : le budget est un filtre, pas un prérequis.
- Routage intelligent des sources : running en priorité pour On/Asics/Hoka/Salomon…, mode/luxe en priorité pour Stone Island/Moncler/etc., tout en gardant les autres connecteurs accessibles.
- Garde-fou marque avant le mode recall : `River Island ... stone` n'entre plus dans une recherche Stone Island ; le garde-fou modèle exact Cloud 5 reste actif.
- Les redirections retail sont suivies uniquement sur le même domaine HTTP(S). Les placeholders JS invalides (`${searchAction}`) sont ignorés au lieu de provoquer une résolution DNS. Aucun contrôle d'accès n'est contourné.
- Migration automatique du cache exact V3.6 vers le catalogue global V3.7 quand c'est possible.
- Le volume affiché dépend uniquement des offres réelles déjà collectées. Pour obtenir des dizaines de pages, `warm_index.py --pages N` doit pré-indexer suffisamment de pages/sources ; aucune annonce n'est inventée.

## V3.6.0 — Index Engine

- Nouveau catalogue local indexé SQLite : une requête déjà chaude peut être rendue sans attendre aucune marketplace.
- Mode hybride introduit en V3.6 : index d’abord puis rafraîchissement des 24+ connecteurs. Depuis V3.7, un index froid ne bloque plus le HTML sur eBay : les sources travaillent toutes en arrière-plan.
- Jusqu’à 5 000 offres fraîches par requête dans le cache de recherche, soit 100 pages de 50 résultats.
- Chaque résultat live réel alimente automatiquement l’index ; aucune annonce n’est inventée.
- `warm_index.py` préchauffe Stone Island, Nike P-6000 et On Cloud 5 (ou toute autre requête) avant ouverture au public.
- `LUXE_RADAR_INDEX_DB` permet de placer la base SQLite sur un disque persistant Render ; sans disque, l’index repart à zéro après un redéploiement/redémarrage.
- `/api/index/status` expose uniquement les compteurs utiles, jamais le chemin local du serveur.


## V3.5.0 — Fashion Expansion

- 10 nouveaux marchands spécialisés configurés : i-Run, Direct Running, Alltricks, Deporvillage, Running Point, 21RUN, MisterRunning, Hardloop, Ekosport et Courir.
- 24 connecteurs actifs/configurés au total sur cette version.
- Ordonnancement fashion-first : spécialistes running/sneakers avant les sources généralistes/bruyantes.
- Connecteurs retail publics fail-fast : plusieurs routes publiques candidates, 2 maximum sur Render, cooldown si refus/403/429, aucun contournement.
- Garde-fou modèle exact : une recherche `On Cloud 5` élimine en amont `Cloud 6` et `Cloud X 5`.
- Accueil enrichi avec un bloc de marchands spécialisés, recentré sur vêtements, sneakers, running, streetwear, outdoor et luxe.
- Monétisation V3.4 conservée, paiements toujours désactivés par défaut.
