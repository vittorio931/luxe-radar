# Données et confidentialité

Ce document décrit le comportement technique actuel. Il ne remplace pas une
politique juridique adaptée au pays de mise en ligne.

## Données conservées dans le navigateur

L’application stocke localement, avec le préfixe `lr:` :

- favoris ;
- historique de recherches ;
- alertes enregistrées ;
- activité locale ;
- comparaisons et collections ;
- suivi de prix saisi manuellement ;
- inventaire de revente ;
- annonces explicitement consultées ;
- préférences d’interface et état de démonstration de l’abonnement.

Ces données peuvent être exportées, restaurées ou supprimées depuis la section
**Données**. L’import est limité à un fichier JSON de 2 Mo au format LUXE RADAR.

## Données côté serveur

Les résultats de recherche sont conservés temporairement en mémoire pour permettre
la pagination par lots. Ils expirent automatiquement et ne sont pas écrits dans une
base de données par l’application actuelle.

Les pages de recherche et endpoints API privés utilisent `Cache-Control: no-store`.
Les médias promotionnels publics peuvent être mis en cache pendant 24 heures.

## Ce qui n’est pas encore en place

- aucun compte utilisateur persistant ;
- aucun paiement réel tant que le prestataire n’est pas configuré ;
- aucune synchronisation cloud des favoris ;
- aucune notification distante ou scan caché ;
- aucun outil d’analytics tiers installé par défaut.

Avant une publication commerciale, faire relire la politique de confidentialité,
les mentions légales et les conditions d’utilisation par une personne qualifiée.

L’application expose également `/confiance` (français) et
`/confiance?lang=en` (anglais). Ce centre de confiance reprend ces faits dans
un format public et lisible, sans se présenter comme un avis juridique final.
