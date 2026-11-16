# Intent: Restyle the Streamlit demo UI after the developer's HTML prototype, and fill every figure from real data
*Author: Kharisma Wardhana. Stage: Intent. Written: 2026-11-16.*

## Problem statement

The P9 demo UI (`app/ui/`, [p9-demo-ui-alerts](../p9-demo-ui-alerts/intent.md)) works: chat, threads, approvals and alerts all run through `/api/v1`. It looks like a default Streamlit page, though: a plain sidebar, a radio for navigation and blocks of plain text. That makes it hard to read in a demo and in the final report's screenshots.

The developer supplied a static prototype, [`reference/qris-devguard-ui.html`](reference/qris-devguard-ui.html). It has:
- a navy sidebar with a brand mark, a SANDBOX pill and nav buttons;
- stat cards, status pills and a "start an investigation" panel;
- a case-detail layout, with the diagnosis and evidence on the left and the deterministic tool results and approval card on the right;
- Overview, Investigations, Knowledge base, Evaluations, Monitoring and Settings pages.

**The prototype is a design reference only.** Its figures are invented: Recall 88%, 99.9% availability, 12 open cases, gates shown as PASS. So are its cases (DG-2408…), partner banks and merchant IDs. The repo rules forbid showing targets or made-up numbers as results.

## Proposed outcome

The developer decided on 2026-11-16 to restyle the UI and add pages backed by real data:

- **Adopt the reference look** in the existing Streamlit app: colours, sidebar, cards, pills, case-detail layout and responsive breakpoints.
- **Rename for the reference's navigation:** Threads and chat shows as *Investigations*, and Alerts shows as *Monitoring*. Approvals stays.
- **Add an Overview page.** Its stat cards are counted from the identity's real threads and pending approvals, and from `/health/ready`. It also has the example prompts as a launch panel and a recent-threads table.
- **Add a Knowledge base page** listing the approved, retrievable sources from `knowledge_base/manifests/sources.yaml`, read through a new read-only API route.
- **Add an Evaluations page** showing the newest release-gate report as measured, through a new read-only API route. Today that verdict is FAIL (9 of 15), and the page shows it as FAIL.
- When a figure has no source, show "—", never a placeholder number.

## Affected users and systems

- **Kharisma Wardhana:** runs the demo and captures the final-report screenshots (P9 final step 17 has screenshots pending).
- **Capstone reviewers:** watch the demo.
- **`app/ui/`:** a new theme module, a restyled `streamlit_app.py`, render helpers, and two new client methods.
- **`app/api/app.py`:** two read-only routes, `GET /api/v1/sources` and `GET /api/v1/evaluations/release-gate`.
- **Tests:** UI AppTests, API unit tests, the real-server integration test and the UI e2e test.
- **Docs:** this folder, `docs/index.md`, `docs/structure.md`, `docs/tech.md` and the P9 demo runbook.

## Constraints

- **The safety model stays.** Text from the API or the manifest is shown with `st.text`/`st.code` only, never as markdown, HTML or a widget label (P9 demo spec AC #15, threat T3). Custom HTML/CSS comes only from fixed constants and from helpers that take integers or fixed enum keys.
- **The token stays in `Client`.** The UI calls `/api/v1` only, and every request goes through the API's auth, rate limits and redaction.
- **No invented numbers.** Every figure is measured or counted from real data (CLAUDE.md: report measured results, never targets).
- **The new routes are read-only and allowlisted.** They take no parameters and don't expose local paths, checksums, permission evidence or evidence SHAs. The API never imports `app/evaluation`, which is eval-time only.
- **Must not break anything.** Existing UI keys, page values, subheaders and all existing tests stay unchanged and passing.
- **Out of scope:**
  - the prototype's Settings page and file upload, because nothing backs them and raw-file persistence is not allowed;
  - a separate HTML/JS frontend, because it would put tokens in the browser;
  - the prototype's invented metrics, cases and partner banks.

## Decisions (developer, 2026-11-16)

- **Intent approved.**
- **Branch:** a new `feature/p9-ui-refresh` branch, cut from `feature/p9-final-evaluation-packaging`.
- **Evaluations page access:** every authenticated role, because the report holds no secrets. Alerts stays approver-only.
