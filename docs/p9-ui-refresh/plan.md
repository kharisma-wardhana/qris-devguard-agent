# Plan: Restyle the Streamlit demo UI after the developer's HTML prototype, and fill every figure from real data
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-16.*
*Implements: docs/p9-ui-refresh/spec.md*

## Affected files

**New**
- `app/api/reports.py`: the release-gate reader. It globs `release-gate-*.json` in `configs/evaluation.yaml` `reports_dir`, skips anything that isn't a JSON object, picks the newest by `(generated_at, name)`, and returns the allowlisted shape. It has no `app.evaluation` import (spec Behaviour §8).
- `app/ui/theme.py`: the `CSS` constant and HTML helpers that take only ints, `None` or fixed-dict keys: `brand_html`, `env_pill_html`, `stat_card_html`, `pill_html`, `STATUS_PILL`. It has no Streamlit import (spec Behaviour §9).
- `tests/unit/test_api_reports.py`: the reader on temp directories.
- `tests/unit/test_ui_theme.py`: the helpers, plus the AST check of `streamlit_app.py`.
- `docs/p9-ui-refresh/tasks.md`.

**Edited**
- `app/api/app.py`:
  - `GET /api/v1/sources`;
  - `GET /api/v1/evaluations/release-gate`;
  - the `REPORT_NOT_FOUND` entry in `MESSAGES`.
- `app/ui/client.py`: `Client.sources()` and `Client.release_gate()`.
- `app/ui/render.py`: `split_sections`, `overview_counts`, `gate_value`, `gate_threshold`, `source_lines`. `answer_sections` stays unchanged.
- `app/ui/streamlit_app.py`:
  - theme injection, the new sidebar and the nav `format_func`;
  - the new pages: Overview, Knowledge base, Evaluations;
  - the case-detail layout on Investigations, amber approval cards, and stat cards on Monitoring.
- `tests/unit/test_api_p9_routes.py`: route shapes, allowlists, 404/500, and the import regression. Existing tests are untouched.
- `tests/unit/test_ui_render.py` and `tests/unit/test_ui_app.py`: new tests only. `Fake` gains `sources()` and `release_gate()`; that is additive, and existing tests are untouched.
- `tests/integration/test_api_p9_server.py`: auth tests for the two routes, plus one burst test on its own server.
- `tests/integration/test_ui_e2e.py`: renders the three new pages against the real API.
- `docs/p9-demo-ui-alerts/runbook.md` (manual pass), `docs/structure.md`, `docs/tech.md`, `docs/index.md`, and `CLAUDE.md` (the `app/ui/` sentence and one Common mistakes line, if the work turns one up).

## Work sequence

1. **Release-gate reader and route.** Files: `app/api/reports.py`, `app/api/app.py`, `tests/unit/test_api_reports.py`, `tests/unit/test_api_p9_routes.py`. Spec AC: #2, #3, #5.
2. **Sources route.** Files: `app/api/app.py`, `tests/unit/test_api_p9_routes.py`. Reuse `app.rag.manifest.load_manifest` and `retrievable_document_ids`. Spec AC: #1.
3. **Auth integration for both routes.** File: `tests/integration/test_api_p9_server.py`. The burst test gets a dedicated server (CLAUDE.md common mistake). Spec AC: #4, #6.
4. **Client and render helpers.** Files: `app/ui/client.py`, `app/ui/render.py`, plus their unit tests. Spec AC: #8 (text helpers), #9 (counts).
5. **Theme module.** Files: `app/ui/theme.py`, `tests/unit/test_ui_theme.py`. The CSS ports the reference's `:root` tokens and the sidebar, card, pill, panel, approval and launch styles, with the three breakpoints. Spec AC: #13 (helpers part).
6. **Pages.** Files: `app/ui/streamlit_app.py`, `tests/unit/test_ui_app.py`, `tests/unit/test_ui_theme.py` (AST part).
   - Theme and sidebar first, then Overview, Investigations, Approvals, Monitoring, Knowledge base and Evaluations.
   - Run the existing AppTests after each page; they must stay green unchanged.
   - Spec AC: #7–#13.
7. **End to end.** File: `tests/integration/test_ui_e2e.py`. Spec AC: #14.
8. **Manual pass and docs.** Files: the runbook, `docs/structure.md`, `docs/tech.md`, `docs/index.md`, `CLAUDE.md`. The developer runs the browser pass (agent-assisted start-up), and the result is recorded in the runbook. Spec AC: #15, #16.

Steps 1–3 and 4–5 don't depend on each other, but steps 6 and 7 need all of them. The work is small, so the steps run in order on one branch.

## Test coverage

Rows matched from the CLAUDE.md test scope: changes to existing behaviour → regression; roles and permissions on the API → auth integration against the real server subprocess; UI → AppTest end to end against the real API and MCP subprocesses, plus one manual browser pass. There is no database, Qdrant or schema change, so no `-m db` test is added. Every step keeps the full existing suite passing unchanged.

1. **Reader and route:**
   - Unit, on temp report directories:
     - no files → 404 `REPORT_NOT_FOUND`;
     - only non-JSON or non-object files → 404;
     - two reports → the newer `generated_at` wins, ties broken by name;
     - a report without `gates` → 500 with no file text in the body;
     - the response carries none of `path`, `sha256`, `freshness_days`, `reported_not_gated`;
     - `evidence_date` is the newest date.
   - Regression: importing `app.api.app` in a fresh subprocess leaves no `app.evaluation` module in `sys.modules`.
2. **Sources route:**
   - The set equals `retrievable_document_ids(load_manifest(...))`, in manifest order.
   - Each item has exactly the seven fields.
   - `synthetic` is true exactly for `team_authored` entries.
   - A manifest read failure → 500 with no path in the body.
3. **Auth integration** (real server subprocess):
   - both routes: no token → 401, MCP-audience token → 401;
   - `developer`, `support_engineer`, `kb_maintainer`, `admin` → 200;
   - a dedicated server: an `api_other` burst → 429.
4. **Client and render:**
   - `httpx.MockTransport`: the paths, the list or object returned, and that the envelope maps to `ApiError`.
   - `split_sections`: every section appears exactly once, in order.
   - `overview_counts`: counts for given threads, approvals and checks; `None` for a failed or forbidden source.
   - `gate_value`/`gate_threshold`: float, int, bool and dict values; a hostile string stays literal.
5. **Theme:**
   - The helpers raise `TypeError` on a `str` value.
   - An unknown key gives "Unknown".
   - The output contains no input text other than `str(int)`.
   - The CSS defines the reference tokens.
6. **Pages (AppTest, fake client):**
   - 6 pages × 2 identities render, and the token appears nowhere.
   - A hostile source title, gate name, gate note, thread status and alert title appear only in `text` elements, and an unknown status shows the "Unknown" pill.
   - Overview counts, with "—" for the developer's pending approvals and for a failed read.
   - An Overview chip creates a thread and lands on `Threads and chat` with the message filled in.
   - Evaluations: a FAIL verdict and one row per gate; a 404 shows the empty state with no error.
   - Knowledge base: the row count and the `SYNTHETIC` pill.
   - AST: `st.html` and `unsafe_allow_html` take only `theme.*`.
7. **End to end:** Overview, Knowledge base and Evaluations against the real API and MCP subprocesses, with the real manifest and committed reports. No 429 and no error across the walk.
8. **Manual and docs:**
   - The repo hygiene tests on the staged docs.
   - The browser pass is recorded with its date, identities, widths and outcome. Screenshots are 1 MB or less if taken.

## Risks
- **Streamlit DOM selectors.** The version is pinned at 1.66.1. If a selector doesn't take, the page falls back to the default look, and the manual pass catches it.
- **`api_other` budget.** Overview reads threads, approvals and sources. All three go through the per-session `read()` cache, and the e2e walk asserts no 429.
- **`format_func` on the nav radio.** AppTest `set_value` takes the option value, not the shown label. Step 6 checks this first.
