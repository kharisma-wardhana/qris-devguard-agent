# Tasks: Qwen3-8B is served by vLLM on one RunPod GPU, and the local PostgreSQL, Qdrant and Langfuse stack can reach it with health checks passing
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Implements: docs/p1-infrastructure/plan.md*

- [x] 1. Model config and pod bootstrap script — spec AC: #9, #10
- [x] 2. Health-check CLI — spec AC: #6, #7, #8
- [x] 3. Local stack definition and env — spec AC: #2, #3, #5 (its original #4 check was superseded by step 6)
- [x] 4. Threat model — spec AC: #13
- [x] 5. Tunnel helper and runbook — spec AC: #12
- [x] 6. Application-level memory caps — spec AC: #4
- [x] 7. Bring-up fixes — spec AC: #5, #6
- [x] 8. Local stack bring-up — spec AC: #1
- [x] 9. Live GPU run (user) — spec AC: #11
- [x] 10. CLAUDE.md merge — agent configuration
- [x] 11. Bootstrap fixes from the live run — spec AC: #10, #11
- [x] 12. GPU label on health-check runs — spec AC: #6
