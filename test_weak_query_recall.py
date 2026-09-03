from app_web import _background_query_variants
from relevance_gate import evaluate_offer


def main():
    expected = {
        "Fourteen": "Fourteen tracksuit",
        "maillot Cameroun": "Cameroon national team jersey",
        "pull Ralph Lauren": "Polo Ralph Lauren knit",
        "sac Jacquemus": "Jacquemus Le Chiquito bag",
        "chaussures Gucci": "Gucci loafers",
    }
    for query, variant in expected.items():
        variants = _background_query_variants(query)
        assert variant in variants, (query, variants)
        assert len(variants) <= 5, (query, variants)

    positives = (
        ("Fourteen", "FOURTEEN tracksuit football homme"),
        ("maillot Cameroun", "Cameroon national team jersey 2026"),
        ("pull Ralph Lauren", "Polo Ralph Lauren cable knit cardigan"),
        ("sac Jacquemus", "Jacquemus Le Chiquito leather crossbody bag"),
        ("chaussures Gucci", "Gucci horsebit leather loafers"),
    )
    negatives = (
        ("Fourteen", "Fourteen classic poems paperback book"),
        ("maillot Cameroun", "France national team jersey 2026"),
        ("pull Ralph Lauren", "Ralph Lauren cotton shirt"),
        ("sac Jacquemus", "Jacquemus logo T-shirt"),
        ("chaussures Gucci", "Gucci leather handbag"),
    )
    for query, title in positives:
        assert evaluate_offer(query, {"titre": title}).accepted, (query, title)
    for query, title in negatives:
        assert not evaluate_offer(query, {"titre": title}).accepted, (query, title)
    print("OK - rappel ciblé des cinq recherches faibles sans faux positifs")


if __name__ == "__main__":
    main()
