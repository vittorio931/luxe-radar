"""Point d'entrée WSGI de production."""

from app_web import _start_collector, app

# V3.8 : le collecteur profond démarre avec le process web (thread daemon).
# S'il meurt, gunicorn n'en a pas connaissance et l'app continue de servir
# l'index déjà persisté sur disque.
_start_collector()

application = app
