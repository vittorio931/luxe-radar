from __future__ import annotations

"""Warm the LUXE RADAR V3.7 global offer index with real connector results.

Examples:
  python warm_index.py
  python warm_index.py --query "Stone Island" --pages 5
  python warm_index.py --query "On Cloud 5" --sources "eBay,Vinted,Zalando,ASOS,i-Run"

No CAPTCHA/login bypass is attempted. Sources that refuse automated access simply
return zero results through their normal connector fail-fast behaviour.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter

from connector_registry import get_available_connectors
from index_engine import prune, stats, upsert_results
from radar_engine import rechercher_multi_marketplaces

DEFAULT_QUERIES = ["Stone Island", "Nike P-6000", "On Cloud 5"]
DEFAULT_PRIORITY = [
    "eBay", "Vinted", "Zalando", "ASOS", "SSENSE", "i-Run", "Direct Running",
    "Alltricks", "Deporvillage", "21RUN", "Running Point", "MisterRunning",
    "Hardloop", "Ekosport", "Courir",
]
PAGED_SOURCES = ["eBay", "Zalando", "Vinted"]


def _parse_args():
    parser = argparse.ArgumentParser(description="Pré-indexe des offres réelles LUXE RADAR.")
    parser.add_argument("--query", action="append", dest="queries", help="Requête à préchauffer (répétable).")
    parser.add_argument("--sources", default="", help="Sources séparées par des virgules.")
    parser.add_argument("--price-max", type=float, default=10000.0)
    parser.add_argument("--pages", type=int, default=3, help="Pages eBay/Zalando/Vinted, 1 à 20.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prune-days", type=int, default=7)
    return parser.parse_args()


def _scan_source(query, source, price_max, page=1, limit=60):
    return rechercher_multi_marketplaces(
        marque=query,
        prix_max=price_max,
        plateformes=[source],
        limite=limit,
        page=page,
    )


def main():
    args = _parse_args()
    queries = [q.strip() for q in (args.queries or DEFAULT_QUERIES) if q and q.strip()]
    available = get_available_connectors()
    requested = [part.strip() for part in args.sources.split(",") if part.strip()] if args.sources else DEFAULT_PRIORITY
    sources = [source for source in requested if source in available]
    pages = max(1, min(int(args.pages), 20))
    workers = max(1, min(int(args.workers), 5))

    print(f"[INDEX] requêtes={queries}")
    print(f"[INDEX] sources={', '.join(sources)} | pages profondes={pages}")
    total_written = 0
    for query in queries:
        started = perf_counter()
        futures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for source in sources:
                futures.append((source, 1, pool.submit(_scan_source, query, source, args.price_max, 1)))
            for source, page, future in futures:
                try:
                    results = future.result()
                except Exception as exc:
                    print(f"[INDEX] {query} | {source} p{page}: erreur {str(exc)[:120]}")
                    continue
                written = upsert_results(results, query)
                total_written += written
                print(f"[INDEX] {query} | {source} p{page}: {written} offre(s)")

        # Deep pages are intentionally serial per marketplace to avoid request storms.
        for page in range(2, pages + 1):
            for source in PAGED_SOURCES:
                if source not in sources:
                    continue
                try:
                    results = _scan_source(query, source, args.price_max, page=page, limit=60)
                except Exception as exc:
                    print(f"[INDEX] {query} | {source} p{page}: erreur {str(exc)[:120]}")
                    continue
                written = upsert_results(results, query)
                total_written += written
                print(f"[INDEX] {query} | {source} p{page}: {written} offre(s)")
        print(f"[INDEX] {query}: terminé en {perf_counter()-started:.2f}s")

    deleted = prune(older_than_seconds=max(1, args.prune_days) * 86400)
    state = stats()
    print(f"[INDEX] terminé | écritures={total_written} | purgées={deleted} | offres={state.get('offers')} | catalogue_global={state.get('catalog_offers')} | requêtes={state.get('queries')}")


if __name__ == "__main__":
    main()
