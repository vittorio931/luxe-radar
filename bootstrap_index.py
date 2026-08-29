"""Restaure rapidement l'index public sur un disque Render éphémère."""

import gzip
import os
import shutil
from pathlib import Path
from threading import Lock

import index_engine


SNAPSHOT = Path(__file__).resolve().parent / "bootstrap" / "search_index.sqlite3.gz"
_LOCK = Lock()
_DONE = False


def _target_counts(target: Path) -> dict:
    if not target.exists() or target.stat().st_size <= 0:
        return {"exact": 0, "catalog": 0, "catalog_total": 0, "columbia_exact": 0}
    counts = index_engine.count_query_offers("Balenciaga", path=target)
    counts["columbia_exact"] = index_engine.count_query_offers(
        "pantalon Columbia", path=target
    )["exact"]
    return counts


def _target_is_current(counts: dict) -> bool:
    # Ces seuils décrivent le snapshot public actuellement livré. Ils évitent
    # qu'un disque Render encore « chaud », mais issu du snapshot précédent,
    # empêche l'import de la nouvelle couverture Columbia.
    return (
        counts.get("exact", 0) >= 1000
        and counts.get("catalog_total", 0) >= 10000
        and counts.get("columbia_exact", 0) >= 200
    )


def ensure_bootstrap_index(*, path=None) -> dict:
    global _DONE
    if not SNAPSHOT.exists() or not index_engine.index_enabled():
        return {"loaded": False, "reason": "absent_or_disabled"}
    target = Path(path or index_engine.default_db_path()).resolve()
    # ``_DONE`` n'est qu'une optimisation, jamais une preuve durable : Render
    # peut remplacer/perdre son disque éphémère après un crash sans recréer le
    # processus Python de la manière attendue. Revalider le contenu réel.
    if _DONE:
        counts = _target_counts(target)
        if _target_is_current(counts):
            return {"loaded": False, "reason": "ready", **counts}
        _DONE = False
    with _LOCK:
        counts = _target_counts(target)
        if _target_is_current(counts):
            _DONE = True
            return {"loaded": False, "reason": "already_warm", **counts}
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".bootstrap.tmp")
        try:
            with gzip.open(SNAPSHOT, "rb") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        _DONE = True
        final = _target_counts(target)
        return {"loaded": True, "imported": final["catalog_total"], **final}


__all__ = ["ensure_bootstrap_index", "SNAPSHOT"]
