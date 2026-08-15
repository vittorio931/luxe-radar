from pathlib import Path
import tempfile
import time

import index_engine


def offer(i, query='Stone Island', price=None, level='fort'):
    price = float(price if price is not None else 100 + (i % 400))
    return {
        'marketplace': 'TestShop' if i % 2 else 'eBay',
        'titre': f'{query} veste modèle {i}',
        'prix': price,
        'prix_total': price,
        'devise': 'EUR',
        'lien': f'https://example.test/p/{i}',
        'image': f'https://example.test/img/{i}.jpg',
        'score_identite': 88 if level == 'fort' else 55,
        'niveau_identite': level,
        'score': 80,
        'score_confiance': 85,
        'risque_contrefacon': 'faible',
    }

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / 'index.sqlite3'
    rows = [offer(i) for i in range(3300)]
    written = index_engine.upsert_results(rows, 'Stone Island', path=db)
    assert written == 3300

    first = index_engine.search('Stone Island', path=db, limit=50, identity='confirmed')
    assert first.total == 3300 and len(first.results) == 50
    assert all(item.get('_index_cached') for item in first.results)

    # 63+ pages are possible without contacting a marketplace.
    page_64 = index_engine.search('Stone Island', path=db, limit=50, offset=3150, identity='confirmed')
    assert page_64.total == 3300 and len(page_64.results) == 50

    # Same listing URL updates instead of duplicating when the price changes.
    changed = offer(10, price=59.99)
    index_engine.upsert_results([changed], 'Stone Island', path=db)
    after = index_engine.search('Stone Island', path=db, limit=5000, identity='confirmed')
    assert after.total == 3300
    matching = [item for item in after.results if item.get('lien') == changed['lien']]
    assert len(matching) == 1 and abs(float(matching[0]['prix']) - 59.99) < 0.001

    # Query isolation: no cross-query relevance leakage.
    index_engine.upsert_results([offer(99999, query='On Cloud 5')], 'On Cloud 5', path=db)
    cloud = index_engine.search('On Cloud 5', path=db, limit=50)
    assert cloud.total == 1 and 'On Cloud 5' in cloud.results[0]['titre']

    # Stale entries are ignored by the live search window.
    stale_db = Path(tmp) / 'stale.sqlite3'
    index_engine.upsert_results([offer(1)], 'Stone Island', path=stale_db, now=time.time() - 7200)
    stale = index_engine.search('Stone Island', path=stale_db, max_age=60)
    assert stale.total == 0

print('OK - V3.6.0 index engine: 3300 offers, 64th page, upsert, query isolation and freshness validated.')
