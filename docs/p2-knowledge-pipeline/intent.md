# Intent: The four approved sources are ingested into Qdrant with chunk-level citation metadata, and hybrid search is measured on a 30-query labelled retrieval set
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-07. Via /aspec:make-it-simple-v2.*

## Problem statement

P1 left the infrastructure running: PostgreSQL, Qdrant and Langfuse locally, and vLLM on RunPod (7/7 health checks on 2026-11-07). But the knowledge base is still only a manifest. Nothing is chunked, embedded or searchable, so:
- the agent has no evidence to retrieve;
- no answer can carry a citation that resolves to a real chunk (the *citations must resolve* invariant);
- none of the retrieval targets in PRD §22 (candidate Recall@20 ≥ 0.90, citation validity ≥ 0.98) can be measured.

The PRD's P2 exit criterion, *retrieval test set and citations work*, is not met.

Two of the four approved sources, the ASPI SNAP PDFs, aren't on disk yet. They are `reference_only`: they may be held locally and quoted in short excerpts, but never committed.

## Proposed outcome

- **One ingestion command.** It reads only `approved` entries from `knowledge_base/manifests/sources.yaml`:
  - the two ASPI SNAP PDFs (Indonesian), from the git-ignored `knowledge_base/sources/`;
  - the two synthetic documents (English), from `knowledge_base/synthetic/`.

  It then:
  1. verifies each file (type, size, permission, checksum);
  2. splits by document structure first, and never splits a code sample, JSON example, endpoint table or signature formula;
  3. attaches the §12.1 metadata to every chunk: document, version, effective date, page or section;
  4. writes dense and sparse (BM25-style) vectors to Qdrant.

  Re-running it on unchanged content changes nothing.
- **Hybrid search.** A query returns the top candidates from dense and keyword retrieval fused with RRF. Each result carries everything the §12.4 citation contract needs, including a short excerpt within the 300-character limit for reference-only sources.
- **A citation check.** It confirms that a cited chunk was returned in that run, that its source is approved, and that its page or section resolves.
- **A labelled retrieval set.** It has 30 queries in Indonesian and English, each with its gold relevant document sections. Claude drafts it and the user reviews the labels. It is reused by P3 for reranking.
- **A measured retrieval report.** It gives Recall@5/10/20 for dense, BM25 and hybrid retrieval on that set, saved under `evals/reports/`. The numbers are measured, not targets.

## Affected users and systems

- Kharisma Wardhana: downloads the two ASPI PDFs, reviews the 30 gold labels, and runs ingestion.
- Qdrant (local): gets its first collection.
- PostgreSQL (local): may get document and chunk metadata tables (TRD §17). This is the first database schema.
- The local machine's CPU (8 cores, 32 GB RAM): runs the embedding model. The GPU pod is not needed for P2.
- `knowledge_base/`: the manifest's `checksum_sha256` values get filled in.
- `configs/`: retrieval and chunking parameters (CLAUDE.md: parameters live in config, not code).
- `evals/`: the retrieval set and report.
- `.github/workflows/ci.yml`: must keep passing without Qdrant, PostgreSQL, a model download or the PDFs.
- Downstream: P3 (reranking, using the same 30 queries), P4 (the `search_qris_spec` tool) and P5 (the agent's `retrieve_evidence` node).

## Constraints

- **Deadline:** P2 is done by 2026-11-09. All of P1–P9 must be done by 2026-11-20.
- **Licence:** ASPI PDFs and their extracted text are never committed: not in fixtures, the test set, reports or logs. Excerpts are at most 300 characters (`configs/security.yaml`).
- **Approval:** only `approved` manifest entries are ingested. Versions, dates and URLs are never guessed.
- **Synthetic labels:** synthetic documents stay labelled as synthetic in every chunk's metadata and in every citation.
- **No GPU cost:** P2 runs on local CPU. GPU budget is $19.57 left for P2–P9.
- **CI:** tests run with no services, no network model download and no ASPI files. Anything that needs them is a local step whose output is pasted as evidence.
- **Untrusted input:** user queries and retrieved text are untrusted. They can't change what is searched beyond the query itself.
- **Data:** synthetic and public reference data only. No secrets in chunks, logs or traces.

## Open questions

- Which embedding model, and how big? BGE-M3 is the TRD's suggestion; it is multilingual, which matters for Indonesian + English. Is it fast enough on 8 CPU cores for this corpus, and how are its weights pinned and cached?
- Should sparse retrieval use BGE-M3's own sparse vectors, or BM25 through Qdrant's built-in BM25? Does Qdrant v1.19.2 support the needed fusion server-side?
- How are the PDFs parsed (which library), and how are page numbers and section headings kept, given these PDFs' layout (tables, code blocks)?
- What chunk size and overlap? §12.2 suggests 400–800 tokens with 60–100 overlap; is that right for these documents?
- Do document and chunk metadata go into PostgreSQL in P2 (TRD §17), or does Qdrant's payload alone serve P2, with tables added when the agent needs them?
- How can CI test chunking and citation logic without the ASPI PDFs: synthetic documents only, or small synthetic PDF fixtures?
- How is "gold relevant section" defined for the 30 queries (a section ID or a page range), so that Recall@k is computed on something stable when the chunking changes?
- If the 1.0.2 PDF covers state a different version or date than the manifest, what happens? (The manifest notes ask for the data spec's cover to be re-checked at ingestion.)
