# Tasks: Agent runs survive a crash in PostgreSQL, and the one side effect (a sandbox ticket) happens only after a bound, single-use human approval, exactly once
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p6-checkpoint-hitl/plan.md*

- [x] 1. Dependencies, config, the db test fixture — spec AC: #13
- [x] 2. Schema, migrations, actions, store — spec AC: #1, #8, #11
- [x] 3. The create_support_ticket MCP tool — spec AC: #7, #8
- [x] 4. Evidence ordering — spec AC: #12
- [x] 5. Graph HITL and the PostgreSQL checkpointer — spec AC: #3, #6, #9, #14
- [x] 6. API routes — spec AC: #2, #4, #5
- [x] 7. Crash/resume and the secrets dump — spec AC: #10, #11
- [ ] 8. Live re-measure — spec AC: #12
- [ ] 9. Threat model and steering docs — spec flagged concerns
- [ ] 10. CLAUDE.md merge — agent configuration
