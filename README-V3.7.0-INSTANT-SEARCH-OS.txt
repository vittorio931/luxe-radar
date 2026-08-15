LUXE RADAR V3.7.0 — INSTANT SEARCH OS

Objectif
- rapprocher l’expérience de recherche d’un comparateur de prix moderne sans copier son identité visuelle ;
- rendre la page de résultats immédiatement, puis actualiser les marchands en arrière-plan ;
- réutiliser un vrai catalogue global indexé au lieu d’un simple cache par requête.

Changements principaux
- Catalogue global SQLite + FTS5/fallback LIKE ; réutilisation entre requêtes et raffinements.
- Premier HTML jamais bloqué par eBay ou une marketplace.
- Résultats indexés affichés dès le premier rendu, même avec un petit cache.
- Autocomplétion sur header + accueil + Radar avec corrections, modèles, compte d’offres et prix observé.
- Budget maximum optionnel.
- Priorité des sources selon l’intention running / mode-luxe / générique.
- Filtre marque strict avant recall et filtre modèle exact conservé.
- Redirections retail same-domain uniquement ; aucune tentative de contourner 403/CAPTCHA/login.
- Migration du cache V3.6 vers le catalogue global V3.7.

Important
Le moteur peut servir jusqu’à 5 000 offres par requête, mais il ne crée jamais de résultats. Un catalogue de 60+ pages n’existe que si les workers ont réellement collecté assez d’offres. En production, utiliser LUXE_RADAR_INDEX_DB sur un stockage persistant si disponible afin que l’index survive aux redémarrages. Stripe reste OFF par défaut.
