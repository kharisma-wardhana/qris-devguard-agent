# Tasks: A local Streamlit demo UI drives chat, threads and approvals through /api/v1, and shows Langfuse alerts received by a loopback webhook
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-demo-ui-alerts/plan.md*

- [x] 1. Dependency and config — spec AC: #19, #12
- [x] 2. Thread list and pending approvals — spec AC: #1, #3, #5
- [x] 3. Thread history — spec AC: #2, #5
- [x] 4. Alert receiver — spec AC: #6, #7, #8, #9, #10, #11, #12
- [x] 5. Alert reader and route — spec AC: #4, #5
- [x] 6. Compose service and Langfuse whitelist — spec AC: #20
- [x] 7. UI client and rendering — spec AC: #14, #15, #17
- [x] 8. Streamlit pages and launcher — spec AC: #13, #15, #16, #18, #19
- [x] 9. CI job for UI tests — spec AC: #13–#19
- [ ] 10. Live local evidence — spec AC: #20, #21 (AC #20 met; AC #21 deferred, developer decision 2026-11-12: Langfuse accepted the http webhook URL, no real alert fired yet; see demo-report.md)
- [ ] 11. Docs and agent configuration — spec AC: #22
