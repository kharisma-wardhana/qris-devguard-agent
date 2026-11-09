# Spec: The four approved sources are ingested into Qdrant with chunk-level citation metadata, and hybrid search is measured on a 30-query labelled retrieval set
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-07. Via /aspec:make-it-simple-v2.*
*Implements: docs/p2-knowledge-pipeline/intent.md*

## Behaviour

1. **One-time setup on the developer machine.**
   - `uv sync --extra embed` installs the embedding stack: CPU-only PyTorch and sentence-transformers.
   - `uv run python scripts/fetch_models.py` downloads BGE-M3 at its pinned revision into the local Hugging Face cache, and prints the revision and file sizes.
   - The user puts the two ASPI PDFs at the `local_path` names in `sources.yaml`.
2. **Ingest.** `uv run python scripts/ingest.py` does the following:
   1. Reads the manifest and selects only `approved` entries that have a `local_path`. Any other entry is reported as `skipped: <reason>`.
   2. Checks each file:
      - it exists;
      - its type is PDF or Markdown, matching its extension;
      - it is at most 50 MB;
      - its permission basis matches its storage area (`reference_only` under `knowledge_base/sources/`, `team_authored` under `knowledge_base/synthetic/`).

      If `checksum_sha256` is set in the manifest and the file's hash differs, that document **fails** and nothing of it is written.
   3. Extracts text with page numbers (PDF) or heading structure (Markdown), and normalises whitespace without touching code blocks, JSON, headers, response codes or algorithm names.
   4. Splits into chunks. Sections come first: a numbered heading in a PDF (for example `4.2 …`) or a Markdown heading. A long section is split further by size with overlap. A code block, JSON example, table or signature formula is never split. If one is bigger than the maximum, it becomes a chunk of its own.
   5. Gives each chunk:
      - a stable ID, derived from document, version, section and position, so re-running gives the same IDs;
      - the §12.1 metadata;
      - its `section_id` and page range.
   6. Embeds each chunk twice: a dense BGE-M3 vector, and a sparse BM25-style vector from the project's own tokenizer, which keeps identifiers such as `X-SIGNATURE`, `2007300` and `SHA256withRSA` whole.
   7. Upserts the chunks into the Qdrant collection named in `configs/retrieval.yaml`. For a document whose checksum is unchanged since the last run, it does nothing. When a document's checksum changes, it replaces all of that document's chunks.
   8. Prints a per-document summary (chunks, pages and sections; `ingested`, `unchanged` or `skipped`), and writes a JSON run record to the git-ignored `evals/reports/`. The record holds no document text.
3. **Search.** `uv run python scripts/search.py "<query>"` prints the top results, and the same search is a Python function for later phases.
   - The query is normalised without removing identifiers.
   - Qdrant runs a dense search and a sparse search in one request and fuses them with RRF. Both candidate depths and the final count come from config.
   - Each result has a run-scoped `citation_id` and every field of the §12.4 citation contract.
   - The excerpt is capped at 300 characters for `reference_only` documents and 1000 for `team_authored` ones (`configs/security.yaml`).
   - A synthetic document's title always carries its SYNTHETIC marker.
4. **Check citations.** A citation validator receives one run's retrieval result and a list of cited IDs. It returns `valid` or a reason for each:
   - `not_retrieved_in_run`
   - `source_not_approved`
   - `section_unresolved`
   - `excerpt_too_long`
   - `excerpt_not_in_chunk`

   Whether the excerpt actually supports the statement is left to human labelling, as §12.4 says.
5. **Measure retrieval.** `uv run python scripts/eval_retrieval.py` runs the 30 labelled queries in dense-only, sparse-only and hybrid modes. It computes Recall@5, @10 and @20 and MRR@10 per mode, writes the full results to the git-ignored `evals/reports/`, and prints a summary table. A query counts as a hit at k if any of its top-k chunks has a `(document_id, section_id)` in that query's gold set.
6. **Version conflicts (§12.5).** If two approved documents with the same `document_id` and different versions are both ingested, search returns chunks from both, each labelled with its own version. Nothing merges them. This is checked with synthetic fixtures; today's corpus has no such pair.

## Architecture and the reason for it

Follows `docs/tech.md` and `docs/structure.md`: Python 3.11+, uv, pytest, settings in `configs/`, CLIs in `scripts/`, CLI integration tests in `tests/integration/`. This is the first application code, placed where TRD §24 puts it: `app/rag/`.

| Decision | Reason |
|---|---|
| **Package `app/rag/`**, with modules for manifest loading, extraction, chunking, sparse encoding, embedding, the Qdrant store, search and citations. `scripts/ingest.py`, `scripts/search.py`, `scripts/eval_retrieval.py` and `scripts/fetch_models.py` are thin CLIs on top of it | The TRD §24 layout. Later phases (the P4 `search_qris_spec` tool, the P5 `retrieve_evidence` node) import the same functions |
| **Dense model: `BAAI/bge-m3` at revision `5617a9f61b028005a4858fdac845db406aefb181`** (MIT, 1024 dimensions), run on the local CPU through `sentence-transformers` 6.2.0 and CPU-only `torch` 2.15.0. Inputs are truncated at 1024 tokens (`max_seq_length`, from config) | It is multilingual, which the Indonesian + English corpus needs, and the TRD names it. CPU keeps GPU spend at zero. 1024 tokens leaves headroom above the 800-token chunk maximum, while capping CPU time well below the model's 8192 limit |
| **The embedding stack is an optional extra (`embed`)**, installed from the PyTorch CPU index. CI never installs it, and tests use a deterministic fake embedder behind the same interface | Default PyTorch on Linux pulls CUDA wheels of several GB. CI must run without model downloads (intent constraint) |
| **Sparse retrieval: the project's own BM25 encoder.** It lowercases, splits on whitespace and punctuation while keeping tokens such as `x-signature`, `2007300` and `sha256withrsa` whole, applies no stemming, hashes each term to a 32-bit index (BLAKE2b), and weights it by BM25 term frequency (k1 = 1.2, b = 0.75, from config). Qdrant's sparse index applies IDF (`modifier: idf`) | About 50 lines, deterministic and testable in CI. It works for both languages, because Snowball stemmers are per-language and would damage Indonesian or English. It also keeps identifiers intact, as §12.3 step 1 requires. It replaces the TRD's "BM25/full-text index" with BM25 scoring computed by the client plus Qdrant's IDF, so there is no separate full-text engine |
| **Fusion: Qdrant Query API**, one `query_points` call with two prefetches (dense and sparse) and `FusionQuery(RRF)`. Prefetch depth 30 each, final 20 | §12.3 says 20–30 candidates and RRF as the default. Server-side fusion is one round trip. It was confirmed working in `qdrant-client` local mode during design, so CI can test it with no server |
| **`qdrant-client` 1.19.1** | Matches the server's minor version (Qdrant v1.19.2, from P1). 1.20.0 is newer than the server |
| **PDF extraction: `pdfplumber` 0.11.10** (MIT), page by page | Keeps page numbers and handles table-heavy layouts better than plain `pypdf`. PyMuPDF was rejected because of its AGPL licence. The section-heading pattern is checked against the real PDFs during the build; the plan step for it pastes sample output |
| **Chunking from config** (`configs/retrieval.yaml`): target 600, maximum 800, overlap 80, measured in **estimated tokens = characters ÷ 4**. Overlap applies only inside long sections | Within §12.2's 400–800 / 60–100. The estimate keeps chunking deterministic and testable without downloading a tokenizer. The estimate can be off, so ingestion counts any chunk longer than the 1024-token embedding limit, by the real tokenizer (see Flagged concerns) |
| **The chunk registry is the Qdrant payload in P2. No PostgreSQL tables yet** | **This departs from TRD §17** (`documents`, `document_chunks`). The tables need the `app/db` layer and migrations, which arrive with the checkpointer (P5/P6). Nothing in P2 queries chunks except through Qdrant. Flagged below |
| **Model downloads use `truststore`** (verification against the operating system's certificate store) inside `scripts/fetch_models.py` only. Ingestion and search run with `HF_HUB_OFFLINE=1` | This machine's network inspects TLS, so Python's own certificate bundle rejects Hugging Face. Verification stays on; only the trust source changes. After the one-time fetch, nothing at run time reaches the internet, which keeps to the egress allowlist |
| **Gold labels by section, not by chunk.** `evals/datasets/retrieval_golden.yaml` stores each query, its language, and a list of `{document_id, section_id}` | Chunk IDs change when chunk sizes change in P3 tuning. Section IDs don't, so the labels stay valid |
| **Run-scoped citation IDs** (`CIT-` plus a counter per run); `chunk_id` is a UUIDv5 | The citation validator can check "retrieved in this run" by membership. The UUIDv5 makes IDs repeatable across re-ingestion |

## Data

- **Qdrant collection** `qris_kb_v1`:
  - Each point is one chunk, with a dense vector (1024, cosine) and a sparse BM25 vector.
  - The payload holds `chunk_id`, `document_id`, `title`, `publisher`, `source_url`, `document_version`, `effective_date`, `language`, `permission_basis`, `approval_status`, `page_start`, `page_end`, `section_id`, `section_title`, `checksum_sha256` (of the source file), `pipeline_fingerprint` (a hash of the chunking, sparse and embedding settings, added during the build on 2026-11-09 so that a config change re-ingests), `text_hash`, `ingested_at` and `text`.
  - It is local, in the existing Qdrant volume.
  - **It holds ASPI text** for `reference_only` documents. That is allowed, because the volume is local and never committed. Reference-only text only ever leaves it as an excerpt of at most 300 characters.
- **Manifest:** `checksum_sha256` is filled in for the four ingested sources. A checksum is a hash, not content, so it is committed.
- **Retrieval set** `evals/datasets/retrieval_golden.yaml` (committed):
  - 30 queries, with gold `(document_id, section_id)` pairs, written by Claude and reviewed by the user;
  - each entry records who reviewed it and when;
  - no ASPI text: queries are paraphrases written by the team, and labels are IDs.
- **Reports** (git-ignored `evals/reports/`): ingestion run records and retrieval results, holding IDs, scores and metrics, but no chunk text. A committed `docs/p2-knowledge-pipeline/retrieval-report.md` records the measured numbers.
- **Model weights:** about 2.3 GB in the user's Hugging Face cache, outside the repo.
- **Secrets:** none. Qdrant needs no API key on loopback (P1). There are no credentials in chunks, payloads or logs.

## Acceptance criteria

1. **Only approved, well-formed sources are ingested.**
   - Entries that aren't `approved`, entries without a `local_path`, and files stored in the wrong area for their permission basis are reported as `skipped` with a reason, and none of their chunks are written.
   - A file whose hash doesn't match a non-null manifest checksum fails, and none of its chunks are written.

   Unit tests.
2. **Chunking keeps structure.**
   - No chunk's estimated size exceeds the maximum, unless the chunk is exactly one block that can't be split.
   - A fenced code block, JSON example or Markdown table in the input appears unbroken in exactly one chunk.
   - Every chunk has a `section_id` that matches a heading in its source.
   - Overlap occurs only between chunks of the same section.

   Unit tests on the synthetic documents and on crafted inputs.
3. **Chunk IDs are stable.** Ingesting the same input twice gives identical chunk IDs and text hashes. A second ingestion with unchanged checksums upserts no points. Unit tests with the fake embedder and in-memory Qdrant.
4. **Every chunk has full metadata.** Every stored payload has all the fields listed under Data, none of them null, except `source_url` for `team_authored` documents and `page_start`/`page_end` for Markdown. A synthetic chunk's `title` contains `(SYNTHETIC — not an official ASPI/BI document)`. Unit tests.
5. **The sparse encoder keeps identifiers.** `X-SIGNATURE`, `X-TIMESTAMP`, `2007300`, `4017300`, `SHA256withRSA`, `/v1.0/access-token/b2b` and `HMAC_SHA512` each produce the same term on the query side and the document side, and none is split into fragments. The weights match the BM25 formula for given k1, b and average document length. Unit tests.
6. **Hybrid search fuses both signals.** In in-memory Qdrant with the fake embedder:
   - a query found only by its exact identifier (sparse only), and a query found only by meaning (dense only), each return their target in the fused top 5;
   - every result carries every §12.4 field.

   Unit tests.
7. **Excerpts respect licence limits.** No excerpt from a `reference_only` chunk exceeds 300 characters, and none from a `team_authored` chunk exceeds 1000. Both limits are read from `configs/security.yaml`. Unit test.
8. **The citation validator rejects bad citations.** Each of the five rejection reasons in Behaviour 4 has a test, and a valid citation passes. Unit tests.
9. **Version conflicts are kept apart.** Two synthetic fixtures with the same `document_id` and versions `0.1.0` and `0.2.0` are ingested. A query matching both returns chunks from each, with distinct `document_version` values, and no chunk mixes text from both. Unit test.
10. **The CLIs work end to end without services.**
    - `scripts/ingest.py` runs as a subprocess against a temporary manifest of synthetic documents, using in-memory Qdrant and the fake embedder (a `--test-backends` flag that is refused unless `DEVGUARD_TEST_BACKENDS=1` is set). It exits 0 and prints one summary line per document.
    - `scripts/search.py` returns results in the same mode.
    - On a missing file, both exit non-zero with a one-line error and no traceback.

    Integration tests.
11. **The retrieval set is complete and reviewed.** `evals/datasets/retrieval_golden.yaml` has exactly 30 queries, at least 10 in Indonesian and at least 10 in English, covering all 4 documents. Every gold pair names an approved `document_id` and a `section_id` that exists in the ingested corpus. Every entry has `reviewed_by` and `reviewed_at`.
    - The schema and the ID checks against the manifest are a unit test.
    - The check that every `section_id` exists is part of the eval run, because it needs the corpus.
12. **Real ingestion works locally.**
    - With the PDFs present, BGE-M3 cached and Qdrant running, `scripts/ingest.py` ingests all 4 sources. Evidence is the pasted output: chunks per document, wall time, and the number of truncated chunks.
    - A second run reports all 4 as `unchanged`.
13. **The retrieval report is measured and recorded.**
    - `scripts/eval_retrieval.py` runs locally on the real corpus and the 30 queries.
    - The pasted output gives Recall@5/10/20 and MRR@10 for dense, sparse and hybrid.
    - The numbers are copied into `docs/p2-knowledge-pipeline/retrieval-report.md`, which is labelled as measured, with date, corpus checksums and config values.
    - **No pass/fail threshold applies in P2.** The ≥ 0.90 Recall@20 gate is a P3/P9 release gate. The report states the result against it, whatever that is.
14. **Licence and secrets hygiene.** No tracked file contains more than 300 consecutive characters of text from the ASPI PDFs. This is checked by a local test that runs only when the PDFs are present, and is skipped in CI with a stated reason. Repo-hygiene tests still pass.
15. **CI still passes with nothing installed beyond the default dependencies.** `uv run pytest` passes in CI with no Qdrant, no model, no PDFs and no `embed` extra, and every earlier test passes unchanged.

## Flagged concerns

- **Departure from TRD §17: no PostgreSQL `documents` or `document_chunks` tables in P2.** The Qdrant payload is the chunk registry for now. The tables arrive with `app/db` and migrations, alongside the checkpointer (P5/P6). **Risk:** two registries to keep in step later. **Decides:** Kharisma Wardhana.
- **Compliance: ASPI text in local storage.** Extracted text of `reference_only` PDFs is stored in the local Qdrant volume and inside eval runs held in memory. It never enters git, reports, logs or Langfuse traces, and excerpts are capped at 300 characters. AC #14 tests the git side. **Decides:** Kharisma Wardhana (accepts the reference-only position from P0).
- **Truncation risk.** Characters ÷ 4 underestimates tokens for some text, such as Indonesian with many affixes, or base64 or JSON. A chunk over 1024 real tokens would be truncated by BGE-M3, and its tail is invisible to dense search, though still visible to sparse. Ingestion counts and reports truncated chunks (AC #12). If any are truncated, lowering `max` is a config change in P3. **Decides:** Kharisma Wardhana, after seeing the count.
- **Supply chain: a 2.3 GB model and a new dependency tree** (torch, transformers, sentence-transformers, pdfplumber, qdrant-client). Mitigations:
  - the model revision is pinned by commit SHA;
  - every package is pinned exactly in `uv.lock`;
  - the embedding stack is optional and never installed in CI;
  - `weights_only` loading is enforced by transformers 5.x.

  `docs/threat-model.md` gets a row for model and parser supply chain (new T19), and the setup-time download from `huggingface.co` is noted as an egress exception, with run time offline. **Decides:** Kharisma Wardhana.
- **Untrusted retrieved text.** PDF text could carry instruction-like strings. In P2 the text only flows into search results, but every result is marked as untrusted data for P5's prompt assembly (TRD invariant). Nothing in P2 executes or follows retrieved text. No further decision needed; recorded for P5.
- **Machine quirk: TLS inspection.** `truststore` is a new runtime dependency used only by the fetch script. If your organisation forbids trusting its inspection root for Python, the fallback is to download the model by hand with a browser into the HF cache. **Decides:** Kharisma Wardhana.
- **Schedule.** The deadline is 2026-11-09. The 30-query review needs your time once the PDFs are downloaded, so it is the critical path. **Decides:** Kharisma Wardhana.
- **Privacy:** none found. Checks covered: the corpus is public reference documents plus synthetic documents, queries are team-written, and no personal or merchant data is involved.
- **Accessibility and UX:** none found. There is no UI, only CLI output.

## Out of scope

- Cross-encoder reranking, MRR@5/nDCG@5 after reranking, and before/after reranking reports (P3).
- Tuning chunk size, k1, b or RRF parameters against the set (P3). P2 records the numbers for the first config.
- PostgreSQL `documents` and `document_chunks` tables (P5/P6, flagged above).
- The MCP `search_qris_spec` tool, the LangGraph `retrieve_evidence` node, and prompt assembly with citations (P4/P5).
- OCR. If a PDF page has no text layer, it is reported as such, not OCR'd.
- Fetching sources from URLs. Ingestion reads only local files listed in the manifest.
- Langfuse tracing of ingestion and search (P8).

## Resolved questions

- **Which embedding model, how big, fast enough on CPU, how pinned?** BGE-M3 (568M parameters, 1024 dimensions, MIT) at revision `5617a9f…`, on CPU through sentence-transformers 6.2.0 and torch 2.15.0 (CPU build). It is fetched once by `scripts/fetch_models.py` and used offline afterwards. CPU speed is measured in AC #12 and not assumed; the corpus is 4 documents.
- **BGE-M3 sparse vectors, or Qdrant BM25?** Neither as such. It is a project BM25 encoder: identifier-preserving tokens, no stemming, BM25 term frequency computed by the client, and IDF applied by Qdrant (`modifier: idf`). BGE-M3's sparse head would need the FlagEmbedding stack, and it splits identifiers into subword pieces. Qdrant's server-side BM25 stems per language. Server-side RRF fusion in the Query API was confirmed working in `qdrant-client` 1.19-compatible local mode.
- **Which PDF parser, and how are pages and headings kept?** `pdfplumber` 0.11.10 (MIT), page by page, so page numbers come from the PDF. Sections are found by a numbered-heading pattern, which is checked against the real PDFs in the build with sample output pasted. PyMuPDF was rejected (AGPL).
- **Chunk size and overlap?** Target 600, maximum 800, overlap 80 estimated tokens (characters ÷ 4), with overlap only inside long sections, all in `configs/retrieval.yaml`. Truncated chunks are counted and reported, and tuning happens in P3.
- **PostgreSQL metadata now, or Qdrant payload only?** Qdrant payload only in P2. The TRD §17 tables come with the DB layer in P5/P6. This is a departure from the TRD, flagged above.
- **How does CI test this without the ASPI PDFs?** With the committed synthetic Markdown documents, a minimal synthetic PDF built inside the test from raw PDF syntax, in-memory Qdrant and a deterministic fake embedder. Real-corpus checks (AC #12–14) are local, with pasted evidence.
- **What counts as a gold section, so Recall@k survives re-chunking?** A `(document_id, section_id)` pair, where `section_id` is the source's own numbering (for example `4.2`) or a Markdown heading slug. A query is a hit at k if any of its top-k chunks belongs to a gold section.
- **What if a PDF cover shows a different version or date than the manifest?** Ingestion never guesses or edits versions. The plan's first build step has you confirm both covers, and the manifest note already asks for the data spec's cover to be re-checked. A mismatch is fixed in `sources.yaml` through the normal manifest process, with evidence, before ingestion. From then on, the checksum guard (AC #1) stops a different file being ingested silently under that version.
