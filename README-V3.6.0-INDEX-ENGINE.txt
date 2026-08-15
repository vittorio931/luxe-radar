LUXE RADAR V3.6.0 — INDEX ENGINE
=================================

OBJECTIF
--------
Passer d'un comparateur qui attend les marketplaces à un moteur hybride :
1. afficher instantanément les offres déjà indexées ;
2. rafraîchir les sources en arrière-plan ;
3. réinjecter uniquement des offres réellement renvoyées par les connecteurs.

CE QUI CHANGE
-------------
- index_engine.py : index SQLite persistant et thread-safe (WAL) ;
- index-first : si au moins 12 offres fraîches sont déjà indexées, aucun appel
  marketplace ne bloque le premier HTML ;
- fallback eBay : si l'index est froid, le comportement rapide historique reste ;
- jusqu'à 5 000 offres chargées par recherche, soit 100 pages de 50 ;
- chaque scan progressif et chaque vague d'infinite scroll nourrit l'index ;
- warm_index.py : préchauffage réel pour Stone Island / Nike P-6000 / On Cloud 5 ;
- /api/index/status : compteurs d'index sans exposer le chemin du serveur ;
- badge "Index instantané" dans les résultats quand le rendu vient de l'index.

IMPORTANT POUR RENDER
---------------------
SQLite est parfait pour tester la V3.6 sans nouvelle dépendance. Sur Render,
le système de fichiers normal peut être éphémère. Pour conserver l'index entre
redémarrages, définir LUXE_RADAR_INDEX_DB vers un chemin situé sur un disque
persistant Render. Une migration PostgreSQL/Typesense peut venir ensuite quand
le trafic le justifie ; la V3.6 ne dépend pas encore de ces services.

PRECHAUFFAGE LOCAL
------------------
  python warm_index.py --query "Stone Island" --pages 3
  python warm_index.py --query "Nike P-6000" --pages 3
  python warm_index.py --query "On Cloud 5" --pages 3

Ou simplement :
  python warm_index.py

Aucun CAPTCHA, login ou contrôle d'accès n'est contourné. Une source bloquée
échoue proprement et n'empêche pas l'index de servir les autres offres.
