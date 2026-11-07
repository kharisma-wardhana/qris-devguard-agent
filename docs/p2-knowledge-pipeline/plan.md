# Plan: The four approved sources are ingested into Qdrant with chunk-level citation metadata, and hybrid search is measured on a 30-query labelled retrieval set
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-07. Via /aspec:make-it-simple-v2.*
*Implements: docs/p2-knowledge-pipeline/spec.md*

## Affected files

- `pyproject.toml`, `uv.lock`: edit.
  - Default dependencies gain `qdrant-client==1.19.1` and `pdfplumber==0.11.10`.
  - A new `embed` extra holds `sentence-transformers==6.2.0`, `torch==2.15.0` (from the PyTorch CPU index) and `truststore`.
  - pytest gets `pythonpath = ["."]`, so tests import `app`.
- `configs/retrieval.yaml`: new. Collection name, embedding model ID, revision, dimension and `max_seq_length`, chunk sizes, BM25 k1 and b, prefetch depths, final limit, and the file-size limit.
- `app/__init__.py`, `app/rag/__init__.py`: new.
- `app/rag/config.py`: new. Loads `configs/retrieval.yaml` and the citation limits from `configs/security.yaml`.
- `app/rag/manifest.py`: new. Selects approved sources, then checks storage area, type, size and checksum.
- `app/rag/extract.py`: new. PDF pages through pdfplumber, and Markdown sections.
- `app/rag/chunking.py`: new. Splits by section first, keeps blocks whole, applies size and overlap, and assigns stable IDs.
- `app/rag/sparse.py`: new. The identifier-preserving BM25 encoder.
- `app/rag/embedding.py`: new. The embedder interface, the BGE-M3 implementation (imported only when used) and the deterministic fake.
- `app/rag/store.py`: new. Creates the Qdrant collection, upserts, finds a document's checksum, and deletes a document.
- `app/rag/ingest.py`: new. The ingestion pipeline and run record.
- `app/rag/search.py`: new. Query normalisation, the hybrid `query_points` call with RRF, and citation building.
- `app/rag/citations.py`: new. The citation validator.
- `app/rag/metrics.py`: new. Recall@k and MRR@k.
- `scripts/fetch_models.py`: new. Downloads the pinned BGE-M3 using `truststore`.
- `scripts/ingest.py`, `scripts/search.py`, `scripts/eval_retrieval.py`: new CLIs.
- `evals/datasets/retrieval_golden.yaml`: new. The 30 labelled queries.
- `tests/unit/test_rag_config.py`, `test_rag_manifest.py`, `test_rag_chunking.py`, `test_rag_sparse.py`, `test_rag_embedding.py` (step 5; added during the build), `test_rag_store_ingest.py`, `test_rag_search.py`, `test_rag_citations.py`, `test_rag_metrics.py`, `test_retrieval_golden.py`, `test_licence_hygiene.py`: new.
- `tests/fixtures/rag/`: new. Small synthetic Markdown inputs, including two versions of one document for the conflict test.
- `tests/integration/test_rag_cli.py`: new. Runs `ingest.py`, `search.py`, `eval_retrieval.py` and `fetch_models.py` as subprocesses.
- `knowledge_base/manifests/sources.yaml`: edit. Fills in `checksum_sha256` for the 4 ingested sources, and corrects a cover version or date only if you find a mismatch (with evidence).
- `docs/threat-model.md`: edit. New T19 (model and parser supply chain), and the setup-time egress exception for `huggingface.co`.
- `docs/tech.md`, `docs/structure.md`: edit. New libraries, the `app/rag/` layout, the `embed` extra, and new commands.
- `README.md`: edit. A short "Knowledge pipeline" section with the commands.
- `docs/p2-knowledge-pipeline/retrieval-report.md`: new. The measured numbers.
- `docs/p2-knowledge-pipeline/tasks.md`: new.
- `docs/index.md`: edit. Links to the plan, tasks and report.
- `CLAUDE.md`: merge (step 13, below).

## Work sequence

1. **Dependencies and config.** `pyproject.toml`, `uv.lock`, `configs/retrieval.yaml`, `app/__init__.py`, `app/rag/__init__.py`, `app/rag/config.py`, `tests/unit/test_rag_config.py`. Spec AC #7 (limits read from config) and #15.
2. **Manifest selection and file checks.** `app/rag/manifest.py`, `tests/unit/test_rag_manifest.py`. Spec AC #1.
3. **Extraction and chunking.** `app/rag/extract.py`, `app/rag/chunking.py`, `tests/unit/test_rag_chunking.py`, `tests/fixtures/rag/`. Spec AC #2 and the chunk part of #4.
4. **Sparse BM25 encoder.** `app/rag/sparse.py`, `tests/unit/test_rag_sparse.py`. Spec AC #5.
5. **Embedders and model fetch.** `app/rag/embedding.py`, `scripts/fetch_models.py`, `tests/unit/test_rag_embedding.py`, and the `fetch_models` part of `tests/integration/test_rag_cli.py`. Spec AC #15. The real fetch runs in step 10.
6. **Store and ingestion pipeline.** `app/rag/store.py`, `app/rag/ingest.py`, `scripts/ingest.py`, `tests/unit/test_rag_store_ingest.py`, and the `ingest` part of `tests/integration/test_rag_cli.py`. Spec AC #1, #3, #4, #10.
7. **Hybrid search and citation validator.** `app/rag/search.py`, `app/rag/citations.py`, `scripts/search.py`, `tests/unit/test_rag_search.py`, `tests/unit/test_rag_citations.py`, and the `search` part of `tests/integration/test_rag_cli.py`. Spec AC #6, #7, #8, #9, #10.
8. **Retrieval metrics and eval CLI.** `app/rag/metrics.py`, `scripts/eval_retrieval.py`, `tests/unit/test_rag_metrics.py`, `tests/unit/test_retrieval_golden.py` (schema check, run against a 2-query sample until step 11), and the `eval_retrieval` part of `tests/integration/test_rag_cli.py`. Spec AC #11 (schema) and #13 (harness).
9. **Threat model and steering docs.** `docs/threat-model.md`, `docs/tech.md`, `docs/structure.md`, `README.md`. Spec flagged concerns: supply chain and egress.
10. **Real corpus (you, then me).**
    1. You download the two PDFs to their `local_path`s and confirm the version and date on each cover.
    2. I print each PDF's detected headings and page count, and paste a sample, so the heading pattern is checked against reality. If the pattern needs a fix, it lands in this step with a regression test.
    3. `uv sync --extra embed`, then `scripts/fetch_models.py` (you approve the download).
    4. Real ingestion, run twice, and the checksums go into `sources.yaml`.
    5. `scripts/check_sources.py` passes.
    6. `tests/unit/test_licence_hygiene.py` is added.

    Files: `knowledge_base/manifests/sources.yaml`, `tests/unit/test_licence_hygiene.py`, and possibly `app/rag/extract.py` with its test. Spec AC #12, #14.
11. **The 30-query retrieval set.**
    1. I draft `evals/datasets/retrieval_golden.yaml`: at least 10 Indonesian and at least 10 English queries, covering all 4 documents, with gold section IDs taken from the step 10 heading list.
    2. You review and correct it.
    3. `reviewed_by` and `reviewed_at` are filled in **only for entries you approved**.

    Spec AC #11.
12. **Measured retrieval report.** Run `scripts/eval_retrieval.py` on the real corpus, paste the output, and write `docs/p2-knowledge-pipeline/retrieval-report.md`. Spec AC #13.
13. **`CLAUDE.md` merge.** Agent configuration, below.

**Parallel work:** steps 2, 3 and 4 have no dependency on each other and share no files, so they could run in separate git worktrees. Given the size, I'll do them in sequence in this session unless you ask for worktrees.

## Test coverage

These rows from `test-scope-by-stage.md` matched:
- **Backend service / script only.** CLIs are the entry points, so each gets an integration test that runs it as a subprocess. Applies to steps 5, 6, 7 and 8.
- **Any database.** The Qdrant collection is stored data, and this feature creates its schema (vector names, sparse IDF modifier, payload fields). Applies to steps 6 and 7. Step 6's tests pin the collection config and payload fields, so a later change to either is caught as a regression.
- **Phase 0 detected an existing project.** Regression for existing behaviour that a step changes. Applies to step 1 (`pyproject.toml`: the full earlier suite passes unchanged), step 9 (`docs/threat-model.md`: `test_repo_hygiene.py` unchanged) and step 10 (`sources.yaml`: `test_check_sources.py` and `scripts/check_sources.py` unchanged and passing).

Not matched: UI (none), accounts and roles (none in P2).

- **Step 1:**
  - Unit: the config loads; required keys are present; the dimension is 1024; `max_seq_length` ≥ chunk max; the citation limits are 300 and 1000 from `security.yaml`.
  - Regression: the whole earlier suite (132 tests) passes unchanged.
- **Step 2:** unit tests for each skip reason (not approved, no `local_path`, wrong storage area, wrong type, over the size limit) and for a checksum mismatch giving a failure.
- **Step 3:** unit tests on the two synthetic documents, crafted Markdown (a fenced code block, a JSON block, a table, an oversize block, a long section) and a minimal PDF built in the test. They cover: the maximum size, blocks kept whole, `section_id` taken from a real heading, overlap only within a section, page numbers from the PDF, and the same IDs across two runs.
- **Step 4:** unit tests: the seven identifiers each stay one term and match on both sides; BM25 weights equal the formula; hashing is stable across runs.
- **Step 5:**
  - Unit: the fake embedder is deterministic with dimension 1024; the BGE-M3 class raises a clear error when the `embed` extra is missing (in CI it is missing).
  - Integration: `fetch_models.py` without the extra exits non-zero with a one-line message, without a traceback or a download.
- **Step 6:**
  - Unit, in in-memory Qdrant with the fake embedder:
    - collection config: dense 1024 cosine, sparse with IDF;
    - every payload field is present (AC #4);
    - synthetic titles carry the marker;
    - a re-run with unchanged checksums upserts 0 points;
    - a changed checksum replaces only that document's points;
    - a skipped document writes nothing.
  - Integration: `ingest.py --test-backends` as a subprocess with `DEVGUARD_TEST_BACKENDS=1`, against a temporary manifest. It exits 0 with one line per document. Without the variable, it is refused. A missing file gives exit 1 and one error line.
- **Step 7:**
  - Unit: a sparse-only target and a dense-only target are each in the fused top 5; every §12.4 field is present; the excerpt caps hold (300 and 1000); the conflict fixture returns both versions, labelled; the citation validator has five rejection tests and one pass.
  - Integration: `search.py --test-backends` returns results; a missing query argument exits non-zero with usage.
- **Step 8:**
  - Unit: Recall@k and MRR@k against hand-computed cases (including no gold hit, and several gold sections); the golden-file schema (count, languages, approved IDs, review fields).
  - Integration: `eval_retrieval.py --test-backends` on a 2-query sample prints the table for all three modes.
- **Step 9:** documentation only. Checked at Phase 4 by quoting the T19 row. Regression: `test_repo_hygiene.py` unchanged.
- **Step 10:**
  - Local evidence, pasted: heading sample, fetch output, ingest output for both runs (chunk counts, wall time, truncated-chunk count), `check_sources.py`.
  - Unit: `test_licence_hygiene.py` scans tracked files for any 300-character run of extracted ASPI text. It is skipped with a reason when the PDFs are absent, as in CI.
  - Regression: the checker tests are unchanged.
- **Step 11:** the golden-file schema test runs against the real 30-query file. The "section exists" check runs inside the step 12 eval.
- **Step 12:** pasted eval output. The report's numbers match it.
- **Step 13:** covered by the existing `agent-config-eval` workflow.

## Agent configuration changes

- **`CLAUDE.md` (step 13).**
  - Append to `## Commands`: `uv sync --extra embed`, `uv run python scripts/fetch_models.py`, `uv run python scripts/ingest.py`, `uv run python scripts/search.py "<query>"`, `uv run python scripts/eval_retrieval.py`.
  - Append to `## Test scope`: "Qdrant collection config or payload fields: add a regression test when either changes."
  - Nothing else changes. You'll see the diff and approve it with `approve write`.
- **Hooks:** none added. Ingestion and search are local and need no gate. `fetch_models.py` downloads from Hugging Face, but it is a one-time setup step that you run, not an environment change. Verified in Phase 0: `eval.sh` 6/6 passing.
- **`.claude/settings.json`:** unchanged. The `.env` read deny still applies, and P2 adds no secrets.
- **CI:** no new job. `ci.yml` runs the new tests with default dependencies only. `agent-config-eval` is present exactly once. `security-scan` covers the new dependencies once they're locked.
- **Subagents:** none.

## Rollback

- **Code:** revert the branch commits, or drop `feature/p2-knowledge-pipeline`.
- **Data:** delete the collection: `curl -X DELETE http://127.0.0.1:6333/collections/qris_kb_v1`. The source files and the manifest are unaffected, apart from the checksum fields, which revert with the commit.
- **Model:** remove the BGE-M3 folder from the Hugging Face cache, `~/.cache/huggingface/hub/models--BAAI--bge-m3`.
