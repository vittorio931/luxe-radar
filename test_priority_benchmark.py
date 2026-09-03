from unittest.mock import patch

import benchmark_priority_queries as benchmark
import index_engine


def test_audit_diagnoses_zero_and_diverse_results():
    with patch.object(
        index_engine, "search",
        return_value=index_engine.IndexSearch([], 0, None, "fourteen"),
    ):
        assert benchmark.audit_query("Fourteen")["diagnosis"] == "ZERO"

    offers = [
        {"marketplace": "eBay", "titre": "On Cloud 5", "lien": "https://e.test/1"},
        {"marketplace": "Vinted", "titre": "On Cloud 5", "lien": "https://v.test/1"},
    ]
    with patch.object(
        index_engine, "search",
        return_value=index_engine.IndexSearch(offers, 250, 0.0, "on cloud 5"),
    ):
        row = benchmark.audit_query("On Cloud 5")
        assert row["diagnosis"] == "OK"
        assert row["source_count"] == 2
        assert row["sources"] == {"eBay": 1, "Vinted": 1}


if __name__ == "__main__":
    test_audit_diagnoses_zero_and_diverse_results()
    print("OK - benchmark prioritaire diagnostique volume, vitesse et diversité")
