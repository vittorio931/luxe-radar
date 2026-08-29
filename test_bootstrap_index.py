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
            moncler = index_engine.search("Moncler", limit=50, path=target)
            assert moncler.total >= 2000, moncler.total
            assert {item["marketplace"] for item in moncler.results} & {"SSENSE", "The Outnet", "eBay"}
            columbia = index_engine.search("pantalon Columbia", limit=50, path=target)
            assert columbia.total >= 200, columbia.total
            assert all("columbia" in str(item.get("titre") or "").casefold() for item in columbia.results)
            tech_wind_offer = {
                "marketplace": "eBay", "titre": "Columbia Tech Wind jacket",
                "prix": 80, "score": 90, "score_confiance": 90,
                "niveau_identite": "fort", "score_identite": 95,
                "lien": "https://example.test/columbia-tech-wind",
            }
            index_engine.upsert_results([tech_wind_offer], "Columbia", path=target)
            broad_columbia = index_engine.search("Columbia", limit=50, path=target)
            refined_columbia = index_engine.search("Columbia Tech Wind", limit=50, path=target)
            media_words = ("vinyl", "vinyle", " lp ", " cd ", "album", "disque", "record")
            assert all(
                not any(word in f" {str(item.get('titre') or '').casefold()} " for word in media_words)
                for item in broad_columbia.results[:20]
            )
            assert 0 < refined_columbia.total < broad_columbia.total
            assert any(item.get("lien") == tech_wind_offer["lien"] for item in refined_columbia.results)
            assert all(
                "tech" in str(item.get("titre") or "").casefold()
                and "wind" in str(item.get("titre") or "").casefold()
                for item in refined_columbia.results
            )
            live_offer = {
                "marketplace": "eBay", "titre": "Marque Rare modèle instantané",
                "prix": 123, "score": 90, "score_confiance": 80,
                "niveau_identite": "fort", "score_identite": 95,
                "lien": "https://example.test/marque-rare-instantanee",
            }
            assert index_engine.upsert_results([live_offer], "Marque Rare", path=target) == 1
            first = index_engine.search("Marque Rare", limit=50, path=target)
            second = index_engine.search("Marque Rare", limit=50, path=target)
            assert first.total >= 1 and second.total == first.total
            assert first.results[0]["lien"] == second.results[0]["lien"]
            # Simulation d'une perte du disque éphémère alors que le processus
            # pense avoir fini son bootstrap : l'appel suivant doit réparer.
            target.unlink()
            assert bootstrap_index._DONE is True
            repaired = bootstrap_index.ensure_bootstrap_index(path=target)
            assert repaired["loaded"] is True, repaired
            assert index_engine.search("Balenciaga", limit=1, path=target).total >= 1000
        finally:
            bootstrap_index._DONE = old_done
    print("OK - bootstrap public Balenciaga 1000+ restauré sur une base vide.")


if __name__ == "__main__":
    main()
