# AGENTS.md — LUXE RADAR

## Mission
You are working on **LUXE RADAR**, a local Windows/Flask multi-marketplace fashion deal radar and comparison tool.

Primary project path on the user's PC:
`C:\Users\aless\vinted-radar ancien`

Main goals:
- search many fashion/sneaker/luxury marketplaces;
- return many relevant listings, not only a tiny top 10;
- rank best matches first but keep scrolling to weaker matches;
- allow sorting/filtering after search;
- support exact product references;
- add product comparison;
- later add image-based product search;
- keep existing working connectors stable.

## Mandatory workflow before edits
1. Read `docs/PROJECT_CONTEXT.md`.
2. Read `docs/ROADMAP.md`.
3. Read `docs/TESTING.md`.
4. Inspect the current files before modifying them. Do not assume docs are newer than code.
5. Create a backup before any meaningful change:
   `.\.venv\Scripts\python.exe .\luxe_radar_manager.py backup`
6. Make the smallest coherent change.
7. Compile/test affected files.
8. Run project smoke tests.
9. If a change breaks the project, fix it or restore the backup.
10. Summarize exactly what changed and what was actually tested.

## Safety / data rules
- Never print, expose, commit, copy, or overwrite secrets from `.env`.
- eBay Production OAuth credentials are private.
- Do not bypass CAPTCHAs, login walls, anti-bot protections, access controls, or 403 protections.
- Public pages and official APIs are acceptable.
- If a marketplace cannot be cleanly supported, leave it disabled.
- Do not invent/fake listings or claim a connector works without a real test.
- Do not delete existing working connectors to simplify a task.

## Windows / PowerShell
The user works in PowerShell on Windows.
The project path contains spaces, so quote absolute paths.

If prompt already shows:
`PS C:\Users\aless\vinted-radar ancien>`
do NOT tell the user to type the path itself.

From elsewhere:
`cd "C:\Users\aless\vinted-radar ancien"`

Launch Flask:
`.\.venv\Scripts\python.exe app_web.py`

Compile one file:
`.\.venv\Scripts\python.exe -m py_compile <file>`

Manager:
`.\.venv\Scripts\python.exe .\luxe_radar_manager.py --help`

## Important architectural rules
- Preserve connector architecture under `marketplaces/connectors/`.
- Prefer dedicated connectors for marketplaces with special behavior.
- Do not hard-code every marketplace into `radar_engine.py`.
- `radar_engine.py` should stay the universal analysis/ranking layer.
- `app_web.py` is the Flask web layer.
- `templates/index.html` is the main UI.
- Marketplace discovery should remain registry/connector-driven where possible.
- New features should be modular when practical.

## Current working connectors
Known working/active:
- Vinted
- eBay
- 67behaviour
- Grailed

Known disabled/not cleanly supported:
- 1688
- Vestiaire Collective
- Depop
Some other sites may exist in configuration but must not be assumed working.

## Current known behavior
- eBay direct search can return many listings.
- Grailed requires a visible Chromium fallback because headless mode is challenged.
- Grailed V4 reads listing cards directly instead of opening many product pages one-by-one.
- Global ranking currently applies a marketplace diversification penalty; this can hide many eBay listings in "Toutes".
- This diversification should become much lighter or optional for the future large-results UI.

## Product direction
The user wants a shopping-search experience:
- large result sets;
- infinite/continuous scroll;
- best result first, weaker results lower down;
- post-search sorting;
- exact price / price range filters;
- exact reference field;
- marketplace filters;
- compare 2–4 products;
- image search later.

## User experience
The user is a beginner.
When giving manual instructions:
- give one clear PowerShell command at a time when troubleshooting;
- never include the `PS C:\...>` prompt inside a copyable command;
- if `>>` appears, tell the user to press Ctrl+C once;
- prefer complete replacement files/functions over tiny indentation-sensitive patches.

## Definition of done
A task is not "done" because code was written.
It is done only when:
- affected Python files compile;
- relevant tests pass;
- the requested behavior was exercised when feasible;
- no existing connector was silently broken;
- the final response distinguishes tested facts from assumptions.
