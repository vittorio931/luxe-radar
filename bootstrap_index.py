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


def ensure_bootstrap_index(*, path=None) -> dict:
    global _DONE
    if not SNAPSHOT.exists() or not index_engine.index_enabled():
        return {"loaded": False, "reason": "absent_or_disabled"}
    target = Path(path or index_engine.default_db_path()).resolve()
    # ``_DONE`` n'est qu'une optimisation, jamais une preuve durable : Render
    # peut remplacer/perdre son disque éphémère après un crash sans recréer le
    # processus Python de la manière attendue. Revalider le contenu réel.
    if _DONE:
        counts = (
            index_engine.count_query_offers("Balenciaga", path=target)
            if target.exists() and target.stat().st_size > 0
            else {"exact": 0, "catalog": 0, "catalog_total": 0}
        )
        if counts["exact"] >= 1000 and counts["catalog_total"] >= 10000:
            return {"loaded": False, "reason": "ready", **counts}
        _DONE = False
    with _LOCK:
        counts = (
            index_engine.count_query_offers("Balenciaga", path=target)
            if target.exists() and target.stat().st_size > 0
            else {"exact": 0, "catalog": 0, "catalog_total": 0}
        )
        if counts["exact"] >= 1000 and counts["catalog_total"] >= 10000:
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
        final = index_engine.count_query_offers("Balenciaga", path=target)
        return {"loaded": True, "imported": final["catalog_total"], **final}


__all__ = ["ensure_bootstrap_index", "SNAPSHOT"]
