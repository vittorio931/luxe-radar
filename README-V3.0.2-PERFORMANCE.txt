LUXE RADAR V3.0.2 — PERFORMANCE HOTFIX

Objectif : empêcher Zalando de bloquer le scroll infini pendant 30–40 secondes.

Changements :
- Zalando : timeout connexion 2,5 s / lecture 6 s.
- Aucun retry HTTP automatique Zalando.
- Circuit breaker temporaire après timeout/réseau (2 min) ou HTTP 403/429 (10 min).
- Aucun contournement de CAPTCHA/403/anti-bot.
- Zalando passe après les sources HTTP les plus stables dans le pipeline initial.
- Pendant le pipeline initial, l'expansion rapide utilise eBay seulement pour éviter deux requêtes Zalando concurrentes.
- Sur Render, Zalando est limité à une seule page d'expansion (page 2) ; jamais de page 3.
- Une page Zalando d'expansion vide marque la source épuisée pour la recherche.
- Limite Zalando abaissée de 50 à 30 résultats par vague.
- Gunicorn 23.0.0 conservé pour compatibilité Render.

Cette version privilégie la fluidité globale du radar : si Zalando est indisponible, les autres marketplaces continuent sans attendre.
