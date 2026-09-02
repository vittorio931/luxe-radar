from app_web import _background_query_variants
from relevance_gate import evaluate_offer
from search_intent import parse_search_intent
from search_understanding import understand_query


fourteen = understand_query("Fourteen")
assert fourteen.brand == "Fourteen", fourteen
assert parse_search_intent("Fourteen").brand == "Fourteen"
fourteen_variants = _background_query_variants("Fourteen")
assert "Fourteen jersey" in fourteen_variants, fourteen_variants
assert not evaluate_offer("Fourteen", {"titre": "The Fourteen Sisters, used book"}).accepted
assert evaluate_offer("Fourteen", {"titre": "FOURTEEN football jersey mens"}).accepted

intent = parse_search_intent("maillot de foot du Cameroun")
assert intent.product_type == "maillot", intent
assert intent.line == "soccer", intent

variants = _background_query_variants("maillot de foot du Cameroun")
assert "Cameroon football jersey" in variants, variants
assert "Cameroon football shirt" in variants, variants

for title in (
    "Cameroon 2026 Home Football Shirt",
    "Cameroon National Team Soccer Jersey",
    "Cameroun maillot de football domicile",
):
    result = evaluate_offer(intent, {"titre": title})
    assert result.accepted, (title, result)

assert not evaluate_offer(intent, {"titre": "France 2026 Home Football Shirt"}).accepted

print("OK - Fourteen reconnu et rappel maillots Cameroun FR/EN validé.")
