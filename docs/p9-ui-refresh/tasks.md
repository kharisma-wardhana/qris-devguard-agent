# Tasks: Restyle the Streamlit demo UI after the developer's HTML prototype, and fill every figure from real data
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-16.*
*Implements: docs/p9-ui-refresh/plan.md*

- [x] 1. Release-gate reader and route — spec AC: #2, #3, #5 (amended: API paths in configs/agent.yaml `api`; reports are git-ignored, so the measured-FAIL check is local-only)
- [x] 2. Sources route — spec AC: #1
- [x] 3. Auth integration for both routes — spec AC: #4, #6 (own server module `test_api_ui_refresh_server.py`, so the shared per-address burst is not exhausted; test-only `DEVGUARD_REPORTS_DIR` override, as `DEVGUARD_ALERT_STORE`)
- [x] 4. Client and render helpers — spec AC: #8, #9
- [x] 5. Theme module — spec AC: #13 (helpers; the AST check of streamlit_app.py lands with step 6)
- [x] 6. Pages (theme, sidebar, Overview, Investigations, Approvals, Monitoring, Knowledge base, Evaluations) — spec AC: #7–#13 (amended: answer stays whole in §5 order with a compact tool panel beside it; split_sections removed; light theme forced in the launcher)
- [x] 7. End to end against the real API — spec AC: #14 (the `-m db` Overview case is written but skipped locally: the scripts cannot reach PostgreSQL with the current .env, see step 8)
- [ ] 8. Manual browser pass and docs — spec AC: #15, #16
