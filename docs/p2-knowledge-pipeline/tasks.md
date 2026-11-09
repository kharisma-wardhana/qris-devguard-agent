# Tasks: The four approved sources are ingested into Qdrant with chunk-level citation metadata, and hybrid search is measured on a 30-query labelled retrieval set
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-07. Via /aspec:make-it-simple-v2.*
*Implements: docs/p2-knowledge-pipeline/plan.md*

- [x] 1. Dependencies and config — spec AC: #7, #15
- [x] 2. Manifest selection and file checks — spec AC: #1
- [x] 3. Extraction and chunking — spec AC: #2, #4
- [x] 4. Sparse BM25 encoder — spec AC: #5
- [x] 5. Embedders and model fetch — spec AC: #15
- [x] 6. Store and ingestion pipeline — spec AC: #1, #3, #4, #10
- [x] 7. Hybrid search and citation validator — spec AC: #6, #7, #8, #9, #10
- [x] 8. Retrieval metrics and eval CLI — spec AC: #11, #13
- [x] 9. Threat model and steering docs — spec flagged concerns
- [x] 10. Real corpus (you, then me) — spec AC: #12, #14
- [x] 11. The 30-query retrieval set — spec AC: #11
- [ ] 12. Measured retrieval report — spec AC: #13
- [ ] 13. CLAUDE.md merge — agent configuration
