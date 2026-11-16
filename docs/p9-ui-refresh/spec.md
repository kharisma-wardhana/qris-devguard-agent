# Spec: Restyle the Streamlit demo UI after the developer's HTML prototype, and fill every figure from real data
*Author: Kharisma Wardhana. Stage: Spec. Written: 2026-11-16. Intent: [intent.md](intent.md). Reference: [reference/qris-devguard-ui.html](reference/qris-devguard-ui.html) (design only; its figures are invented).*

## Behaviour

### 1. Look and navigation
- **Theme.** One fixed stylesheet, taken from the reference's `:root` tokens, is injected on every run. It restyles:
  - the sidebar: navy, brand mark, nav, health list;
  - stat cards, bordered panels and status pills;
  - the approval card (amber) and the launch panel;
  - layouts for widths of 1100 px and above, 780 px and below, and 540 px and below.
- **Pages, in nav order.** The nav is the existing `page` radio. Its option values are unchanged (the tests depend on them); a display function shows the labels below.

  | Option value | Shown as |
  |---|---|
  | `Overview` (new, default) | Overview |
  | `Threads and chat` | Investigations |
  | `Approvals` | Approvals |
  | `Alerts` | Monitoring |
  | `Knowledge base` (new) | Knowledge base |
  | `Evaluations` (new) | Evaluations |

- **Sidebar, top to bottom:**
  1. the brand block ("DevGuard AI", "QRIS integration suite");
  2. a SANDBOX pill;
  3. the demo identity select (unchanged);
  4. the nav;
  5. the API health list. Each check from `/health/ready` (`mcp`, `llm`) is shown with an Online/Offline pill, plus the "Check API status" button (unchanged);
  6. the caption "Synthetic data only. Local demo; not a product UI."
- **Prototype parts that are left out:** the workspace switcher, Settings, file upload, notifications, "Export report", the help card and the profile avatar. Nothing backs them.

### 2. Overview (new)
- **Four stat cards.** Each value is an integer counted from real data, or "—" when its source failed or is not allowed for the identity.

  | Card | Value | Foot text |
  |---|---|---|
  | My threads | number of threads in `GET /api/v1/threads` | "Threads you created (newest 50)" |
  | Awaiting approval | those threads with status `awaiting_approval` | "Paused until an approver decides" |
  | Pending approvals | `len(GET /api/v1/approvals?status=pending)` for approver roles; "—" for others | "Approver roles only" for others |
  | API readiness | number of `/health/ready` checks that are `up` (the foot gives the total) | "of N checks up" |

- **Demo banner (fixed text):** "Demo workspace — every record here is synthetic. No production system is connected."
- **Launch panel: "Start a new investigation".** One chip button per non-empty `EXAMPLES` entry. A click does three things:
  1. creates a thread (a write, which clears the read cache);
  2. fills the message and context with that example;
  3. switches the page to Investigations with the new thread open.

  If thread creation fails, the API error envelope is shown and the page stays on Overview.
- **Recent investigations.** Up to 5 of the caller's threads. Each row shows the short thread ID (text), a status pill, `updated_at` (text) and an "Open" button. The button opens the thread on Investigations; its key is `ov-open-{thread_id}`. With no threads, the panel shows "No threads yet."
- **Pinned references.** The first 3 entries from `GET /api/v1/sources`, each showing title, version and effective date (text), plus a "Library" button that switches to Knowledge base.

### 3. Investigations (restyled Threads and chat)
- The thread list, the "New thread" and "Refresh" buttons, the open-thread view, the run expanders ("Run N"), the chat form and the example select keep their keys, labels and behaviour.
- **The latest answer uses the reference's case-detail layout:**
  - Left panel, the diagnosis: Summary, Findings, Next steps, Sources, Confidence and limitations.
  - Right column: Validation results in a "Deterministic tool results" panel, then Action or Draft. Below them, when the thread is waiting, the proposed-approval card in amber.
  - Every section keeps its `st.subheader` heading and `st.text` body exactly as today. `render.answer_sections` is unchanged; a new `render.split_sections` partitions its output.
- The thread status is shown as a pill next to the thread ID.

### 4. Approvals (restyled)
- Each pending approval sits in an amber card headed "Human approval required" (fixed text). The body is `render.approval_lines` as text, unchanged.
- The decide controls, the manual-ID expander, the keys and the decision outcome are unchanged.

### 5. Monitoring (restyled Alerts)
- The behaviour is unchanged: approver roles only, auto-refresh toggle, Refresh, toast on new firing alerts, and "No alerts received yet".
- Above the feed are two stat cards: "Alerts received" (count) and "Firing" (count with severity `ALERT`).
- Alert cards use panel styling.

### 6. Knowledge base (new)
- **Fixed banner:** "Only approved, retrievable sources are listed. Each answer cites chunks from these documents."
- **One row per entry from `GET /api/v1/sources`:**
  - title, `document_id` and publisher (text);
  - kind, version and effective date (text);
  - a pill: `SYNTHETIC` when `synthetic` is true, otherwise `Reference only`.
- **Panel header:** "N sources" (count).
- **An API error** shows the envelope, as on other pages.

### 7. Evaluations (new)
- **Header** (from `GET /api/v1/evaluations/release-gate`):
  - the verdict pill (`PASS` or `FAIL`; anything else is shown as `UNKNOWN`);
  - `generated_at` (text);
  - three stat cards: "Gates passing" (passing count, foot "of N gates"), "Not passing" (count) and "Critical not passing" (count).
- **One row per gate:**
  - name (text);
  - kind (`critical`/`gate`, as a pill);
  - status pill (`PASS`, `FAIL`, `NOT RUN`, `STALE`; anything else `UNKNOWN`);
  - measured value and threshold (text, formatted by `render.gate_value` / `render.gate_threshold`);
  - newest evidence date (text).
- **Fixed caption:** "Measured results from the newest release-gate report (scripts/release_gate.py). Thresholds come from configs/eval_thresholds.yaml. Nothing on this page is a target."
- **Empty state:** when the API answers 404 `REPORT_NOT_FOUND`, the page shows "No release-gate report yet. Run scripts/release_gate.py." instead of an error.
- **Access:** every authenticated role sees this page (developer decision, 2026-11-16).

### 8. New API routes (read-only)
Both routes:
- use the existing `other_identity` dependency (API-audience bearer token, `api_other` rate bucket), so any authenticated role may call them;
- take no path or query parameters;
- run their file reads in the threadpool and answer in the §18 envelope on error.

**`GET /api/v1/sources`** → `{"sources": [...]}`
- It loads `knowledge_base/manifests/sources.yaml` with `app.rag.manifest.load_manifest` and keeps only entries in `retrievable_document_ids`, in manifest order.
- Each item holds exactly these fields:

  | Field | Value |
  |---|---|
  | `document_id`, `title`, `publisher`, `kind`, `document_version` | from the manifest |
  | `effective_date` | ISO `YYYY-MM` or `YYYY-MM-DD` string, or `null` |
  | `synthetic` | `permission.basis == "team_authored"` |

- If the manifest can't be read, the route answers 500 `INTERNAL_ERROR`, with no file content and no path in the body.

**`GET /api/v1/evaluations/release-gate`** → the newest release-gate report, as an allowlist
- **Choosing the report:** it reads every `release-gate-*.json` in `configs/evaluation.yaml` `reports_dir` and ignores files that aren't a JSON object. The newest is the one with the largest `(generated_at, file name)`, the same rule the gate uses.
- **Response fields:**
  - `generated_at`, `verdict`, `not_passing` (list of names), `critical_not_passing` (list of names);
  - `gates`: a list with, per gate, `name`, `kind`, `status`, `value` (number, bool, or an object of numbers as in the report), `threshold` (object as in the report), `evidence_date` (the newest evidence `date`, or `null`) and `note` (string).
- **What it never returns:** evidence paths, SHA-256 values, `freshness_days` or `reported_not_gated`.
- **Errors:**
  - No usable report: 404 with the new envelope code `REPORT_NOT_FOUND`, message "no release-gate report has been generated yet".
  - A report missing a required field: 500 `INTERNAL_ERROR`.
- `app/api` does not import `app/evaluation` (eval-time only); the route's reader lives in a small new module, `app/api/reports.py`.

**Client.** `app/ui/client.py` gets `Client.sources()` (returns the list) and `Client.release_gate()` (returns the object).

### 9. Rendering rules (all pages)
- **Plain text only for outside data.** Every string that comes from the API or the manifest goes through `st.text`/`st.code`/`st.dataframe`. That covers titles, IDs, dates, gate names, notes, answers and alerts. Such strings never go into `st.markdown`, `st.html`, a caption, a toast or a widget label. This is unchanged from P9 demo AC #15.
- **Where custom HTML comes from.** It comes only from `app/ui/theme.py`. It is either a constant or a helper whose variable inputs are `int`/`None` or a key looked up in a fixed dict. An unknown key renders the fixed word "Unknown". The `st.html` calls in `streamlit_app.py` pass only `theme.*` results.
- **No invented figures.** A number is either counted or measured from an API response, or shown as "—".

## Architecture and the reason for it
- **Restyle Streamlit instead of porting the prototype to a frontend framework.** A browser frontend would have to hold the bearer token or need a new backend-for-frontend. The current design keeps the token in the server-side `Client`, and the AppTest suite already checks the security properties. CSS plus a few fixed HTML snippets gets the reference look without changing that trust boundary.
- **Keep the nav option values and use `format_func`.** The existing unit and e2e tests select pages by value. Renaming only the displayed labels keeps them unchanged, as the CLAUDE.md test rules require.
- **Read the manifest and the gate report through the API, not from disk in the UI.** The UI's rule is "calls `/api/v1` only" (P9 demo spec). Going through the API keeps auth and rate limits in the path, and the API's allowlists decide what leaves the files.
- **A separate reader instead of importing `app/evaluation`.** `app/evaluation` is eval-time only and pulls in judge and bench code. The API needs about 30 lines: glob, parse, pick the newest, allowlist.
- **Style through Streamlit 1.66 keyed containers.** Containers with a `key` get an `st-key-<key>` class, which lets fixed CSS style the approval card, the panels and the launch panel without wrapping API text in HTML.

## Data
- No schema, migration or Qdrant change.
- The sources route reads `knowledge_base/manifests/sources.yaml`, which is committed.
- The release-gate route reads `evals/reports/release-gate-*.json`. It writes nothing.
- The UI keeps reads in its existing per-session cache (`ui.read_cache_seconds`). The pinned references, the Knowledge base page and the Evaluations page share cache entries, so changing pages doesn't add `api_other` calls inside the cache window.

## Acceptance criteria

**API**
1. `GET /api/v1/sources` returns, for `developer`, `support_engineer`, `kb_maintainer` and `admin` tokens, exactly the retrievable documents (the same set as `manifest.retrievable_document_ids()`) in manifest order, each with exactly the seven fields in Behaviour §8. No item contains `local_path`, `checksum_sha256`, `permission`, `notes`, `source_url` or `approval_status`.
2. `GET /api/v1/evaluations/release-gate` returns the newest report by `(generated_at, file name)`, with only the fields in Behaviour §8. No path, `sha256`, `freshness_days` or `reported_not_gated` appears anywhere in the body. With the committed reports it returns verdict `FAIL` and 15 gates.
3. With no release-gate report, or none that parses, the route returns 404 `REPORT_NOT_FOUND` in the §18 envelope. A report missing `gates` returns 500 `INTERNAL_ERROR` with no file content in the body.
4. Both routes return 401 without a token and 401 with an MCP-audience token, checked against the real API server subprocess. A burst over the `api_other` bucket gets 429.
5. Importing `app.api.app` does not import any `app.evaluation` module (regression test on `sys.modules`).
6. All existing tests pass unchanged, and no existing route changes its response.

**UI**
7. Every page (6 option values) renders for both demo identities without an exception, and the token never appears in the rendered page (extends P9 demo AC #14).
8. A hostile string (markdown link + HTML) used as a source title, a gate name, a gate note, a thread status and an alert title appears only in `text` elements. It never appears in markdown, html, caption, label or toast elements, and an unknown status renders the pill "Unknown".
9. Overview shows the counts derived from the fake client's threads, approvals and ready checks. For the developer identity, "Pending approvals" shows "—". When a read fails, its card shows "—" and the envelope is shown.
10. Clicking an Overview example chip creates one thread, switches `page` to `Threads and chat`, opens that thread and fills the message box with the example message.
11. Evaluations shows a `FAIL` verdict pill and one row per gate for a FAIL report. On a 404 `REPORT_NOT_FOUND` it shows the empty-state text and no error.
12. Knowledge base shows one row per source and the `SYNTHETIC` pill for synthetic entries.
13. `theme.py`'s HTML helpers accept only `int`/`None` values and fixed-dict keys: a string value raises `TypeError`, and an unknown key gives "Unknown". An AST test of `streamlit_app.py` shows that every `st.html` call and every `unsafe_allow_html=True` argument takes only `theme.*`.
14. End to end against the real API and MCP subprocesses, Overview, Knowledge base and Evaluations render with the real manifest and the committed release-gate report.

**Manual and docs**
15. One manual browser pass covers all 6 pages as both identities. It runs one example end to end, then approves one ticket and rejects one, and checks the layout at about 1400, 900 and 400 px wide. The pass is recorded in `docs/p9-demo-ui-alerts/runbook.md`, and any screenshots taken are 1 MB or less.
16. `docs/structure.md`, `docs/tech.md`, `docs/index.md` and `CLAUDE.md` (the `app/ui/` line) are updated in the same change as the code.

## Flagged concerns
- **Hand-written CSS against Streamlit's internal DOM** (`data-testid` selectors, `st-key-*` classes) can break when Streamlit is upgraded. Streamlit is pinned (`streamlit==1.66.1`), and the manual pass (AC #15) is the check. A broken style degrades to default Streamlit and never to a security issue, because no data goes through HTML.
- **The Evaluations page shows a FAIL verdict in the demo.** That is intended: it is the measured result (release report, 2026-11-15).
- **The new routes add `api_other` calls.** The per-session read cache covers them. The e2e test asserts no 429 across a full page walk.

## Out of scope
- the prototype's Settings page, file upload, workspace switcher, notifications, export buttons and its invented metrics, cases, banks and merchant IDs;
- a JS/HTML frontend or a backend-for-frontend;
- ingest statistics such as chunk counts per source, which no API exposes;
- `reported_not_gated` (latency and bench) on the Evaluations page; the final report already covers it;
- changes to the alert receiver, the agent graph, the database or the release gate itself.

## Resolved questions
- Scope: restyle plus real-data pages (developer, 2026-11-16).
- Branch: `feature/p9-ui-refresh` (developer, 2026-11-16).
- Evaluations access: every authenticated role (developer, 2026-11-16).
