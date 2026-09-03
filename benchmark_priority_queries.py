r"""Audit rapide des 15 recherches prioritaires dans l'index réellement stocké.

Ce script ne contacte aucune marketplace : il mesure exactement ce que LUXE
RADAR peut afficher instantanément, sans charger le petit serveur Render.

Usage :
    .\.venv\Scripts\python.exe benchmark_priority_queries.py
    .\.venv\Scripts\python.exe benchmark_priority_queries.py --json
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from statistics import median
from time import perf_counter

import index_engine


PRIORITY_QUERIES = (
    "On Cloud 5",
    "Fourteen",
    "maillot de foot",
    "maillot Cameroun",
    "Nike Trail",
    "Nike P-6000",
    "Adidas Samba",
    "New Balance 2002R",
    "Salomon XT-6",
    "Stone Island",
    "veste Moncler",
    "sac Jacquemus",
    "Balenciaga Track",
    "pull Ralph Lauren",
    "chaussures Gucci",
)


def audit_query(query: str, limit: int = 250) -> dict:
    started = perf_counter()
    found = index_engine.search(query, identity="all", limit=limit)
    elapsed_ms = (perf_counter() - started) * 1000
    counts = Counter(
        str(item.get("marketplace") or "Inconnu") for item in found.results
    )
    total = max(0, int(found.total or 0))
    if total == 0:
        diagnosis = "ZERO"
    elif len(counts) < 2:
        diagnosis = "MONO_SOURCE"
    elif total < 25:
        diagnosis = "FAIBLE_VOLUME"
    elif elapsed_ms > 500:
        diagnosis = "LENT"
    else:
        diagnosis = "OK"
    return {
        "query": query,
        "total": total,
        "returned": len(found.results),
        "latency_ms": round(elapsed_ms, 1),
        "sources": dict(counts.most_common()),
        "source_count": len(counts),
        "diagnosis": diagnosis,
    }


def run() -> list[dict]:
    return [audit_query(query) for query in PRIORITY_QUERIES]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args()
    report = run()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{'Recherche':<25} {'Total':>7} {'ms':>8} {'Sources':>8}  Diagnostic")
        for row in report:
            print(
                f"{row['query']:<25} {row['total']:>7} "
                f"{row['latency_ms']:>8.1f} {row['source_count']:>8}  {row['diagnosis']}"
            )
        timings = [row["latency_ms"] for row in report]
        print(f"\nMédiane : {median(timings):.1f} ms")
        problems = [row for row in report if row["diagnosis"] != "OK"]
        print(f"Recherches à renforcer : {len(problems)}/{len(report)}")
        for row in problems:
            print(f"- {row['query']} : {row['diagnosis']} ({row['total']} résultats)")
    return 1 if any(row["diagnosis"] == "ZERO" for row in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
