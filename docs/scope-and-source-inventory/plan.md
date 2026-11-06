# Plan: Phase P0 scope and source inventory is signed off and the source list is confirmed safe to use publicly
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Implements: docs/scope-and-source-inventory/spec.md*

## Affected files

- `knowledge_base/manifests/source.schema.json`: edit. Adds the `reference_only` basis and the `local_path` field, and widens the `effective_date` pattern.
- `scripts/check_sources.py`: edit. Adds the synthetic/storage/evidence rules from spec § Architecture.
- `tests/unit/test_check_sources.py`: edit. Adds unit tests for the new rules and date formats.
- `tests/integration/test_check_sources_cli.py`: new. Runs the checker as a CLI and asserts exit codes.
- `knowledge_base/synthetic/synthetic-qris-mpm-service-spec.md`: new. The synthetic MPM spec (Generate QR, Query Payment).
- `knowledge_base/synthetic/synthetic-qris-troubleshooting-sop.md`: new. The synthetic SOP.
- `tests/unit/test_synthetic_docs.py`: new. Checks the marker line and required headings.
- `knowledge_base/manifests/sources.yaml`: edit. The final source set from spec AC #2.
- `configs/security.yaml`: edit. Adds the excerpt limit for reference-only sources (300 characters).
- `tests/unit/test_repo_hygiene.py`: new. Checks that nothing tracked is in the sources directory and that the excerpt limit is present.
- `docs/threat-model.md`: edit. Adds T16 (redistributing documents without a licence), T17 (synthetic mistaken for official) and the separation-of-duties residual risk.
- `docs/scope.md`: edit. §1.1 endpoint confirmations with `document_id`s, §3/§4 reference-only policy, exit criteria. The §5 signatures are entered by the user.
- `docs/structure.md`: edit. Adds `knowledge_base/synthetic/` and `tests/integration/` to the directory map.
- `CLAUDE.md`: merge. Adds the missing template sections (agent configuration, below).
- `docs/scope-and-source-inventory/tasks.md`: new. Mirrors the work sequence.
- `docs/index.md`: edit. Links `plan.md`.

## Work sequence

1. **Schema and checker rules.** Files: `source.schema.json`, `check_sources.py`, `tests/unit/test_check_sources.py`. Advances spec AC #3, #4 and #11.
2. **Synthetic documents.** Files: both `knowledge_base/synthetic/*.md` and `tests/unit/test_synthetic_docs.py`. Advances AC #5 and #6.
3. **User check, then the final manifest.**
   - First, the user opens the SNAP Technical & Security standard PDF, confirms the cover version is 1.0.2, and reads the usage-guide PDF (spec § Flagged concerns 1–2).
   - Then I rewrite `sources.yaml` with the confirmed values and add `tests/integration/test_check_sources_cli.py`.
   - Advances AC #1 and #2.
   - If the cover version isn't 1.0.2, or the usage guide forbids reference use, stop and re-open the spec.
4. **Excerpt limit and repo hygiene.** Files: `configs/security.yaml`, `tests/unit/test_repo_hygiene.py`. Advances AC #9 and #10.
5. **Threat model.** File: `docs/threat-model.md`. Advances AC #8.
6. **Scope document and structure map.** Files: `docs/scope.md` (§1.1, §3, §4, the exit criteria) and `docs/structure.md`. Advances AC #7, partly.
7. **User sign-off.** The user enters a name and date in each `docs/scope.md` §5 row, on or before 2026-11-10. Completes AC #7.
8. **`CLAUDE.md` merge.** File: `CLAUDE.md`. This is agent configuration; see below.

## Test coverage

Only these rows from `test-scope-by-stage.md` matched: *Backend service / script only*, and *Phase 0 detected an existing project*. Nothing applies for UI, login/roles or the database. `configs/security.yaml` roles are policy text here and no code enforces them yet.

- **Step 1:**
  - Unit tests for each new checker rule, (a) to (g), and for each date value in AC #4.
  - Regression: the existing 8 tests in `test_check_sources.py` must still pass unchanged, because this step changes `check_manifest`, which already existed.
- **Step 2:** unit tests for the synthetic-document marker and required headings.
- **Step 3:**
  - An integration test at the entry point: run `scripts/check_sources.py` as a subprocess, expecting exit 0 on the committed manifest and exit 1 on a deliberately invalid temporary manifest.
  - Regression: the "committed manifest is valid" test still passes against the new manifest.
- **Step 4:** unit tests for git-tracked files in the sources directory and for the excerpt-limit key.
- **Steps 5–7:** documentation only, with no automated test. The AC #7 and #8 checks are done by reading the files at Phase 4 and quoting the relevant lines.
- **Step 8:** the agent-configuration eval is required on this path, but there's no CI platform yet (Phase 0: "not yet"), so it is **deferred**. See below.

## Agent configuration changes

- **`CLAUDE.md` merge (step 8):**
  - Adds `## Documentation layout`, `## Test scope` (the two matched rows above), `## Workflow` (intent → spec → plan) and an empty `## Common mistakes`.
  - The existing `## Project context` pointer and all user-written sections are kept unchanged.
  - I'll show the merged diff and wait for `approve write`.
- **Subagents:** none. Nothing in the spec is a recurring, well-bounded task yet.
- **Build-time hooks:** none proposed. `jq` is not installed here, so any later hook will be written in Python under the same fail-closed contract.
- **CI agent-configuration eval job:** deferred until a CI platform is chosen. It is recorded here so a later feature picks it up.

## Rollback

Every change is on branch `feature/scope-and-source-inventory`, which nothing has merged. To roll back, revert or drop the branch's commits. There is no data or external state to undo.
