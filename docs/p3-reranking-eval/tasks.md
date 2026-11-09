# Tasks: A cross-encoder reranker reorders the hybrid top-20, and its before/after effect is measured on a harder, reviewed query set
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*
*Implements: docs/p3-reranking-eval/plan.md*

- [x] 1. Draft hard queries for review; start reranker download — spec AC: #5
- [x] 2. Reranker module and config — spec AC: #1, #2, #3
- [x] 3. Ranking metrics and golden-set schema — spec AC: #4, #5
- [x] 4. Model fetch for both models — spec AC: #8
- [x] 5. search.py --rerank — spec AC: #7
- [x] 6. Before/after eval (subset fixture is a new file, see plan) — spec AC: #6
- [x] 7. Merge the reviewed hard queries — spec AC: #5
- [x] 8. Threat model and steering docs — spec flagged concerns
- [x] 9. Real measurements and report — spec AC: #9, #10
- [ ] 10. CLAUDE.md merge — agent configuration
