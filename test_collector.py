"""Tests du collecteur de catalogue profond (collector.py + index_engine).

Vérifie : marche des pages, déduplication inter-pages, arrêt sur seuil de
pages vides / max_pages, dry-run sans écriture, sources en cooldown sautées,
traces collector_runs, avant/après par seed, et le dédupe d'enqueue.
"""

import os
import pathlib
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

import index_engine
import collector
from marketplaces import source_health


def _make_offer(number, marketplace="eBay", title=None):
    return {
        "titre": title or f"Nike P-6000 blanc taille 42 (annonce {number})",
        "prix": 120.0 + number,
        "devise": "EUR",
        "marketplace": marketplace,
        "source": marketplace,
        "lien": f"https://example.example/item/{number}",
        "url": f"https://example.example/item/{number}",
        "image": "",
        "categorie": "Baskets",
        "reference": "",
        "score": 90.0,
        "score_confiance": 0.8,
        "niveau_identite": "possible",
    }


def _make_irrelevant():
    item = _make_offer(999, title="Chaise design moderne")
    item["lien"] = "https://example.example/item/irrelevant"
    item["url"] = item["lien"]
    return item


class _FakeConnector:
    def __init__(self, name, pages, *, supports_pagination=True, max_pages=4,
                 empty_pages_threshold=2, cooldown_seconds=0.0, expansion_page_size=100):
        self.name = name
        self.display_name = name
        self._pages = list(pages)
        self.supports_pagination = supports_pagination
        self.max_pages = max_pages
        self.empty_pages_threshold = empty_pages_threshold
        self.cooldown_seconds = cooldown_seconds
        self.expansion_page_size = expansion_page_size
        self.calls = []

    def search(self, query, price_max=None, limit=20, **kwargs):
        self.calls.append(("search", 1))
        return self.search_page(query, price_max=price_max, limit=limit, page=1)

    def search_page(self, query, price_max=None, limit=20, page=1, **kwargs):
        self.calls.append(("page", int(page)))
        index = int(page) - 1
        if index < 0 or index >= len(self._pages):
            return []
        return [dict(item) for item in self._pages[index]]


def _with_index_db():
    """Contexte : index sur DB temporaire (le module lit l'env à l'appel)."""
    db = pathlib.Path(tempfile.mkdtemp()) / "test_index.sqlite3"
    previous = os.environ.get("LUXE_RADAR_INDEX_DB")
    os.environ["LUXE_RADAR_INDEX_DB"] = str(db)
    try:
        yield db
    finally:
        if previous is None:
            os.environ.pop("LUXE_RADAR_INDEX_DB", None)
        else:
            os.environ["LUXE_RADAR_INDEX_DB"] = previous
_with_index_db = contextmanager(_with_index_db)


def test_pages_dedupe_has_more_and_traces():
    with _with_index_db() as db:
        pages = [
            [_make_offer(1), _make_offer(2), _make_offer(3)],
            [_make_offer(3), _make_offer(4), _make_irrelevant()],
            [_make_offer(5)],
        ]
        fake = _FakeConnector("eBay", pages, max_pages=4)
        with patch.object(collector, "get_available_connectors", return_value={"eBay": fake}):
            summary = collector.collect_seed("Nike P-6000", 250, sources=["eBay"], path=db)

        walked = summary["sources"]["eBay"]
        assert walked["pages"] == 4, walked
        assert walked["parsed"] == 7, walked  # 3 + 3 + 1
        assert walked["relevant"] == 6, walked  # l'irrelevante est rejetée
        assert walked["rejected"] == 1, walked
        assert walked["new"] == 6, walked  # la rejetée est quand même une clé neuve indexée
        assert walked["duplicates"] == 1, walked  # /3 au p2
        assert walked["error"] == ""
        assert not walked["skipped"]

        detail = {d["page"]: d for d in walked["pages_detail"]}
        assert detail[1]["new"] == 3
        assert detail[2]["new"] == 2 and detail[2]["duplicates"] == 1
        assert detail[3]["new"] == 1
        assert detail[4]["new"] == 0 and detail[4]["has_more"] is False

        counts = index_engine.count_query_offers("Nike P-6000", path=db)
        assert counts["exact"] == 6, counts
        assert counts["catalog"] == 5, counts

        stats = index_engine.collector_stats(seed_query="Nike P-6000", path=db)
        assert stats["runs"] == 4 and stats["new"] == 6
        assert stats["sources"]["eBay"]["runs"] == 4


def test_non_paged_single_pass():
    with _with_index_db() as db:
        fake = _FakeConnector(
            "67behaviour",
            [[_make_offer(1, title="On Cloud 5 chaussures (annonce 1)"),
              _make_offer(2, title="On Cloud 5 chaussures (annonce 2)")]],
            supports_pagination=False, max_pages=1,
        )
        with patch.object(collector, "get_available_connectors", return_value={"67behaviour": fake}):
            summary = collector.collect_seed("On Cloud 5", 250, sources=["67behaviour"], path=db)
        walked = summary["sources"]["67behaviour"]
        assert walked["pages"] == 1 and walked["paged"] is False
        assert walked["new"] == 2 and walked["relevant"] == 2


def test_dry_run_writes_nothing():
    with _with_index_db() as db:
        fake = _FakeConnector("eBay", [[_make_offer(1), _make_offer(2)]], max_pages=4)
        with patch.object(collector, "get_available_connectors", return_value={"eBay": fake}):
            summary = collector.collect_seed("Nike P-6000", 250, sources=["eBay"], path=db, dry_run=True)
        walked = summary["sources"]["eBay"]
        assert walked["new"] == 2
        assert walked["pages"] == 3, walked  # p1 riche puis 2 pages vides consécutives (seuil=2)
        counts = index_engine.count_query_offers("Nike P-6000", path=db)
        assert counts["exact"] == 0
        stats = index_engine.collector_stats(seed_query="Nike P-6000", path=db)
        assert stats["runs"] == 0


def test_cooldown_source_skipped():
    with _with_index_db() as db:
        fake = _FakeConnector("Grailed", [[_make_offer(1)]])
        source_health.registry.preseed_blocked("Grailed")
        try:
            with patch.object(collector, "get_available_connectors", return_value={"Grailed": fake}):
                summary = collector.collect_seed("Stone Island", 300, sources=["Grailed"], path=db)
            walked = summary["sources"]["Grailed"]
            assert walked["skipped"] is True
            assert fake.calls == []
        finally:
            source_health.registry.reset()


def test_stops_on_empty_threshold():
    with _with_index_db() as db:
        fake = _FakeConnector("eBay", [], max_pages=8, empty_pages_threshold=2)
        with patch.object(collector, "get_available_connectors", return_value={"eBay": fake}):
            summary = collector.collect_seed("Nike P-6000", 250, sources=["eBay"], path=db)
        walked = summary["sources"]["eBay"]
        assert walked["pages"] == 2, walked  # 2 pages vides consécutives
        assert walked["new"] == 0


def test_known_keys_and_recent():
    with _with_index_db() as db:
        offers = [_make_offer(1), _make_offer(2)]
        index_engine.upsert_results(offers, "Nike P-6000", path=db)
        known = index_engine.known_offer_keys([index_engine._offer_key(offers[0]),
                                               index_engine._offer_key(offers[1]),
                                               index_engine._offer_key(_make_offer(3))], "Nike P-6000", path=db)
        assert len(known) == 2
        assert index_engine.collector_has_recent("Nike P-6000", max_age_seconds=3600, path=db) is False
        index_engine.record_collector_run(seed_query="Nike P-6000", marketplace="eBay", page=1,
                                          raw=2, parsed=2, relevant=2, new=2, has_more=True, path=db)
        assert index_engine.collector_has_recent("Nike P-6000", max_age_seconds=3600, path=db) is True


def test_enqueue_dedupe():
    with _with_index_db() as db:
        engine = collector.Collector(path=db)
        assert engine.enqueue("Nike P-6000", 250) is True
        assert engine.enqueue("nike  p-6000 ", 250) is False  # même seed replié
        assert engine.enqueue("On Cloud 5", 250) is True
        status = engine.status()
        assert len(status["queue"]) == 2


def test_parse_seeds_env():
    with patch.dict(os.environ, {"LUXE_RADAR_COLLECTOR_SEEDS": "Nike P-6000|250,On Cloud 5|300"}, clear=False):
        seeds = collector.parse_seeds()
        assert seeds[0] == ("Nike P-6000", 250.0)
        assert seeds[1] == ("On Cloud 5", 300.0)
    with patch.dict(os.environ, {"LUXE_RADAR_COLLECTOR_SEEDS": '[["Stone Island",300],["Nike",250]]'}, clear=False):
        seeds = collector.parse_seeds()
        assert seeds == [("Stone Island", 300.0), ("Nike", 250.0)]


def test_deep_page_limit():
    fake_ebay = _FakeConnector("eBay", [])
    assert collector.deep_page_limit(fake_ebay) == 200
    fake_retail = _FakeConnector("i-Run", [], supports_pagination=True)
    assert collector.deep_page_limit(fake_retail) == 100
    fake_flat = _FakeConnector("ASOS", [], supports_pagination=False, expansion_page_size=60)
    assert collector.deep_page_limit(fake_flat) == 60


def test_index_spillover():
    """Quand le token est épuisé mais que l'index contient d'autres offres,
    la pagination continue depuis l'index, dédupliquée contre le token."""
    with _with_index_db() as db:
        offers = [_make_offer(i, marketplace="eBay") for i in range(1, 7)]
        index_engine.upsert_results(offers, "Nike P-6000", path=db)
        from app_web import _index_spillover
        token_results = offers[:2]
        entry = {
            "search_query": "Nike P-6000",
            "search_price": None,
            "index_total": 6,
            "results": token_results,
        }
        spilled = _index_spillover(entry, token_results, offset=len(token_results), limit=10)
        assert spilled is not None
        known = {index_engine._offer_key(item) for item in token_results}
        fresh = [index_engine._offer_key(item) for item in spilled["results"]]
        assert len(fresh) == 4, fresh
        assert not (set(fresh) & known)
        assert spilled["total"] == 6
        assert spilled["has_more"] is False
        assert index_engine.count_query_offers("Nike P-6000", path=db)["exact"] == 6


def _main():
    import sys
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_") and callable(value)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"OK  {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests OK")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    _main()
