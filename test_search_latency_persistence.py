"""Régression : gros token non bloquant et résultats réellement persistés."""

from time import perf_counter, sleep
from unittest.mock import patch
from concurrent.futures import Future

import app_web
import index_engine
import search_sessions


def main():
    for query in ("Nike Trail", "Stone Island", "Adidas Samba"):
        started = perf_counter()
        found = index_engine.search(query, limit=50)
        elapsed = perf_counter() - started
        print(f"INDEX {query}: {found.total} offres en {elapsed:.3f}s")
        assert found.total > 0

    offers = [
        {
            "titre": f"Adidas Samba {index}", "marketplace": "eBay",
            "prix": 80 + index, "niveau_identite": "fort",
            "lien": f"https://example.test/{index}",
        }
        for index in range(600)
    ]
    saved = []

    def slow_save(**payload):
        sleep(1.0)
        saved.append(payload)

    started = perf_counter()
    with patch.object(search_sessions, "save_search_session", side_effect=slow_save):
        token = app_web._cache_results(offers, "owner", search_query="Adidas Samba")
        response_time = perf_counter() - started
        deadline = perf_counter() + 4
        while not saved and perf_counter() < deadline:
            sleep(0.02)

    assert response_time < 0.35, response_time
    assert saved and saved[-1]["token"] == token
    assert len(saved[-1]["state"]["results"]) == len(offers)
    print(f"SESSION: rendu en {response_time:.3f}s, 600 offres persistées en arrière-plan")

    # Le contrat de l'indexeur expose maintenant sa Future : les workers de
    # source peuvent confirmer l'écriture avant de marquer la recherche finie.
    with patch.object(index_engine, "upsert_results", return_value=len(offers)) as upsert:
        future = app_web._index_results_async(offers, "Adidas Samba")
        assert isinstance(future, Future)
        future.result(timeout=3)
        assert upsert.call_count == 1
    print("INDEX: confirmation d'écriture disponible avant clôture de source")


if __name__ == "__main__":
    main()
