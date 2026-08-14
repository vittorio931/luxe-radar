from __future__ import annotations

"""Auto-loader des connecteurs LUXE RADAR.

Tout fichier Python du dossier connectors contenant une classe héritant de
MarketplaceConnector peut être détecté automatiquement. Les fichiers backup,
test et helpers sont ignorés.
"""

import importlib
import inspect
import os
import pkgutil
from pathlib import Path

from .base import MarketplaceConnector
from .quality_filters import filter_results
from .universal import load_configured_connectors

_SKIP_MODULES = {
    "__init__",
    "base",
    "quality_filters",
    "universal",
}


def _debug(message):
    if os.environ.get("LUXE_RADAR_DEBUG_CONNECTORS") == "1":
        print(f"[CONNECTEURS] {message}")


def _should_skip(module_name):
    low = module_name.lower()
    if module_name in _SKIP_MODULES:
        return True
    return any(token in low for token in ("backup", "stable", "old", "test", "smoke"))


def _native_connectors():
    found = {}
    package_path = Path(__file__).resolve().parent
    prefix = __name__ + "."

    for info in pkgutil.iter_modules([str(package_path)]):
        module_name = info.name
        if _should_skip(module_name):
            continue
        try:
            module = importlib.import_module(prefix + module_name)
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
        return filter_results(
            results,
            query=query,
            marketplace=str(getattr(self._connector, "name", "")),
        )


def _all_raw_connectors():
    found = _native_connectors()
    for connector in load_configured_connectors():
        name = str(getattr(connector, "name", "") or "").strip()
        if name and name not in found:
            found[name] = connector
    return found


def _aliases(name, connector):
    values = {str(name).strip().lower()}
    for attr in ("name", "display_name"):
        value = str(getattr(connector, attr, "") or "").strip().lower()
        if value:
            values.add(value)
    return values


def get_available_connectors():
    result = {}
    for name, connector in _all_raw_connectors().items():
        if getattr(connector, "enabled", True):
            result[name] = _QualityProxy(connector)
    return result


def get_connector(name):
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for canonical, connector in _all_raw_connectors().items():
        if wanted in _aliases(canonical, connector):
            return _QualityProxy(connector)
    return None


def get_all_connectors(include_disabled=True):
    result = {}
    for name, connector in _all_raw_connectors().items():
        if include_disabled or getattr(connector, "enabled", True):
            result[name] = _QualityProxy(connector)
    return result
