"""V3.7.x INTENT + PERTINENCE : gate central, propriété de sous-ensemble, déterminisme.

Valide les exigences "ZÉRO RÉSULTAT ALÉATOIRE" :
- ``casquette Nike Trail`` ⊂ ``Nike Trail`` (propriété de sous-ensemble) ;
- faux positifs rejetés : Air Force 1 pour ``Nike P-6000``, River Island pour
  ``Stone Island``, ``On Running Cloudstratus`` pour ``On Cloud 5`` ;
- faux négatifs évités : ``Nike Pegasus Trail 5`` reste pertinent pour
  ``Nike Trail`` ;
- ordre total déterministe : deux bases identiques => même classement.
"""

from pathlib import Path
import tempfile

import index_engine
from relevance_gate import evaluate_offer
from search_intent import parse_search_intent


def offer(titre, query, marketplace='eBay', i=0, price=120.0, level='fort'):
    return {
        'marketplace': marketplace,
        'titre': titre,
        'prix': price,
        'prix_total': price,
        'devise': 'EUR',
        'lien': f'https://example.test/p/{abs(hash(titre)) % 10**9}',
        'image': f'https://example.test/img/{i}.jpg',
        'score_identite': 88 if level == 'fort' else 55,
        'niveau_identite': level,
        'score': 80,
        'score_confiance': 85,
        'risque_contrefacon': 'faible',
    }


def _keys(results):
    return sorted(str(item.get('lien') or item.get('url')) for item in results)


def _upsert(db, pairs):
    for query, items in pairs:
        index_engine.upsert_results(items, query, path=db)


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)

    # --- Gate unitaire : matrice pertinent / non pertinent -------------------
    cases = [
        ('Nike Trail', 'Nike ReactX Pegasus Trail 5', True),
        ('Nike Trail', 'Nike Air Force 1', False),
        ('Nike P-6000', 'Nike Air Force 1', False),
        ('Nike P-6000', 'Nike P6000', True),
        ('casquette Nike', 'Nike Club casquette', True),
        ('casquette Nike', 'Nike Air Force 1', False),
        ('casquette Nike Trail', 'Nike Trail casquette', True),
        ('casquette Nike Trail', 'Nike Trail running shoe', False),
        ('casquette Nike Trail', 'Nike Pegasus Trail 5 chaussures', False),
        ('Stone Island', 'River Island stone trousers', False),
        ('Stone Island', 'Stone Island veste modèle 42', True),
        ('On Cloud 5', 'On Cloud 5 Waterproof', True),
        ('On Cloud 5', 'On Running Cloudstratus', False),
    ]
    for query, titre, want in cases:
        got = evaluate_offer(query, offer(titre, query)).accepted
        assert got is want, f'GATE {query!r} / {titre!r}: attendu {want}, obtenu {got}'

    # --- Intent parsée : dimensions correctes ----------------------------------
    intent = parse_search_intent('casquette Nike Trail')
    assert (intent.brand, intent.line, intent.product_type) == ('Nike', 'trail', 'casquette'), intent
    intent = parse_search_intent('Nike P-6000')
    assert intent.model == 'P-6000' and intent.line is None, intent

    # --- Sous-ensemble sur le chemin index (via catalogue global) -------------
    db = base / 'subset.sqlite3'
    broad = [
        offer('Nike Trail casquette noire', 'Nike Trail', i=0),
        offer('Nike Trail running shoe', 'Nike Trail', i=1),
        offer('Nike ReactX Pegasus Trail 5', 'Nike Trail', i=2),
        offer('Nike Trail cap white', 'Nike Trail', i=3),
    ]
    _upsert(db, [('Nike Trail', broad)])
    broad_keys = _keys(index_engine.search('Nike Trail', path=db, limit=500).results)
    assert len(broad_keys) == 4, f'attendu 4, obtenu {len(broad_keys)}'
    narrow = index_engine.search('casquette Nike Trail', path=db, limit=500)
    narrow_keys = _keys(narrow.results)
    assert set(narrow_keys) <= set(broad_keys), 'sous-ensemble cassé'
    expected_narrow = {str(broad[0]['lien']), str(broad[3]['lien'])}
    assert set(narrow_keys) == expected_narrow, f'casquettes attendues, obtenu {narrow_keys}'

    # --- Faux positifs rejetés AU TEMPS DE LA RECHERCHE (lignes exact-query) ---
    db2 = base / 'fp.sqlite3'
    _upsert(db2, [
        ('Nike P-6000', [offer('Nike Air Force 1', 'Nike P-6000', i=0)]),
        ('Stone Island', [offer('River Island stone trousers', 'Stone Island', i=1)]),
        ('casquette Nike', [offer('Nike Air Force 1', 'casquette Nike', i=2)]),
        ('On Cloud 5', [offer('On Running Cloudstratus', 'On Cloud 5', i=3)]),
        ('casquette Nike Trail', [offer('Nike Pegasus Trail 5 chaussures', 'casquette Nike Trail', i=4)]),
    ])
    assert index_engine.search('Nike P-6000', path=db2).total == 0
    assert index_engine.search('Stone Island', path=db2).total == 0
    assert index_engine.search('casquette Nike', path=db2).total == 0
    assert index_engine.search('On Cloud 5', path=db2).total == 0
    assert index_engine.search('casquette Nike Trail', path=db2).total == 0

    # --- Faux négatifs évités : l'offre pertinente reste visible ----------------
    db3 = base / 'fn.sqlite3'
    _upsert(db3, [('Nike Trail', [offer('Nike ReactX Pegasus Trail 5', 'Nike Trail', i=0, price=140.0)])])
    assert index_engine.search('Nike Trail', path=db3).total == 1

    # --- Référence produit : pas de rejet abusif --------------------------------
    db4 = base / 'ref.sqlite3'
    _upsert(db4, [('DM4652-040', [offer('Nike Dunk Low DM4652 040', 'DM4652-040', i=0)])])
    assert index_engine.search('DM4652-040', path=db4).total == 1

    # --- Déterminisme : deux bases identiques => même ordre ---------------------
    def build_db(root):
        d = root / 'det.sqlite3'
        rows = [
            offer('Nike Trail cap', 'Nike Trail', i=0, price=25.0),
            offer('Nike Trail running shoe', 'Nike Trail', i=1, price=90.0),
            offer('Nike ReactX Pegasus Trail 5', 'Nike Trail', i=2, price=140.0),
            offer('Nike Trail casquette noire', 'Nike Trail', i=3, price=19.0),
        ]
        index_engine.upsert_results(rows, 'Nike Trail', path=d)
        return d

    d_a = build_db(base / 'a')
    d_b = build_db(base / 'b')
    for q in ('Nike Trail', 'Nike P-6000', 'casquette Nike Trail'):
        seq_a = [str(r.get('lien')) for r in index_engine.search(q, path=d_a, limit=500).results]
        seq_b = [str(r.get('lien')) for r in index_engine.search(q, path=d_b, limit=500).results]
        assert seq_a == seq_b, f'ordre non déterministe pour {q!r}'

print('OK - V3.7.3 INTENT + PERTINENCE: gate 13 cas, sous-ensemble, faux +/- , références et déterminisme validés.')
