"""V3.7.x INTENT + PERTINENCE : gate central, propriÃ©tÃ© de sous-ensemble, dÃ©terminisme.

Valide les exigences "ZÃ‰RO RÃ‰SULTAT ALÃ‰ATOIRE" :
- ``casquette Nike Trail`` âŠ‚ ``Nike Trail`` (propriÃ©tÃ© de sous-ensemble) ;
- faux positifs rejetÃ©s : Air Force 1 pour ``Nike P-6000``, River Island pour
  ``Stone Island``, ``On Running Cloudstratus`` pour ``On Cloud 5`` ;
- faux nÃ©gatifs Ã©vitÃ©s : ``Nike Pegasus Trail 5`` reste pertinent pour
  ``Nike Trail`` ;
- ordre total dÃ©terministe : deux bases identiques => mÃªme classement.
"""

from pathlib import Path
import tempfile

import index_engine
from relevance_gate import evaluate_offer
from search_intent import parse_search_intent
from product_recognition import build_query_profile, recognize_product


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
        ('Stone Island', 'Stone Island veste modÃ¨le 42', True),
        ('On Cloud 5', 'On Cloud 5 Waterproof', True),
        ('On Cloud 5', 'On Running Cloudstratus', False),

        # V3.7.5 : un mod?le chaussure ne doit pas accepter un v?tement
        # uniquement parce que le nom du mod?le appara?t dans le titre.
        ('Air Force 1', "Nike Air Force 1 '07", True),
        ('Air Force 1', "Nike Swoosh Women's Air Force 1 Sports Bra", False),
        ('Samba', 'adidas Samba Hoodie', False),
        ('XT-6', 'Salomon XT-6 Jacket', False),
        ('P-6000', 'Nike P-6000 Socks', False),
        # Recherche famille : ces noms commerciaux décrivent bien des
        # pantalons Nike trail même lorsque le titre omet le mot « Trail ».
        ('pantalon Nike Trail', 'Nike ACG Dawn Range Dri-FIT Pants', True),
        ('pantalon Nike Trail', 'Nike Phenom Elite Running Pants', True),
        ('pantalon Nike Trail', 'Nike Storm-FIT ACG trousers', True),
        ('pantalon Nike Trail', 'Nike ACG hoodie', False),
        ('pantalon Nike Trail', 'Nike Pegasus Trail shoes', False),
        # Une catégorie chaussures générique couvre les libellés internationaux
        # sans accepter les sacs « Basket » ni les emballages/accessoires.
        ('chaussure Balenciaga', 'Balenciaga Triple S Sneakers', True),
        ('chaussure Balenciaga', 'Balenciaga Track Trainers', True),
        ('chaussure Balenciaga', 'Balenciaga Bistro Basket Sac XXS', False),
        ('chaussure Balenciaga', 'Boite a chaussures Balenciaga', False),
        ('chaussure Balenciaga', 'Balenciaga Alaska Boot Keychain', False),
    ]
    for query, titre, want in cases:
        got = evaluate_offer(query, offer(titre, query)).accepted
        assert got is want, f'GATE {query!r} / {titre!r}: attendu {want}, obtenu {got}'

    # --- Intent parsÃ©e : dimensions correctes ----------------------------------
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
    assert set(narrow_keys) <= set(broad_keys), 'sous-ensemble cassÃ©'
    expected_narrow = {str(broad[0]['lien']), str(broad[3]['lien'])}
    assert set(narrow_keys) == expected_narrow, f'casquettes attendues, obtenu {narrow_keys}'

    # --- Faux positifs rejetÃ©s AU TEMPS DE LA RECHERCHE (lignes exact-query) ---
    db2 = base / 'fp.sqlite3'
    _upsert(db2, [
        ('Nike P-6000', [offer('Nike Air Force 1', 'Nike P-6000', i=0)]),
        ('Stone Island', [offer('River Island stone trousers', 'Stone Island', i=1)]),
        ('casquette Nike', [offer('Nike Air Force 1', 'casquette Nike', i=2)]),
        ('On Cloud 5', [offer('On Running Cloudstratus', 'On Cloud 5', i=3)]),
        ('casquette Nike Trail', [offer('Nike Pegasus Trail 5 chaussures', 'casquette Nike Trail', i=4)]),
        ('Air Force 1', [offer("Nike Swoosh Women's Air Force 1 Sports Bra", 'Air Force 1', i=5)]),
    ])
    assert index_engine.search('Nike P-6000', path=db2).total == 0
    assert index_engine.search('Stone Island', path=db2).total == 0
    assert index_engine.search('casquette Nike', path=db2).total == 0
    assert index_engine.search('On Cloud 5', path=db2).total == 0
    assert index_engine.search('casquette Nike Trail', path=db2).total == 0
    air_force_results = index_engine.search('Air Force 1', path=db2, limit=500).results
    air_force_titles = [str(item.get('titre') or '') for item in air_force_results]
    assert air_force_results, 'les vraies Air Force 1 doivent rester visibles'
    assert not any('sports bra' in title.casefold() for title in air_force_titles), air_force_titles

    # --- Faux nÃ©gatifs Ã©vitÃ©s : l'offre pertinente reste visible ----------------
    db3 = base / 'fn.sqlite3'
    _upsert(db3, [('Nike Trail', [offer('Nike ReactX Pegasus Trail 5', 'Nike Trail', i=0, price=140.0)])])
    assert index_engine.search('Nike Trail', path=db3).total == 1

    # Le retrieval sémantique doit chercher au-delà du mot littéral « trail »
    # dans le catalogue, puis conserver uniquement la bonne famille + catégorie.
    semantic = [
        offer('Nike ACG Dawn Range Dri-FIT Pants', 'Nike ACG', i=10),
        offer('Nike Phenom Elite Running Pants', 'Nike Phenom Elite', i=11),
        offer('Nike ACG hoodie', 'Nike ACG', i=12),
        offer('Nike Pegasus Trail shoes', 'Nike Trail', i=13),
    ]
    _upsert(db3, [('Nike ACG', semantic[:1] + semantic[2:3]),
                  ('Nike Phenom Elite', semantic[1:2]),
                  ('Nike Trail', semantic[3:])])
    semantic_results = index_engine.search('pantalon Nike Trail', path=db3, limit=500).results
    semantic_titles = {str(item.get('titre') or '') for item in semantic_results}
    assert 'Nike ACG Dawn Range Dri-FIT Pants' in semantic_titles
    assert 'Nike Phenom Elite Running Pants' in semantic_titles
    assert 'Nike ACG hoodie' not in semantic_titles
    assert 'Nike Pegasus Trail shoes' not in semantic_titles

    # Le catalogue global doit réutiliser les annonces anglaises quand la
    # recherche utilisateur demande la catégorie générique en français.
    footwear = [
        offer('Balenciaga Triple S Sneakers', 'Balenciaga sneakers', i=20),
        offer('Balenciaga Track Trainers', 'Balenciaga trainers', i=21),
        offer('Balenciaga Bistro Basket Sac XXS', 'Balenciaga sac', i=22),
    ]
    _upsert(db3, [('Balenciaga sneakers', footwear[:1]),
                  ('Balenciaga trainers', footwear[1:2]),
                  ('Balenciaga sac', footwear[2:])])
    footwear_titles = {
        str(item.get('titre') or '')
        for item in index_engine.search('chaussure Balenciaga', path=db3, limit=500).results
    }
    assert footwear_titles == {
        'Balenciaga Triple S Sneakers',
        'Balenciaga Track Trainers',
    }, footwear_titles

    # --- RÃ©fÃ©rence produit : pas de rejet abusif --------------------------------
    db4 = base / 'ref.sqlite3'
    _upsert(db4, [('DM4652-040', [offer('Nike Dunk Low DM4652 040', 'DM4652-040', i=0)])])
    assert index_engine.search('DM4652-040', path=db4).total == 1

    # --- DÃ©terminisme : deux bases identiques => mÃªme ordre ---------------------
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
        assert seq_a == seq_b, f'ordre non dÃ©terministe pour {q!r}'

# ModÃ¨les connus recherchÃ©s sans marque : infÃ©rence exacte et non ambiguÃ«.
for raw, expected_brand, expected_model in (
    ("Air Force 1", "Nike", "Air Force 1"),
    ("Samba", "Adidas", "Samba"),
    ("XT-6", "Salomon", "XT-6"),
    ("P-6000", "Nike", "P-6000"),
):
    inferred = parse_search_intent(raw)
    assert inferred.brand == expected_brand, (raw, inferred)
    assert inferred.model == expected_model, (raw, inferred)
    assert inferred.is_reference is False, (raw, inferred)

# Ne pas transformer un type/gamme gÃ©nÃ©rique ou une vraie rÃ©fÃ©rence en modÃ¨le.
assert parse_search_intent("polo").brand is None
assert parse_search_intent("Trail").brand is None
assert parse_search_intent("DM4652-040").is_reference is True

# Le chemin live (radar_engine/product_recognition) doit partager la mÃªme
# comprÃ©hension que SearchIntent. C'Ã©tait la source du bug oÃ¹ ``Air Force 1``
# Ã©tait correctement compris dans le rÃ©sumÃ© de recherche mais rejetÃ© Ã  38/100
# par ASOS/Zalando/eBay/Courir.
for raw, expected_brand, expected_model, good_title, bad_title in (
    ("Air Force 1", "Nike", "Air Force 1", "Nike Air Force 1 '07 Sneakers", "Nike Air Zoom Victory 2"),
    ("Samba", "Adidas", "Samba", "adidas Samba OG Shoes", "adidas Ultraboost Shoes"),
    ("XT-6", "Salomon", "XT-6", "Salomon XT-6 Sneakers", "Salomon Speedcross 6"),
    ("P-6000", "Nike", "P-6000", "Nike P-6000 Shoes", "Nike Air Force 1 Shoes"),
):
    profile = build_query_profile(raw)
    assert (profile.brand, profile.model) == (expected_brand, expected_model), (raw, profile)
    good = recognize_product(raw, good_title, marketplace="eBay")
    assert good.accepted and good.level == "fort", (raw, good)
    bad = recognize_product(raw, bad_title, marketplace="eBay")
    assert not bad.accepted, (raw, bad)

# Les termes gÃ©nÃ©riques et vraies rÃ©fÃ©rences ne doivent toujours pas Ãªtre
# transformÃ©s en marque/modÃ¨le par le moteur live.
assert build_query_profile("Trail").brand is None
assert build_query_profile("Trail").model is None
assert build_query_profile("polo").brand is None
assert build_query_profile("DM4652-040").brand is None
assert build_query_profile("DM4652-040").model is None

# Une marque non encore cataloguée reste recherchable par preuve textuelle
# exacte, sans laisser passer une marque concurrente.
for raw, good_title in (
    ("Loewe", "Sac Loewe Puzzle cuir"),
    ("Goyard", "Sac Goyard Saint Louis PM"),
    ("Brunello Cucinelli", "Pull Brunello Cucinelli cachemire"),
    ("Loewe pantalon", "Pantalon Loewe homme"),
):
    assert recognize_product(raw, good_title, marketplace="eBay").accepted
assert not recognize_product("Loewe", "Sac Gucci Marmont", marketplace="eBay").accepted

print('OK - V3.7.5 INTENT + PERTINENCE: gate 18 cas + modÃ¨les sans marque cohÃ©rents index/live, rÃ©fÃ©rences et dÃ©terminisme validÃ©s.')

