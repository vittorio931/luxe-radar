from __future__ import annotations

"""Registre robuste des connecteurs LUXE RADAR.

Ce module est volontairement au niveau racine du projet. Il évite de dépendre
de l'exécution de ``marketplaces/connectors/__init__.py`` au démarrage : sur
certaines extractions Windows, Python peut traiter ``marketplaces.connectors``
comme un namespace package et l'import ``from marketplaces.connectors import
get_available_connectors`` échoue alors même que les modules du dossier sont
présents.
"""

import importlib
import inspect
import os
import pkgutil
from pathlib import Path
from threading import RLock

from marketplaces.connectors.base import MarketplaceConnector
from marketplaces.connectors.quality_filters import filter_results
from marketplaces.connectors.universal import load_configured_connectors

_CONNECTORS_DIR = Path(__file__).resolve().parent / "marketplaces" / "connectors"
_SKIP_MODULES = {"__init__", "base", "quality_filters", "universal"}
_CACHE_LOCK = RLock()
_RAW_CONNECTORS_CACHE = None


def _debug(message):
    if os.environ.get("LUXE_RADAR_DEBUG_CONNECTORS") == "1":
        print(f"[CONNECTEURS] {message}")


def _should_skip(module_name):
    low = str(module_name or "").lower()
    if module_name in _SKIP_MODULES:
        return True
    return any(token in low for token in ("backup", "stable", "old", "test", "smoke"))


def _native_connectors():
    found = {}
    if not _CONNECTORS_DIR.is_dir():
        raise RuntimeError(f"Dossier connecteurs introuvable: {_CONNECTORS_DIR}")

    for info in pkgutil.iter_modules([str(_CONNECTORS_DIR)]):
        module_name = info.name
        if _should_skip(module_name):
            continue
        try:
            module = importlib.import_module(f"marketplaces.connectors.{module_name}")
        except Exception as exc:
            _debug(f"Import ignoré {module_name}: {exc}")
            continue

        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is MarketplaceConnector:
                continue
            try:
                is_connector = issubclass(cls, MarketplaceConnector)
            except Exception:
                is_connector = False
            if not is_connector or cls.__module__ != module.__name__:
                continue
            try:
                instance = cls()
            except Exception as exc:
                _debug(f"Classe ignorée {cls.__name__}: {exc}")
                continue
            name = str(getattr(instance, "name", "") or "").strip()
            if not name or name == "Marketplace":
                continue
            found[name] = instance
    return found


class _QualityProxy:
    def __init__(self, connector):
        self._connector = connector

    def __getattr__(self, name):
        return getattr(self._connector, name)

    def search(self, query, price_max=None, limit=20, **kwargs):
        results = self._connector.search(query=query, price_max=price_max, limit=limit, **kwargs)
        return filter_results(results, query=query, marketplace=str(getattr(self._connector, "name", "")))

    def search_page(self, query, price_max=None, limit=20, page=1, **kwargs):
        method = getattr(self._connector, "search_page", None)
        if method is None:
            return self.search(query=query, price_max=price_max, limit=limit, **kwargs)
        results = method(query=query, price_max=price_max, limit=limit, page=page, **kwargs)
        return filter_results(results, query=query, marketplace=str(getattr(self._connector, "name", "")))


def _all_raw_connectors():
    global _RAW_CONNECTORS_CACHE
    with _CACHE_LOCK:
        if _RAW_CONNECTORS_CACHE is None:
            found = _native_connectors()
            for connector in load_configured_connectors():
                name = str(getattr(connector, "name", "") or "").strip()
                if name and name not in found:
                    found[name] = connector
            _RAW_CONNECTORS_CACHE = found
        # Les appelants reçoivent une vue isolée, mais les instances sont
        # réutilisées : plus de relecture des 1 200 définitions ni de
        # réinstanciation de toutes les classes à chaque page de scroll.
        return dict(_RAW_CONNECTORS_CACHE)


def invalidate_connector_cache():
    """Invalide explicitement le registre après une modification de config."""
    global _RAW_CONNECTORS_CACHE
    with _CACHE_LOCK:
        _RAW_CONNECTORS_CACHE = None


def _aliases(name, connector):
    values = {str(name).strip().lower()}
    for attr in ("name", "display_name"):
        value = str(getattr(connector, attr, "") or "").strip().lower()
        if value:
            values.add(value)
    return values


def get_available_connectors():
    return {
        name: _QualityProxy(connector)
        for name, connector in _all_raw_connectors().items()
        if getattr(connector, "enabled", True)
    }


def get_connector(name):
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for canonical, connector in _all_raw_connectors().items():
        if wanted in _aliases(canonical, connector):
            return _QualityProxy(connector)
    return None


def get_all_connectors(include_disabled=True):
    return {
        name: _QualityProxy(connector)
        for name, connector in _all_raw_connectors().items()
        if include_disabled or getattr(connector, "enabled", True)
    }
