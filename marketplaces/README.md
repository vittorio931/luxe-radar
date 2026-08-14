# LUXE RADAR — architecture multi-marketplaces

Le catalogue contient les marketplaces demandées par le projet.

## Etat actuel

- Vinted : connecteur déjà présent dans `radar_engine.py`.
- Les autres plateformes : référencées comme `planned`.
- Aucun faux résultat n'est généré pour une plateforme non connectée.

## Prochaine étape

Créer les connecteurs un par un dans `marketplaces/connectors/`, puis
faire passer tous les résultats par le même format normalisé avant le
ranking.

Avant toute automatisation d'une plateforme, vérifier ses API, son accès
public et ses conditions d'utilisation.
