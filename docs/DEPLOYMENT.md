# Déploiement de production

## Validation locale

```powershell
.\.venv\Scripts\python.exe .\luxe_radar_manager.py test
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

## Variables attendues

Partir de `.env.example`. Ne jamais copier le secret de développement en production.

- `LUXE_RADAR_ENV=production`
- `LUXE_RADAR_SECRET_KEY=<secret aléatoire de 32 caractères ou plus>`
- `LUXE_RADAR_ALLOWED_HOSTS=radar.example.com`
- `LUXE_RADAR_TRUST_PROXY=1` uniquement derrière un proxy connu
- `LUXE_RADAR_VERBOSE=false` pour éviter les logs annonce par annonce

## Démarrage Linux

Installer Chromium après les dépendances Python :

```bash
python -m playwright install chromium
```

Sur une image Linux minimale, utiliser la variante `--with-deps` pendant le
build si l’hébergeur l’autorise.

```bash
gunicorn --config gunicorn.conf.py wsgi:application
```

Le `Procfile` fournit également cette commande aux hébergeurs compatibles.
La configuration démarre avec un seul processus et quatre threads pour rester
compatible avec une petite instance. `WEB_CONCURRENCY` permet de choisir entre
1 et 4 processus sur une machine plus grande.

## Déploiement Render gratuit

`render.yaml` décrit un service web gratuit avec Gunicorn, Chromium, un secret
généré par Render et un health-check. Render fournit automatiquement
`RENDER_EXTERNAL_HOSTNAME`; LUXE RADAR ajoute uniquement ce domaine précis à sa
liste d’hôtes autorisés.

Le service gratuit se met en veille après 15 minutes sans trafic et peut mettre
environ une minute à redémarrer. Son disque est éphémère : cela ne supprime pas
les favoris et inventaires, qui restent dans le navigateur, mais aucun fichier
créé côté serveur ne doit être considéré comme persistant.

Documentation officielle :

- https://render.com/docs/free
- https://render.com/docs/blueprint-spec
- https://render.com/docs/deploy-flask

## Vérifications après publication

- `/api/health` renvoie HTTP 200 ;
- la page racine est servie en HTTPS ;
- HSTS apparaît uniquement en HTTPS ;
- les recherches et API renvoient `Cache-Control: no-store` ;
- les fichiers vidéo acceptent les requêtes partielles HTTP 206 ;
- les six vidéos ne sont hydratées qu’après ouverture de **Découvrir** ;
- les connecteurs actifs restent exactement ceux réellement validés ;
- exécuter une recherche réelle sur chaque connecteur depuis l’environnement
  publié : Grailed peut refuser le mode headless et son dernier recours visible
  n’est généralement pas disponible sur un serveur sans interface graphique ;
- le fichier `.env` et les sauvegardes ne sont jamais exposés publiquement.

## Limites de l’architecture actuelle

La limitation de débit et le cache de recherche sont en mémoire locale. Pour un
déploiement multi-instance, les déplacer vers un service partagé comme Redis avant
de promettre une cohérence globale.
