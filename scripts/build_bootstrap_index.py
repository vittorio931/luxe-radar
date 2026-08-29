"""Construit un SQLite compressé avec uniquement l'index public d'annonces."""

import gzip
import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "instance" / "luxe_radar_index.sqlite3"
TARGET = ROOT / "bootstrap" / "search_index.sqlite3.gz"


def main():
    if not SOURCE.exists():
        raise SystemExit(f"Index source absent: {SOURCE}")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    compact = TARGET.with_suffix("")
    compact.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(compact)
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    connection = sqlite3.connect(compact)
    try:
        connection.executescript(
            "DELETE FROM collector_diag; DELETE FROM collector_pending; "
            "DELETE FROM collector_progress; DELETE FROM collector_runs; "
            "DELETE FROM learn_events; VACUUM;"
        )
        indexed = connection.execute("SELECT COUNT(*) FROM indexed_results").fetchone()[0]
        catalog = connection.execute("SELECT COUNT(*) FROM catalog_offers").fetchone()[0]
    finally:
        connection.close()
    with compact.open("rb") as source_stream, gzip.open(TARGET, "wb", compresslevel=9) as output:
        shutil.copyfileobj(source_stream, output, length=1024 * 1024)
    compact.unlink(missing_ok=True)
    print(f"[OK] bootstrap {indexed} indexées / {catalog} uniques / {TARGET.stat().st_size} octets")


if __name__ == "__main__":
    main()
