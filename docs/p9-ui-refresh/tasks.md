# Tasks: Restyle the Streamlit demo UI after the developer's HTML prototype, and fill every figure from real data
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-16.*
*Implements: docs/p9-ui-refresh/plan.md*

- [x] 1. Release-gate reader and route — spec AC: #2, #3, #5 (amended: API paths in configs/agent.yaml `api`; reports are git-ignored, so the measured-FAIL check is local-only)
- [x] 2. Sources route — spec AC: #1
- [x] 3. Auth integration for both routes — spec AC: #4, #6 (own server module `test_api_ui_refresh_server.py`, so the shared per-address burst is not exhausted; test-only `DEVGUARD_REPORTS_DIR` override, as `DEVGUARD_ALERT_STORE`)
- [x] 4. Client and render helpers — spec AC: #8, #9
- [ ] 5. Theme module — spec AC: #13
- [ ] 6. Pages (theme, sidebar, Overview, Investigations, Approvals, Monitoring, Knowledge base, Evaluations) — spec AC: #7–#13
- [ ] 7. End to end against the real API — spec AC: #14
- [ ] 8. Manual browser pass and docs — spec AC: #15, #16
