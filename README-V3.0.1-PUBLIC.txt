LUXE RADAR V3.0.1 — PUBLIC READY
=============================================

Objectif
--------
Version candidate pour remise en public après la série V2.9.x.

Points clés
-----------
- Interface Radar 3.0 retravaillée : zone de recherche plus grande, tableau de bord
  plus lisible, actions rapides, historique de requêtes directement dans le Radar.
- 14 connecteurs actifs au catalogue, dont 3 nouveaux connecteurs retail publics :
  Spartoo, Footshop et JD Sports.
- Les nouveaux connecteurs ne contournent aucun CAPTCHA, login ou contrôle d'accès.
  Ils lisent d'abord les données structurées JSON-LD, puis utilisent un fallback HTML
  conservateur qui exige toujours titre + prix + URL produit réelle.
- Scroll progressif V2.9.3 conservé : une seule vague à la fois, backoff sur 202/429,
  pas de rafale de /expand.
- Une recherche expirée après redémarrage du serveur arrête maintenant son polling 404.
- Nouvelles actions : Bons plans, Mes favoris, Faible risque, Créer une alerte,
  recherches récentes directement réutilisables.
- Aucun .env dans les archives.

Déploiement Render
------------------
Conserver exactement la commande de build déjà validée :
python -m pip install -r requirements.txt && python -m playwright install chromium

Start command :
gunicorn --config gunicorn.conf.py wsgi:application

Avant mise en public
--------------------
1. Tester localement une requête Nike P-6000, On Cloud 5 et Columbia Tech Wind.
2. Vérifier /api/version => 3.0.1 / 20260815-301.
3. Vérifier que les premiers résultats apparaissent sans attendre Grailed/Vinted.
4. Descendre jusqu'à plusieurs vagues et vérifier l'absence de spam 429.
5. Tester mobile après déploiement staging/public.

Note importante
---------------
Les sources publiques peuvent modifier leur HTML ou appliquer des protections d'accès.
Le Radar doit alors renvoyer 0 pour la source concernée plutôt que fabriquer un résultat.

PAIEMENT / SÉCURITÉ
-------------------
Le checkout Stripe est volontairement OFF par défaut, même si STRIPE_SECRET_KEY
est présent. Ne définir LUXE_RADAR_BILLING_ENABLED=1 qu’après branchement et
validation du webhook de confirmation + provisionnement réel du plan client.
Cela évite tout débit accidentel lors du déploiement public.
