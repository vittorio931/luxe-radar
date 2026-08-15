LUXE RADAR V3.1.0 — SIGNATURE UI + RESULTS + RELIABILITY

Objectif de cette release : remplacer V3.0.2 publique en intégrant le hotfix V3.0.3 (résultats visibles + Vinted prioritaire), puis une vraie refonte UI et des garde-fous supplémentaires.

PRINCIPAUX CHANGEMENTS
- Signature UI beaucoup plus structurée sur PC/mobile.
- 3 thèmes + 6 couleurs d’accent persistantes.
- Vue grille/liste, mode dense, focus, réduction d’animations.
- Scanner plus / Partager / Réinitialiser + presets de recherche + raccourci /.
- Pertinence : tout afficher par défaut (les annonces possibles restent marquées À vérifier).
- Vinted juste après eBay dans le pipeline Render.
- ASOS réduit à une page FR par défaut sur Render, timeout lecture 4 s.
- AliExpress demande FR/EUR via paramètres/cookie publics, sans authentification ni contournement.
- Footshop/JD/Spartoo et autres retail publics : cooldown mémoire après 400/403/429 pour éviter de perdre du temps à répétition.
- Zalando fast-fail V3.0.2 conservé.
- Infinite scroll anti-spam V2.9.3+ conservé.

VALIDATION EFFECTUÉE
- test_v310_signature_release.py
- test_v301_public_release.py
- test_v302_performance_hotfix.py
- test_v303_result_visibility.py
- test_search_understanding_v288.py
- test_product_recognition_v287.py
- test_retail_public_connectors.py
- test_asos_connector.py
- test_cdiscount_connector.py
- test_relevance_v282.py
- test_search_intent_v27.py
- node --check static/app.js
- py_compile sur app + connecteurs
- parsing Jinja de templates/index.html
- parsing CSS via tinycss2

NOTE
Les tests structurels/parsers passent dans l’environnement de build. Le rendu navigateur et la latence réelle des marketplaces doivent encore être validés sur le PC de test puis sur Render. Aucun CAPTCHA/403 externe n’est contourné.
