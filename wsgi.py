"""Point d'entrée WSGI de production.

Aucun background worker ne démarre au chargement de ce module.
Sous Gunicorn, les workers (collector + learning) démarrés dans le hook
post_fork de gunicorn.conf.py uniquement, après le fork du process.
"""

from app_web import app

application = app
