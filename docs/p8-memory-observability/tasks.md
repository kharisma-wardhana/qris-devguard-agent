# Tasks: The agent saves only policy-approved memory with tested save/update/delete, every request is a redacted Langfuse trace with §21.2 scores, and an alert is shown firing
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p8-memory-observability/plan.md*

- [ ] 1. Dependency, configs and migration `0003` with grants — spec AC: #27
- [ ] 2. Memory policy and store — spec AC: #1, #4, #5, #6, #7, #8, #10
- [ ] 3. Memory routes and feedback route — spec AC: #1, #2, #4, #6, #7, #8, #10, #19
- [ ] 4. Memory in the graph (`compose-v2`) — spec AC: #3, #9, #11
- [ ] 5. Tracing core (allowlist, mask hook, no-op, egress) — spec AC: #14, #15, #16, #17
- [ ] 6. Agent and MCP spans — spec AC: #12, #13, #14, #16
- [ ] 7. Scores — spec AC: #18, #19, #20
- [ ] 8. Fencing and the marker scan — spec AC: #21, #22, #23, #24
- [ ] 9. Retention command — spec AC: #5, #26, #27
- [ ] 10. Alerts in the local Langfuse (controlled test) — spec AC: #25
- [ ] 11. Live pod session (echo before/after, live traces) — spec AC: #23
- [ ] 12. Report and docs — spec AC: #25, #28
- [ ] 13. `CLAUDE.md` merge — spec AC: #28 (agent configuration)
