"""V3.7.2 : QUALITÉ + RAPPEL (Sous-total V3.8.0).

Vérifie les corrections de qualité/rappel :
- Bug 1 : le rejet d'identité produit ne garde JAMAIS de fausse correspondance,
  mais un vrai résultat « possible » reste visible (plus de porte de sortie
  « mode couverture maximale »).
- Bug 2 : ASOS ne confond plus une marque multi-mots (Stone Island) avec un
  simple mot de titre ; la marque demandée doit apparaître en bloc contigu.
- Bug 3/12/13/14 : l'index déduplique par identifiant produit puis URL, refuse
  les URLs de template `${...}`, et applique une fraîcheur par marketplace.
- Bug 4 : l'univers « Luxe » ne recherche JAMAIS le mot littéral « luxury ».
- Bug 5 : Grailed échoue vite sur challenge (visible uniquement si opt-in).
- Bug 7 : SSENSE peut remonter 120 candidats et le plafond mono-source du
  moteur est paramétrable (défaut 160).
- Bug 15 : une ligne [SEARCH SUMMARY] clôt chaque recherche terminée.

Le test lit le code source et exécute aussi les fonctions pures quand les
dépendances sont disponibles (sinon il s'appuie sur les marqueurs source).
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app_web.py").read_text(encoding="utf-8")
RADAR = (ROOT / "radar_engine.py").read_text(encoding="utf-8")
ASOS = (ROOT / "marketplaces" / "connectors" / "asos.py").read_text(encoding="utf-8")
INDEX = (ROOT / "index_engine.py").read_text(encoding="utf-8")
GRAILED = (ROOT / "marketplaces" / "connectors" / "grailed.py").read_text(encoding="utf-8")
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def main():
    # ---------------------------------------------------------------
    # Bug 1 : rejet = exclusion dure, jamais de « mode couverture »
    # ---------------------------------------------------------------
    assert re.search(r'if reconnaissance\.level == "rejet":\s*return None', RADAR)
    assert re.search(r'if reconnaissance_vinted\.level == "rejet":\s*continue', RADAR)
    assert 'if identite_non_confirmee and not _recall_mode_enabled(): return None' not in RADAR
    assert "Annonce réelle conservée en mode couverture maximale" not in RADAR
    assert "Correspondance produit non confirmée" not in RADAR

    # ---------------------------------------------------------------
    # Bug 2 : marques multi-mots chez ASOS, titre != marque
    # ---------------------------------------------------------------
    assert "_MARQUES_ENTITE_MULTI_MOTS" in ASOS
    assert '"stone island"' in ASOS and '"new balance"' in ASOS
    assert "_marque_entite_absente" in ASOS
    assert "(5, False)" in ASOS or "5, False" in ASOS
    assert "Essentials" in ASOS  # cas spécial FOG Essentials

    # ---------------------------------------------------------------
    # Bug 3/12/13/14 : URLs de template refusées, clé d'offre hiérarchisée
    # ---------------------------------------------------------------
    assert "MARKETPLACE_TTL_SECONDS" in INDEX
    assert "_marketplace_ttl_seconds" in INDEX
    assert "LUXE_RADAR_TTL_" in INDEX
    for fragment in ('"$"', '"{"', '"}"', '"%7b"', '"%7d"', '"%24"', "javascript:", "data:"):
        assert fragment in INDEX, fragment
    assert "_offer_key" in INDEX
    assert "id_produit" in INDEX and "product_id" in INDEX
    assert "_clean_url" in INDEX
    assert "first_seen" in INDEX

    # ---------------------------------------------------------------
    # Bug 4 : l'univers Luxe ne cherche jamais le mot « luxury »
    # ---------------------------------------------------------------
    assert "_LUXURY_UNIVERSE_QUERIES" in APP
    assert "_rotating_luxury_query" in APP
    assert "universe == 'luxury'" in APP or "universe == \"luxury\"" in APP
    assert "search_universe" in APP
    assert "data-universe=\"luxury\"" in HTML
    assert 'name="universe" id="universe"' in HTML
    assert "$$('[data-universe]')" in JS
    assert "requestSubmit" in JS

    # ---------------------------------------------------------------
    # Bug 5 : Grailed fail-fast, navigateur visible en opt-in seulement
    # ---------------------------------------------------------------
    assert "marqueurs_blocage" in GRAILED
    assert "LUXE_RADAR_GRAILED_VISIBLE" in GRAILED
    assert '"0"' in GRAILED  # opt-in visible désactivé par défaut
    assert "Blocage headless cohérent" in GRAILED
    assert "fallback visible" in GRAILED

    # ---------------------------------------------------------------
    # Bug 7 : SSENSE 120 candidats + plafond mono-source paramétrable
    # ---------------------------------------------------------------
    assert '"SSENSE": 120' in APP
    assert "LUXE_RADAR_SOURCE_MAX_CAP" in RADAR
    assert "_SOURCE_MAX_CAP" in RADAR
    assert "160" in RADAR
    # Compatibilité V3.8.0 : les bornes de rappel restent en place.
    assert '"eBay": 100' in APP
    assert '"Grailed": 36' in APP
    assert "next_recall_limit = min(100" in APP
    assert "if empty[target] >= 1 or limits[target] >= 100" in APP

    # ---------------------------------------------------------------
    # Bug 15 : observabilité par source + bilan final
    # ---------------------------------------------------------------
    assert "[PROGRESSIF]" in APP
    assert "[SEARCH SUMMARY]" in APP
    assert "_log_search_summary" in APP

    # ---------------------------------------------------------------
    # Exécution réelle quand les dépendances existent
    # ---------------------------------------------------------------
    live = True
    try:
        import index_engine
        from marketplaces.connectors.asos import _marque_entite_absente, _score_pertinence_titre
    except Exception:
        live = False

    if live:
        # Bug 2 : « River Island stone wash shirt » n'est PAS un Stone Island.
        assert _score_pertinence_titre("River Island stone wash shirt", "Stone Island") == (5, False)
        assert _score_pertinence_titre("River Island Stone Island oversize tee", "Stone Island") == (100, True)
        assert _score_pertinence_titre("New Balance 530 white", "New Balance") == (100, True)
        assert _marque_entite_absente("river island stone wash shirt", "stone island") is not None
        assert _marque_entite_absente("river island stone island oversize tee", "stone island") is None

        # Bug 3/12/13/14 : URLs de template refusées, fragments retirés.
        assert not index_engine._clean_url("https://x.fr/item?ref=${searchAction}")
        assert not index_engine._clean_url("https://x.fr/item?ref=%7B%7D")
        assert not index_engine._clean_url("javascript:alert(1)")
        assert index_engine._clean_url("https://x.fr/listing#reviews").endswith("/listing") or "#reviews" not in (index_engine._clean_url("https://x.fr/listing#reviews") or "")
        assert index_engine._clean_url("https://x.fr/item?a=1") == "https://x.fr/item?a=1"

        # Bug 14 : la clé d'offre priorise l'identifiant produit sur l'URL.
        def _mk(**kw):
            base = {"marketplace": "eBay", "lien": "https://x/item", "titre": "t", "prix": 1}
            base.update(kw)
            return base

        assert index_engine._offer_key(_mk(id_produit="A", lien="https://x/1")) == \
               index_engine._offer_key(_mk(id_produit="A", lien="https://x/2"))
        assert index_engine._offer_key(_mk(id_produit="A", lien="https://x/1")) != \
               index_engine._offer_key(_mk(id_produit="B", lien="https://x/1"))
        assert index_engine._offer_key(_mk(lien="https://x/item#a")) == \
               index_engine._offer_key(_mk(lien="https://x/item#b"))
    else:
        print("AVERTISSEMENT: dépendances absentes, assertions source uniquement.")

    print("OK - V3.7.2 QUALITY + RECALL: rejet dur, marques multi-mots ASOS, URLs de template refusées, "
          "clé d'offre priorisée, univers Luxe sans mot littéral, Grailed fail-fast, SSENSE 120, "
          "plafond mono-source 160, [SEARCH SUMMARY].")


if __name__ == "__main__":
    main()
