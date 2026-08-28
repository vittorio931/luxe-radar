"""Régressions du vocabulaire international et des modèles luxe V3.8.8."""

from relevance_gate import evaluate_offer
from search_intent import parse_search_intent


def _accepted(query, title):
    return evaluate_offer(query, {
        "titre": title,
        "niveau_identite": "possible",
        "score_identite": 60,
    }).accepted


def main():
    positives = (
        ("sac Jacquemus", "Jacquemus Le Chiquito leather handbag"),
        ("pantalon Balenciaga", "Balenciaga logo leggings black"),
        ("veste Stone Island", "Stone Island nylon bomber jacket"),
        ("pull Ralph Lauren", "Polo Ralph Lauren cable knit cardigan"),
        ("chaussures Gucci", "Gucci leather loafers"),
        ("doudoune The North Face", "The North Face Nuptse puffer jacket"),
    )
    negatives = (
        ("sac Jacquemus", "Jacquemus logo T-shirt"),
        ("pantalon Balenciaga", "Balenciaga Track sneakers"),
        ("veste Stone Island", "Stone Island cargo trousers"),
    )
    for query, title in positives:
        assert _accepted(query, title), (query, title)
    for query, title in negatives:
        assert not _accepted(query, title), (query, title)

    track = parse_search_intent("Balenciaga Track")
    assert track.brand == "Balenciaga" and track.model == "Track", track
    assert _accepted("Balenciaga Track", "Balenciaga Track Sneakers")
    assert not _accepted("Balenciaga Track", "Balenciaga Triple S Sneakers")

    chiquito = parse_search_intent("Jacquemus Chiquito")
    assert chiquito.brand == "Jacquemus" and chiquito.model == "Le Chiquito", chiquito
    print("OK - catégories internationales et modèles luxe précis validés.")


if __name__ == "__main__":
    main()
