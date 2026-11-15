# Tasks: One release-gate run decides PASS/FAIL on every P0 gate, and a first-time cloner can set up, run, evaluate and tear down the system from the README and one setup script
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-15. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-final-evaluation-packaging/plan.md*

- [x] 1. Config, extra, package skeleton — spec AC: #21
- [x] 2. Golden dataset schema and deterministic checks — spec AC: #7, #8
- [x] 3. Golden run script — spec AC: #8, #13
- [x] 4. Judge — spec AC: #9, #10, #11, #12
- [x] 5. Benchmark parser — spec AC: #14
- [x] 6. Evidence producers — spec AC: #23, #24
- [x] 7. Release gate — spec AC: #16–#21
- [x] 8. Deployment manifest — spec AC: #27
- [x] 9. Setup script — spec AC: #1–#6
- [x] 10. README and runbook — spec AC: #26
- [x] 11. Steering docs and threat model — spec AC: #29
- [x] 12. CLAUDE.md merge — spec AC: #29
- [x] 13. Developer review of golden items — spec AC: #7
- [ ] 14. Pod session — spec AC: #13, #15, #25, #27
  - *2026-11-15:* done except the controlled alert test, which was **deferred by developer decision**. Evidence so far:
    - row 0 local: validators 110/110, security 20/20, crash/resume PASS
    - row 1: health check 7/7 on the NVIDIA RTX PRO 4000 Blackwell
    - row 3: live flows 3/4 (flow 1 `sources_include`)
    - row 4: 100 golden answers
    - row 5: injection 10/10, echo 1/10 against the 5/10 baseline
    - row 6: bench at concurrency 1, p95 TTFT 117.3 ms, p95 TPOT 15.7 ms
  - **Alert test:** 15 delayed runs (5.9–7.6 s) and one `APPROVAL_MISMATCH` incident were sent, and the receiver path was verified with a synthetic alert. But Langfuse's worker logged `MonitorRunner(1/1): published 0 events` throughout, so nothing was delivered. AC #25 and P9 AC #21 stay open, and `langfuse_alert_fired` reads NOT RUN. The step stays unticked.
- [x] 15. Judge run and calibration grades — spec AC: #12, #13
  - *2026-11-15:* subagent backend, 100/100 judged, 0 NOT JUDGED. `golden_answer_supported` 0.27, `citation_support_precision` 0.4356. Calibration is **model-graded** (decision b): agreement 1.0, kappa 1.0, with no independent human check.
- [ ] 16. Deployment manifest completed and release gate run — spec AC: #22, #27
- [ ] 17. Final report, screenshots, addenda — spec AC: #25, #28, #30
