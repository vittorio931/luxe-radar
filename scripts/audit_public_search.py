r"""Audit séquentiel du site public, sans lancer de recherches en parallèle.

Usage :
    .\.venv\Scripts\python.exe scripts\audit_public_search.py
    .\.venv\Scripts\python.exe scripts\audit_public_search.py --query "Fourteen"
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.request


DEFAULT_BASE = "https://luxe-radar.onrender.com"
DEFAULT_QUERIES = (
    "Fourteen",
    "maillot Cameroun",
    "pull Ralph Lauren",
    "sac Jacquemus",
    "chaussures Gucci",
)


def _read(opener, url, timeout=45, attempts=3):
    """Lecture avec reprise courte pour les redémarrages du worker gratuit."""
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            with opener.open(url, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise last_error


def audit(base: str, query: str, wait_seconds: int = 12) -> dict:
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies)
    )
    started = time.perf_counter()
    html = _read(opener, f"{base}/search?{urllib.parse.urlencode({'q': query})}", timeout=45)
    token_match = re.search(r'"token":\s*"([0-9a-f]{32})"', html)
    if not token_match:
        return {"query": query, "error": "token absent"}
    token = token_match.group(1)
    deadline = time.monotonic() + max(1, wait_seconds)
    status = {}
    first_result_ms = None
    while True:
        status = json.loads(_read(opener, f"{base}/api/results/{token}/status", timeout=30))
        if status.get("total", 0) and first_result_ms is None:
            first_result_ms = round((time.perf_counter() - started) * 1000)
        if not status.get("pending") or time.monotonic() >= deadline:
            break
        time.sleep(2)
    counts = {
        name: int(count)
        for name, count in (status.get("source_counts") or {}).items()
        if int(count or 0) > 0
    }
    return {
        "query": query,
        "total": int(status.get("total") or 0),
        "first_result_ms": first_result_ms,
        "sources_with_results": len(counts),
        "source_counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "pending": bool(status.get("pending")),
        "completed": list(status.get("completed_sources") or []),
        "failed": list(status.get("failed_sources") or []),
        "skipped": list(status.get("skipped_sources") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--query", action="append", dest="queries")
    parser.add_argument("--wait", type=int, default=12)
    parser.add_argument("--delay", type=float, default=3.0, help="Pause entre les recherches")
    args = parser.parse_args()
    queries = tuple(args.queries or DEFAULT_QUERIES)
    report = []
    for position, query in enumerate(queries):
        if position:
            time.sleep(max(0.0, args.delay))
        try:
            row = audit(args.base.rstrip("/"), query, args.wait)
        except Exception as exc:
            row = {"query": query, "error": f"{type(exc).__name__}: {exc}"}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print("SUMMARY " + json.dumps(report, ensure_ascii=False))
    return 1 if any(row.get("error") for row in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
