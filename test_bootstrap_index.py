import tempfile
from pathlib import Path

import bootstrap_index
import index_engine


def main():
    assert bootstrap_index.SNAPSHOT.exists()
    with tempfile.TemporaryDirectory(prefix="lr_bootstrap_") as folder:
        target = Path(folder) / "index.sqlite3"
        old_done = bootstrap_index._DONE
        try:
            bootstrap_index._DONE = False
            result = bootstrap_index.ensure_bootstrap_index(path=target)
            assert result["loaded"] is True
            counts = index_engine.count_query_offers("Balenciaga", path=target)
            assert counts["exact"] >= 1000, counts
            search = index_engine.search("Balenciaga", limit=50, path=target)
            assert search.total >= 1000, search.total
            assert len(search.results) == 50
            nike_trail = index_engine.search("Nike Trail", limit=50, path=target)
            assert nike_trail.total >= 1000, nike_trail.total
        finally:
            bootstrap_index._DONE = old_done
    print("OK - bootstrap public Balenciaga 1000+ restauré sur une base vide.")


if __name__ == "__main__":
    main()
