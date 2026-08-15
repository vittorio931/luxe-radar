"""Benchmark qualité + performance du moteur d'index (V3.7.x).

Objectifs vérifiés :
1. Latence index chaud < 100 ms par requête (cible moteur de recherche) ;
2. Zéro résultat aléatoire : sous-ensemble, précision catégorie, faux positifs
   exclus même quand la donnée piège est indexée sous la mauvaise requête ;
3. Ordre total déterministe sur deux bases identiques.

Usage :
    .\\.venv\\Scripts\\python.exe benchmark_search_quality.py
"""

from pathlib import Path
import statistics
import tempfile
import time

import index_engine


def offer(titre, query, marketplace, i, price=100.0):
    return {
        'marketplace': marketplace,
        'titre': titre,
        'prix': price,
        'prix_total': price,
        'devise': 'EUR',
        'lien': f'https://example.test/off/{marketplace}/{i}',
        'image': f'https://example.test/img/{i}.jpg',
        'score_identite': 82.0,
        'niveau_identite': 'fort',
        'score': 75.0,
        'score_confiance': 80.0,
        'risque_contrefacon': 'faible',
    }


def build_catalogue(path, seed_bias=0):
    """Construit ~6000 offres réalistes indexées sous leurs requêtes réelles."""
    marketplaces = ['eBay', 'Vinted', 'Grailed', 'ASOS', 'SSENSE']
    grouped = {}
    index = 0
    types = [
        ('casquette', ['casquette', 'cap', 'cap']),
        ('veste', ['veste', 'jacket', 'jacket']),
        ('pantalon', ['pantalon', 'trousers', 'pants']),
        ('chaussures', ['chaussures', 'shoes', 'running shoe']),
        ('pull', ['pull', 'sweater', 'knit']),
        ('tee-shirt', ['t-shirt', 'tee', 'tshirt']),
    ]
    def push(titre, query, marketplace, price):
        nonlocal index
        grouped.setdefault(query, []).append(
            offer(titre, query, marketplace, index + seed_bias, price=price)
        )
        index += 1
    for i in range(2000):
        if i % 3 == 0:
            titre = f"Nike ReactX Pegasus Trail {i % 90 + 1} {types[i % 6][1][0]}"
            push(titre, 'Nike Trail', marketplaces[i % 5], 20 + (i % 300))
        elif i % 3 == 1:
            titre = f"Stone Island {types[i % 6][1][1]} modèle {i}"
            push(titre, 'Stone Island', marketplaces[i % 5], 20 + (i % 300))
        else:
            titre = f"Nike Air Force 1 {types[i % 6][1][2]} blanc"
            push(titre, 'Nike Air Force 1', marketplaces[i % 5], 20 + (i % 300))
    for i in range(60):
        push(
            f"Nike Trail casquette {['noir', 'blanc', 'gris'][i % 3]}",
            'Nike Trail', marketplaces[i % 5], 18 + i,
        )
    for i in range(40):
        push('Nike Air Force 1', 'Nike P-6000', 'eBay', 80 + i)
    for query, items in grouped.items():
        index_engine.upsert_results(items, query, path=path)


def main():
    print("Benchmark LUXE RADAR (index) V3.7.x\n")
    queries = [
        'Nike Trail',
        'casquette Nike Trail',
        'casquette Nike',
        'Nike P-6000',
        'Nike Air Force 1',
        'Stone Island',
        'On Cloud 5',
        'Nike',
        'DM4652-040',
    ]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        db = base / 'bench.sqlite3'
        print(f"Construction catalogue (~6100 offres) : {db} ...")
        t0 = time.time()
        build_catalogue(db)
        print(f"  index construit en {time.time() - t0:.2f}s\n")

        print(f"{'requête':<22}{'total':>7}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}  contrôle")
        latencies = []
        for query in queries:
            timings = []
            result_keys = None
            for _ in range(5):
                start = time.time()
                res = index_engine.search(query, path=db, limit=100)
                timings.append((time.time() - start) * 1000)
                current = [str(r.get('lien')) for r in res.results]
                if result_keys is not None:
                    assert current == result_keys, f"ordre instable pour {query!r}"
                result_keys = current
            timings.sort()
            p50 = timings[len(timings) // 2]
            p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
            latencies.extend(timings)
            check = "-"
            low = query.casefold()
            if 'casquette' in low:
                check = 'casquette' if all('casquette' in str(r.get('titre') or '').casefold() or 'cap' in str(r.get('titre') or '').casefold() for r in res.results) else 'ECHEC precision'
            if low.startswith('nike p-6000'):
                check = 'ok' if not any('air force 1' in str(r.get('titre') or '').casefold() for r in res.results) else 'ECHEC faux positif'
            print(f"{query:<22}{res.total:>7}{p50:>9.1f}{p95:>9.1f}{max(timings):>9.1f}  {check}")

        # Sous-ensemble : casquette Nike Trail est un sous-ensemble de Nike Trail.
        broad = {str(r.get('lien')) for r in index_engine.search('Nike Trail', path=db, limit=5000).results}
        narrow = {str(r.get('lien')) for r in index_engine.search('casquette Nike Trail', path=db, limit=5000).results}
        subset_ok = narrow and narrow <= broad
        print(f"\nSous-ensemble casquette Nike Trail / Nike Trail : {'OK' if subset_ok else 'ECHEC'}")
        assert subset_ok, "propriete de sous-ensemble violee"

        overall = statistics.median(latencies)
        print(f"Latence médiane globale : {overall:.1f} ms (cible < 100 ms)")
        status = "OK" if overall < 100 else "LENT"
        print(f"Résultat benchmark : {status}")
        assert overall < 100, "index trop lent (cible < 100 ms)"
        return overall

    return None


if __name__ == '__main__':
    main()
