# Alertes Discord Vinted / eBay

L'organisation s'inspire des salons par marque visibles dans la vidéo fournie.
Un seul compte bot LUXE RADAR dessert les deux marketplaces, sans nouveau token.
Les anciens salons sont conservés. L'installation ajoute les éléments manquants
jusqu'à une structure de 9 catégories et 35 salons, dont 16 radars :

- 8 marques Vinted : Nike, Adidas, Ralph Lauren, Lacoste, The North Face,
  Carhartt, Stone Island, Stussy ;
- 4 marques eBay : Nike, Adidas, Ralph Lauren, Lacoste ;
- 4 salons CPU : AMD et Intel sur chaque source, maximum 300 EUR.

Les marques démarrent à 100 EUR, intervalle minimum 2 minutes ; ce sont des réglages
initiaux modifiables, pas des seuils garantissant une bonne affaire.
Les processeurs démarrent à 300 EUR, intervalle minimum 2 minutes.
Chaque règle représente une recherche ; les requêtes larges ne couvrent pas
toutes les annonces d'une marketplace. Chaque scan lit au plus 50 résultats.

## Installation ciblée

Prévisualisation hors réseau et sans changement :

```powershell
.\.venv\Scripts\python.exe discord_server_setup.py
```

Après identification du serveur de destination :

```powershell
.\.venv\Scripts\python.exe discord_server_setup.py --apply --guild-id IDENTIFIANT_DU_SERVEUR
```

Le script exige une destination explicite, ne parcourt pas les autres serveurs
et ne supprime ni ne déplace les anciens salons. Les nouveaux salons d'alertes
sont en lecture seule pour le rôle général ; le bot peut y envoyer les annonces.
Les salons déjà présents gardent leurs permissions. Une seconde installation
ne remplace pas les réglages existants, même mis en pause.

Lancer ensuite le bot sur le même ordinateur et dans le même projet :

```powershell
.\.venv\Scripts\python.exe discord_bot.py
```

Le bot doit rester actif ; cette commande n'installe pas d'hébergement 24 h/24.
Utiliser une seule instance du bot : la déduplication n'est pas un verrou
distribué entre plusieurs processus. Le script utilise le token déjà configuré
dans l'environnement ; ne jamais le coller dans Discord ou dans un journal.

## Commandes

- `/surveiller source:eBay produit:AMD Ryzen 7 7800X3D prix_max:300` : configure
  les alertes dans le salon courant ; nécessite Gérer les salons.
- `/arreter-surveillance` : met le salon courant en pause, même permission.
- `/etat-surveillance` : affiche les critères et le dernier résultat du salon.
- `/ebay produit:Intel Core i5-12400F prix_max:150` : recherche immédiate privée.
- `/radar` puis `/scanner` : recherche Vinted personnelle existante.

La surveillance automatique des salons démarre avec le bot. Elle est distincte
des radars personnels historiques du site. Chaque passage envoie au plus cinq
annonces, sans ping de rôle. Les annonces envoyées sont mémorisées dans
`instance/discord_radar.sqlite3`, y compris après redémarrage. Une erreur d'envoi
laisse l'annonce réessayable. Une coupure entre l'envoi Discord et l'écriture
SQLite peut exceptionnellement produire un doublon. Les erreurs de collecte
sont espacées d'au moins 15 minutes. Une réponse vide ne certifie pas la santé
de la source. Les scans sont séquentiels : les intervalles sont des minima.

## Publications récentes et bouton BUY (mise à jour du 6 septembre)

Les alertes automatiques exigent désormais une publication de moins de 15
minutes, vérifiée à l'instant de l'envoi. Cette limite est configurable avec
`/surveiller anciennete_max:15` (minutes). Une annonce seulement découverte
par le bot, mais ancienne ou sans date vérifiable, n'est pas envoyée.
Les recherches manuelles peuvent afficher une annonce plus ancienne, avec sa
date ou une mention explicite « Date non disponible ».

Le tri récent est demandé à la source : `newlyListed` pour eBay et
`order=newest_first` pour Vinted (vérifié dans son interface publique).
Les autres recherches du site gardent leur classement par défaut.
eBay transmet `itemCreationDate`. Pour Vinted, au plus cinq fiches sont lues
dans le navigateur habituel, uniquement dans le résumé officiel de l'article,
hors description du vendeur. Les libellés « à l'instant » et « il y a X minutes »
sont convertis avec une borne prudente et affichés comme approximatifs.
Un refus ou challenge arrête la lecture du lot sans contournement.

Chaque message porte un bouton BUY vers l'annonce officielle, la mise en vente
et, si connue, la détection locale dans deux champs distincts. Les dates relatives
Discord évoluent automatiquement avec le temps. Le bouton est un lien, sans achat
ni paiement automatique. Les deux boucles de sources sont indépendantes : eBay
n'attend pas le navigateur de Vinted. Les scans Vinted restent séquentiels.

Les 16 règles du serveur cible ont été migrées vers l'intervalle minimum de
120 secondes et la fraîcheur maximale de 900 secondes, en conservant les prix,
requêtes, pauses et historique. Les fiches à date inconnue sont différées quinze
minutes ; les anciennes sont ignorées pendant une journée. Ces règles ne
garantissent ni l'exhaustivité de la marketplace ni une détection à la seconde.

Validation : 22 tests radars/dates hors réseau et deux scripts Discord existants
réussis. Des dates réelles récentes ont été lues sur eBay et Vinted ; les libellés
Vinted « à l'instant » ont reçu un test dédié. Sauvegarde sources avant changement :
`20260906_145913_manual.zip`. Copie SQLite des réglages avant migration :
`instance/discord-radar-before-freshness.sqlite3`.

Référence API : [guide eBay de découverte des annonces](https://edp.ebay.com/develop/guides/buy/inventory-discovery-and-refresh-guide).

## Détection CPU

Les suffixes des références sont conservés : 5600 et 5600X, ou 12400 et 12400F,
ne sont pas interchangeables. Les titres et états signalant des accessoires,
ordinateurs complets ou processeurs défectueux sont écartés par un filtre textuel
conservateur. Ce filtre ne peut pas vérifier l'état physique, l'authenticité ou
la disponibilité. Prix hors frais éventuels ; achat final sur la marketplace.

## Validation du 6 septembre 2026

- Sauvegarde : `20260906_094453_manual.zip`, sans `.env`.
- Tests locaux : `test_discord_radar.py` (12 tests), deux anciens scripts de tests
  Discord et organisation, compilation de 130 fichiers et 9 smoke tests du manager : OK.
- Recherche réelle CPU eBay : 9 annonces après filtre pour `AMD Ryzen 5 5600`,
  maximum 300 EUR ; le premier essai a permis de compléter le filtre des titres
  signalant un défaut. Ce nombre dépend des annonces disponibles au moment du test.
- Le premier essai CPU Vinted ciblé avait renvoyé zéro résultat. Lors du
  déploiement, les requêtes larges `AMD Ryzen` et `Intel Core` ont chacune
  retourné 14 correspondances et publié 5 alertes : accès CPU Vinted confirmé
  sur ce passage, sans contournement de protection.
- Déploiement appliqué au seul serveur `1545750308267888721` : 4 catégories
  et 17 salons ajoutés, 35 salons attendus vérifiés par l'API Discord ;
  16 radars configurés et premier passage terminé sans erreur.
- Les quatre salons CPU ont chacun publié 5 alertes. La lecture des messages
  a révélé un mini-PC Lenovo dans eBay AMD : filtre corrigé, test de régression
  ajouté, et notre propre message rectifié dans Discord. Cela illustre la
  limite du filtrage textuel, qui n'est pas une garantie de pertinence parfaite.
- Bot relancé en arrière-plan avec la correction, connexion confirmée,
  historique et 16 radars actifs conservés. Après correction : 12 tests radars,
  compilation de 130 fichiers et 9 smoke tests du manager OK.
- Sauvegarde avant cette correction : `20260906_145524_manual.zip`.
- Le démarrage automatique de Windows et l'hébergement permanent ne sont
  pas installés : le processus local doit rester actif et le PC éveillé.
