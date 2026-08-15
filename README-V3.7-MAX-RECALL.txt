LUXE RADAR V3.7 — MAX RECALL / CONTINUOUS SCROLL PATCH

Goal
- Keep instant indexed results.
- When the user reaches the end, keep collecting REAL offers only.
- Rotate through all existing sources that expose public pagination.
- For sources without a reliable page cursor, widen the real first pass up to 100 candidates.
- Never bypass 403 / 429 / CAPTCHA / challenge pages.
- Scan the existing public catalog in deeper waves to discover additional non-blocked shops.

Expanded page sources
- eBay, Zalando, Vinted
- i-Run, Direct Running, Alltricks, Deporvillage
- Running Point, Hardloop, Ekosport, Courir, 21RUN, MisterRunning
- Spartoo, Footshop, JD Sports

Recall-widening sources (bounded at 100 real candidates)
- SSENSE, ASOS, AliExpress, DHgate, Cdiscount, 67behaviour, 1688, Grailed

Important
- Grailed may still return zero additional offers if Grailed presents a browser challenge. This patch intentionally does not bypass it.
- Retail sources that return 403/429 remain fail-fast and are exhausted after empty waves.
- Existing strict identity/quality filtering remains in place, so River Island should not enter a Stone Island result set.
- No Stripe changes.
- No .env file is included.

Internal validation performed before packaging
- test_v371_max_recall.py: PASS
- test_v370_instant_search_os.py: PASS
- test_v360_hybrid_release.py: PASS
- python compile: PASS
- node --check static/app.js: PASS
