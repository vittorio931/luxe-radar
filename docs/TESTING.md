# TESTING.md — Validation checklist

## Always before major changes
```powershell
.\.venv\Scripts\python.exe .\luxe_radar_manager.py backup
```

Le ZIP contient le manager lui-même, les sources Python, le frontend, les
fichiers de déploiement et les médias publics. Il exclut `.env`, `.venv`, les
anciens backups et les pistes vidéo/audio intermédiaires. Une restauration à
blanc doit pouvoir exécuter `luxe_radar_manager.py test` sans le projet original.

## Compile a changed Python file
```powershell
.\.venv\Scripts\python.exe -m py_compile .\path\to\file.py
```

## Project tests
```powershell
.\.venv\Scripts\python.exe .\luxe_radar_manager.py test
```

Historically the manager may find only a small number of smoke-test files.
Do not overstate coverage: report exactly how many tests ran.

La suite actuelle exécute cinq fichiers :

- `test_radar_engine_smoke.py` ;
- `test_infinite_scroll.py` ;
- `test_catalog_massive.py` ;
- `test_security_ux.py` ;
- `test_v301_connector_import.py`.

V4 : les vidéos de campagne ont été retirées du catalogue (aucun MP4, aucun
`<video>` ni KIT CRÉATEUR). Le test vidéo `test_campaign_media.py` a été supprimé
avec les assets `static/campaign/`.

Pour une installation de développement reproductible :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirements-dev.txt
.\.venv\Scripts\python.exe -m bandit -ll -r app_web.py radar_engine.py marketplaces\connectors -x "*_backup.py"
```

`-ll` rend l’audit bloquant sur les alertes moyennes et hautes. Lire également le rapport
complet : les exceptions silencieuses de nettoyage/parsing peuvent apparaître en
faible sans constituer une autorisation de les ignorer systématiquement.

## Registry
```powershell
.\.venv\Scripts\python.exe .\luxe_radar_manager.py sync-registry
.\.venv\Scripts\python.exe .\luxe_radar_manager.py sites
```

## Launch
```powershell
.\.venv\Scripts\python.exe app_web.py
```

Local URL:
`http://127.0.0.1:5000`

## Regression searches

### eBay
Direct `Nike Trail`, <= 50 EUR should return many relevant listings when the API is healthy.
Do not require an exact fixed count because live inventory changes.
Le connecteur accepte maintenant jusqu’à 100 résultats normalisés ; vérifier sur
une recherche réelle que les URL restent uniques et que les faux positifs
« Portland Trail Blazers » sont rejetés.

### Grailed
`Nike Trail`, <= 50 EUR:
- headless may be challenged;
- visible Chromium fallback is expected;
- should return relevant Nike Trail cards if current inventory/routes are available.

Do not close the visible browser while the connector is using it.

### 67behaviour
`Nike Trail` should return relevant results when the site structure is unchanged.

### False positives
Verify that obvious unrelated Portland Trail Blazers listings are not ranked as Nike Trail matches.

## UI checks for future large-results feature

La recherche par lots est maintenant implémentée ; cette liste reste une checklist
de non-régression.
- initial page does not freeze;
- load-more/infinite scroll appends without duplicates;
- avec le chargement automatique OFF, le bouton manuel charge le lot suivant,
  disparaît à la fin et ne crée aucun doublon ;
- sorting does not lose results;
- marketplace filters work;
- exact price works;
- exact reference strict mode works;
- `DM4652-040` correspond à `DM4652 040` ; le boost conserve les autres annonces
  plus bas et le mode strict les retire ;
- une correspondance classée « A IGNORER » ne dépasse pas une « BONNE AFFAIRE »
  uniquement grâce au boost de référence ;
- la référence est conservée dans l’historique et le lien de partage ;
- comparator works with 2, 3 and 4 products;
- le comparateur ne marque « moins cher », « meilleur total », « meilleur score »
  ou « meilleure confiance » que lorsqu’une valeur numérique existe ;
- empty/missing image does not break card layout;
- direct links remain correct.

## Explorateur du catalogue

- `GET /api/catalog` renvoie au maximum 50 sites par lot ;
- deux lots consécutifs ne partagent aucun domaine ;
- la recherche texte et le filtre `status=active` sont testés ;
- les sept connecteurs actifs apparaissent avant les entrées OFF ;
- l’interface affiche honnêtement les statuts et ne transforme jamais une entrée
  du catalogue en connecteur actif ;
- sur 390 px, les filtres et cartes n’ajoutent aucun défilement horizontal et
  les contrôles tactiles mesurent au moins 44 px.

## Alertes locales

- le produit et le prix cible sont validés avant stockage ;
- une alerte strictement identique n’est pas ajoutée deux fois ;
- les correspondances sont calculées uniquement dans les résultats déjà affichés ;
- le texte ne laisse jamais entendre qu’un scan serveur ou une notification
  distante tourne en arrière-plan.

## Centre de confiance

- `/confiance` répond en français et `/confiance?lang=en` en anglais ;
- les deux pages annoncent exactement sept connecteurs testés et 1 218 sites
  référencés, sans confondre catalogue et sources actives ;
- `/sitemap.xml` référence l’accueil et le centre de confiance ;
- la page rappelle que scores, authenticité, disponibilité et bénéfices ne sont
  jamais garantis.

## Outils locaux revendeur

- une collection est créée uniquement depuis au moins un favori et son nom doit
  être unique ; son aperçu montre jusqu’à trois pièces ;
- un relevé de prix refuse les valeurs négatives, nulles, non finies ou supérieures
  à 1 000 000 et garde au plus 200 relevés par article ;
- l’inventaire valide achat, vente et statut, puis reste borné à 1 000 lignes ;
- recherche et filtre de statut modifient la liste et le compteur affiché/total,
  mais pas les statistiques globales ;
- toutes ces données restent couvertes par les limites et le nettoyage de l’import.

## Image-search future checks
- reject oversized/non-image uploads;
- handle corrupt images;
- timeout failed remote image downloads;
- do not keep temp files indefinitely;
- visual similarity ranking is deterministic enough for tests;
- no crash when a listing has no image.
