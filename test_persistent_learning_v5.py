from pathlib import Path
import sqlite3
import tempfile
import time

import collector
import index_engine
import learn


def offer(i, title="Nike Trail pantalon running"):
    return {
        "marketplace": "eBay" if i % 2 else "Vinted", "titre": f"{title} {i}",
        "prix": 40 + i % 30, "prix_total": 40 + i % 30, "devise": "EUR",
        "lien": f"https://example.test/{i}", "image": "", "categorie": "Pantalon",
        "niveau_identite": "fort", "score_identite": 90, "score": 80,
        "score_confiance": 80, "risque_contrefacon": "faible",
    }


class ResumeConnector:
    name = "ResumeShop"
    supports_pagination = True
    max_pages = 3
    empty_pages_threshold = 2
    cooldown_seconds = 0
    expansion_page_size = 10

    def __init__(self, fail_page2=False):
        self.fail_page2 = fail_page2
        self.calls = []

    def search_page(self, query, price_max=None, limit=10, page=1):
        self.calls.append(page)
        if page == 2 and self.fail_page2:
            raise RuntimeError("temporary")
        return [offer(page * 100 + i) for i in range(3)]


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "persistent.sqlite3"
    rows = [offer(i) for i in range(120)]
    assert index_engine.upsert_results(rows, "Nike Trail", path=db) == 120

    # A/B/C: first and repeated reads are immediate and cross-query is gated.
    first = index_engine.search("Nike Trail", path=db, limit=20)
    second = index_engine.search("Nike Trail", path=db, limit=20)
    refined = index_engine.search("pantalon Nike Trail", path=db, limit=20)
    assert first.total >= 100 and second.total == first.total and refined.total > 0

    # D/E: stable-key dedupe and confirmed DEAD offers are hidden.
    index_engine.upsert_results([offer(0)], "Nike Trail", path=db)
    assert index_engine.search("Nike Trail", path=db).total == first.total
    dead_key = index_engine._offer_key(offer(0))
    assert index_engine.mark_offers_dead([dead_key], path=db) == 1
    assert all(item["lien"] != offer(0)["lien"] for item in index_engine.search("Nike Trail", path=db).results)

    # Collector restart resumes the exact failed page instead of page 1.
    bad = ResumeConnector(fail_page2=True)
    collector._walk_source(bad, "Nike Trail", None, path=db, log=lambda *_: None)
    good = ResumeConnector()
    collector._walk_source(good, "Nike Trail", None, path=db, log=lambda *_: None)
    assert good.calls[0] == 2

    # F/G: two sessions never learn; 100 independent positive interactions do.
    learn.LEARN_ENABLED = True
    learn._learn_schema_ready = False
    learn._learn_db_path = None
    learn._learn_buffer.clear()
    learn.init_learn_db(db)
    for i in range(2):
        learn.learn_push(f"few-{i}", f"s{i}", "result_click", "nike trail", marketplace="eBay")
    learn._learn_flush_batch(); learn._learn_aggregate()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COALESCE(MAX(bonus),0) FROM learn_signals").fetchone()[0] == 0
    for i in range(100):
        learn.learn_push(f"many-{i}", f"session-{i}", "result_click", "nike trail", marketplace="eBay")
        if len(learn._learn_buffer) >= learn.LEARN_BATCH_SIZE:
            learn._learn_flush_batch()
    learn._learn_flush_batch(); learn._learn_aggregate()
    assert 0 < conn.execute("SELECT MAX(bonus) FROM learn_signals").fetchone()[0] <= 2
    conn.close()

    # H: persistence survives fresh connections/process-style schema reuse.
    assert index_engine.search("Nike Trail", path=db, limit=5).total > 0
    assert index_engine.stats(path=db)["schema_version"] == 3

print("OK - persistent index/lifecycle/resume/learning A-H")
