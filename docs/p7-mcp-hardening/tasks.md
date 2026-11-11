# Tasks: Every TRD §13.4 go-live control has recorded evidence, and the §22.5 MCP security test set shows zero unauthorized side effects and no secret leakage
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p7-mcp-hardening/plan.md*

- [x] 1. Config and `app/security/` (limiter, body limit, egress check) — spec AC: #16
- [x] 2. Roles, audit table and env (`--add-missing`) — spec AC: #11, #13, #14
- [x] 3. Audit writer and MCP wiring, purge — spec AC: #1–#6
- [x] 4. Services on role DSNs; egress at start-up; no redirects — spec AC: #12, #15, #16, #17
- [x] 5. Rate and size limits on both servers; malformed input — spec AC: #7–#10
- [ ] 6. SSRF socket guard and loopback pins — spec AC: #18, #23
- [ ] 7. Injection cases, CI mode — spec AC: #19
- [ ] 8. Security suite, evidence and `eval_security.py` — spec AC: #21, #22
- [ ] 9. Container scan job and pip-audit record — spec AC: #24
- [ ] 10. Live injection run (pod) — spec AC: #20
- [ ] 11. Security report, transport note, threat model and docs — spec AC: #25
- [ ] 12. `CLAUDE.md` merge — spec AC: #26 (agent configuration)
