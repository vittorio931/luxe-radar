# LUXE RADAR 3.0.2 — Public Performance Hotfix

LUXE RADAR centralise la recherche de produits mode et sneakers sur plusieurs sources, classe les annonces, conserve les résultats moins sûrs sans les imposer dans le flux recommandé et charge de nouvelles vagues au fil du scroll.


## Hotfix V3.0.2

- Zalando échoue maintenant vite : timeout lecture 6 s, sans retry automatique.
- Après timeout/403/429, un circuit breaker temporaire évite de re-bloquer le scroll.
- Sur Render, Zalando ne dépasse plus la page 2 et une page d’expansion vide suffit à l’épuiser.
- Pendant le pipeline initial, l’expansion rapide reste sur eBay pour éviter deux requêtes Zalando concurrentes.
- Aucun contrôle d’accès Zalando n’est contourné.

## Version publique

- Radar 3.0 : interface PC plus large, responsive mobile, mode dense et mode focus.
- Recherche tolérante aux fautes de marque/modèle (ex. Onclod, Colmbia, Nikz, Hka…).
- Scroll progressif avec backoff : une seule expansion à la fois, respect des réponses 202/429.
- 14 connecteurs actifs configurés : eBay, Vinted, ASOS, Cdiscount, Grailed, AliExpress, SSENSE, 67behaviour, DHgate, 1688, Zalando, Spartoo, Footshop et JD Sports.
- Catalogue exploratoire de plus de 1 200 sites, séparé des connecteurs actifs.
- Nouveaux outils : Bons plans, Favoris, Faible risque, alertes locales, recherches récentes, filtres rapides et indicateurs de sources.
- Aucun résultat n’est fabriqué. Si une source bloque, exige une connexion ou ne fournit pas de carte exploitable, elle peut simplement retourner 0 résultat.

## Lancement local

```powershell
python app_web.py
```

Puis ouvrir `http://127.0.0.1:5000`.

## Render

Build command — conserver exactement :

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

Ne jamais committer `.env`. Les identifiants eBay/Stripe et la clé de session restent dans les variables d’environnement du service.

Le checkout Stripe est **désactivé par défaut**, même si une clé Stripe est présente. Ne définir `LUXE_RADAR_BILLING_ENABLED=1` qu’après mise en place et validation d’une confirmation serveur (webhook) et du provisionnement réel des abonnements.

## Limites assumées

LUXE RADAR n’essaie pas de contourner CAPTCHA, login, challenge anti-bot ou autre contrôle d’accès. Les marketplaces peuvent modifier leurs pages ; un connecteur qui n’arrive plus à lire une source doit échouer proprement plutôt que produire de fausses annonces.
