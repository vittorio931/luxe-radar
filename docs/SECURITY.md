# Sécurité de LUXE RADAR

## Garanties actuellement implémentées

- jeton CSRF lié à la session pour chaque requête `POST` ;
- limitation en mémoire des requêtes globales et des soumissions ;
- validation stricte du produit, du prix, de la marketplace, des tris et filtres ;
- refus des hôtes HTTP non autorisés ;
- assainissement des URL de résultats avant exposition au navigateur ;
- liste blanche explicite des champs publics de résultat, avec textes bornés et
  rejet des nombres non finis avant sérialisation JSON ;
- politique CSP avec nonce, médias et workers limités à la même origine ;
- protection anti-framing, anti-MIME sniffing et politique de référent stricte ;
- cookies `HttpOnly`, `SameSite=Lax` et `Secure` en production ;
- secret de production d’au moins 32 caractères obligatoire au démarrage ;
- exports CSV neutralisés contre l’injection de formules ;
- import JSON local limité à 2 Mo, à un format/version connus, à des tailles de
  collections bornées et à des objets nettoyés ;
- aucune mise en cache des pages de recherche et réponses API privées.
- réponses 400/404/405/413/500 uniformisées : JSON borné pour `/api/*`, texte
  simple ailleurs, sans page d’erreur de développement ni trace exposée ;
- les jetons aléatoires de pagination sont liés à la session qui a lancé la
  recherche : une seconde session reçoit une réponse 404, même avec le jeton ;
- les filtres de pagination refusent les marketplaces inconnues et les prix
  minimum non numériques, non finis ou hors limites ;
- l’API publique du catalogue ne renvoie que des champs descriptifs bornés et
  des URL HTTP(S), jamais le contenu de `.env` ni un secret de connecteur ;
- `.gitignore` et `.dockerignore` excluent `.env`, les backups, l’environnement
  virtuel et les intermédiaires lourds de production vidéo.

## Frontières importantes

- LUXE RADAR n’authentifie pas les articles et ne garantit pas leur authenticité.
- Le score et la confiance sont des aides au classement, jamais une garantie d’achat.
- Les achats ont lieu sur les sites des marketplaces, hors de LUXE RADAR.
- Aucun CAPTCHA, 403, mur de connexion ou mécanisme anti-bot ne doit être contourné.
- Une marketplace reste désactivée tant qu’une recherche réelle exploitable n’a pas validé son connecteur.
- Le paiement Stripe n’est actif que si `STRIPE_SECRET_KEY` est présente dans `.env`
  (clé serveur, jamais exposée à l’interface). Sans clé, aucun débit n’est possible
  et la démo locale 7 jours reste proposée. Les renouvellements d’abonnement sont
  gérés par Stripe ; les webhooks de confirmation ne sont pas encore branchés et
  aucune fonctionnalité n’est verrouillée côté serveur.

## Avant une mise en production

1. Définir un secret aléatoire via `LUXE_RADAR_SECRET_KEY` sans le committer.
2. Servir exclusivement derrière HTTPS.
3. Définir précisément `LUXE_RADAR_ALLOWED_HOSTS`.
4. Activer `LUXE_RADAR_TRUST_PROXY` uniquement derrière un proxy maîtrisé.
5. Garder un worker Gunicorn sur une petite instance ; augmenter
   `WEB_CONCURRENCY` uniquement si la mémoire le permet.
6. Centraliser le cache de recherche et les limites de débit avant tout
   déploiement multi-instance.
7. Ajouter une journalisation et une supervision externes sans enregistrer de secrets.
8. Relancer `luxe_radar_manager.py test` et `pip-audit` avant chaque publication.

## Signalement

Aucune adresse publique de sécurité n’est inscrite tant qu’un canal de contact réel
n’a pas été choisi. Ne pas publier de faux contact dans `security.txt`.
