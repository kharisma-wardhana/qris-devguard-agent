# REVIEW.md
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Amended: 2026-11-07 for p1-infrastructure.*
*Amended: 2026-11-09 for p2-knowledge-pipeline.*
*Amended: 2026-11-09 for p3-reranking-eval.*
*Amended: 2026-11-10 for p4-deterministic-mcp-tools.*
*Amended: 2026-11-10 for p5-langgraph-agent.*
*Amended: 2026-11-11 for p6-checkpoint-hitl.*
*Amended: 2026-11-11 for p7-mcp-hardening.*
*Amended: 2026-11-12 for p9-demo-ui-alerts.*
*Amended: 2026-11-12 for p8-memory-observability.*

Claude reviews pull requests on this repo against this file. Read `CLAUDE.md` and the feature's `docs/<feature-name>/spec.md` first. The review checks the diff against the spec's acceptance criteria.

## Passes

### Bugs
- Every field added to `knowledge_base/manifests/source.schema.json` must also have a matching rule in `scripts/check_sources.py`, where one is needed. Each new rule must have one passing and one failing unit test in `tests/unit/test_check_sources.py`.
- **Dates in `sources.yaml` must be quoted or month-only.** PyYAML turns an unquoted full date (for example `2024-09-15`) into a `date` object, and the schema then rejects it. `2024-09` is safe because PyYAML reads it as a string.
- Tests that read repo files (`tests/unit/test_synthetic_docs.py`, `test_repo_hygiene.py`, `tests/integration/`) must locate files relative to the repo root, never relative to the working directory.
- A ticked box in `docs/<feature-name>/tasks.md` must have a matching commit that contains the step's work and its tests. A step added to `plan.md` must appear in `tasks.md` in the same commit.
- `docs/index.md` must link every `docs/<feature-name>/` folder.
- **P1:** `configs/model.yaml`, `deploy/runpod/bootstrap_vllm.sh` and `scripts/tunnel.sh` (port) change together. A value changed in only one fails the config-agreement test in `tests/unit/test_bootstrap_vllm.py`.
- **P1:** local URLs in `.env.example` use `127.0.0.1`, never `localhost` (on Windows, `localhost` tries IPv6 first and every connection stalls).
- **P1:** `docs/runpod-deployment.md` fetches the bootstrap script only by commit SHA, never by branch name.
- **P2:** a new setting that changes chunks or vectors must be included in `pipeline_fingerprint()` (`app/rag/ingest.py`). Otherwise re-ingestion reports `unchanged` and keeps stale vectors.
- **P2:** a chunking or extraction change that renames section IDs must update `evals/datasets/retrieval_golden.yaml` in the same PR. `scripts/eval_retrieval.py` refuses gold sections that are missing from the corpus.
- **P2:** editing a synthetic document updates its `checksum_sha256` in `sources.yaml` in the same commit (`test_real_manifest_checksums_match_committed_synthetic_files`).
- **P2:** CLIs in `scripts/` print UTF-8 (Windows consoles default to cp1252) and fail with one `error:` line, never a traceback.
- **P3:** `rerank()` only reorders. Any change keeps the permutation, `citation_id` and §12.4-field tests in `tests/unit/test_rag_rerank.py`; `select()` takes `top_n` from `configs/retrieval.yaml`, never a literal.
- **P3:** the original 30 entries of `evals/datasets/retrieval_golden.yaml` are pinned by content hash (`test_original_thirty_unchanged_since_p2`). New queries are appended with `set:` and a `dataset_version` bump; the old entries are never edited.
- **P3:** the `rerank` block stays outside `pipeline_fingerprint()`. A reranker change must not trigger re-ingestion, and nothing that changes chunks or vectors may move into `rerank`.
- **P3:** `scripts/eval_retrieval.py` computes fused and reranked metrics from the same hybrid top-20 pool object, never from a second search.
- **P4:** a validator never decides by LLM, never mutates its input (`test_input_is_not_modified`) and never picks a "closest" schema: an unknown endpoint/version pair is `INVALID_ARGUMENT`.
- **P4:** a canonicalization change is a new profile name (`-v2`) in `app/validators/canonical.py`, never an edit to an existing profile. The hashes in `knowledge_base/fixtures/signatures/kat.yaml` are never edited to make a test pass.
- **P4:** `scripts/eval_validators.py` stays at 100% on every PR. A `requires_table` response-code case is reported skipped in CI, never counted as passed.
- **P4:** editing `knowledge_base/fixtures/fixtures.yaml` updates its `checksum_sha256` in `sources.yaml` in the same commit (`test_fixture_index_checksum_matches_manifest`). The file stays `eol=lf` in `.gitattributes`.
- **P4:** the tool list, `config.TOOL_NAMES` and the `tools:` matrix in `configs/security.yaml` change together.
- **P5:** intent, risk, tool choice and tool arguments stay deterministic (`app/agent/classify.py`, `select_tools`). No change may let model output pick a node, a tool or an argument.
- **P5:** verdicts, sources and confidence are assembled by `app/agent/answer.py` from tool output, never taken from model text. A change to `policy.py` keeps a breaking-output test per rule in `tests/unit/test_agent_policy.py`.
- **P5:** `evals/datasets/agent_flows.yaml` flows pass in CI mode (`scripts/eval_agent.py`). A routing or policy change adds or updates a flow property; it never deletes one to go green.
- **P5:** live flow results in `docs/p5-langgraph-agent/agent-flow-report.md` stay as measured. Flow 1's recorded FAIL is fixed in P6 and re-measured, never edited into a pass.
- **P6:** a schema change is a new `app/db/migrations/NNNN_*.sql` file, never an edit to an applied one. A regression test runs it on a fresh schema (`test_db_migrate.py`).
- **P6:** `app/db/actions.py` is the only place the payload is normalized and hashed. The agent and the ticket tool must both use it. A change to normalization changes every hash, so it needs a migration note.
- **P6:** a node that calls `interrupt()` re-runs from its first line on resume. Side-effect records are created in an earlier, checkpointed node and are idempotent.
- **P6:** a PR touching `app/db/`, `app/agent/graph.py`, the ticket tool or the thread/approval routes pastes the local `uv run pytest -m db` run. CI skips these tests, so CI green proves nothing about them.
- **P7:** every MCP tool registers through `GuardedTool`, which writes one audit row per call. `create_support_ticket` writes `attempt` before it touches the ticket store and refuses with `AUDIT_UNAVAILABLE` if that insert fails. A tool or code path that skips the audit is a blocker.
- **P7:** a change to security behaviour updates the cases that prove it in `evals/datasets/security_cases.yaml` and re-renders `docs/p7-mcp-hardening/security-report.md` with `scripts/eval_security.py`. The generated block is never edited by hand, and a case or a test node is never removed to turn the report green.
- **P7:** a burst or flood test gets a dedicated server or a lenient limiter, because every loopback caller shares one address bucket. A test that relies on ordering to stay under a limit is a bug.
- **P7:** a PR touching `app/db/roles.py`, `app/db/audit.py`, a migration, the role DSNs or `GuardedTool` pastes the local `uv run pytest -m db` run and `uv run python scripts/eval_security.py` output. The role matrix and audit tests skip in CI.
- **P9:** `app/alerts/receiver.py` stays standard-library only (`test_receiver_imports_standard_library_only`), and its limits move together with `configs/agent.yaml` `alerts` (`test_limits_match_configs_agent_yaml`).
- **P9:** Streamlit reads go through `read()` and writes through `write()` in `app/ui/streamlit_app.py`. A new per-rerun API call outside the cache is a bug: a few clicks then get 429 from the `api_other` bucket.
- **P9:** `Agent.history` takes each run's answer from its newest checkpoint with no `next`. A PR touching it, or the thread/approval list routes, pastes `uv run pytest -m db tests/unit/test_api_p9_routes.py tests/integration/test_ui_e2e.py`.
- **P8:** what memory saves is decided only by the configured phrase table in `app/memory/policy.py`; model output never picks a key, a value or whether to save. A run whose input held a secret saves nothing.
- **P8:** scores are computed in `app/observability/scores.py` from run state, never by an LLM. A new score gets one fixture per value in `test_scores.py`; a score that wasn't measured is not sent, never sent as 0.
- **P8:** `retention.purge` stays one transaction in foreign-key order, and `--dry-run` rolls back the same deletes. Pending approvals and `awaiting_approval` threads are never purged; `test_retention.py`'s inside/outside rows and app-role test stay.
- **P8:** a PR touching `app/memory/`, `app/db/retention.py`, migration `0003` or the memory routes pastes the local `uv run pytest -m db` run.

### Security
- Nothing under `knowledge_base/sources/` may be tracked except `.gitkeep`. No `.env*` file other than `.env.example`, and no `*.pem`, `*.key` or `*.p12` file, may be added anywhere.
- `.env.example` must contain placeholders only (`replace-me` style). No real-looking keys, tokens or hosts.
- Synthetic documents and fixtures must contain no real merchant, partner, customer or credential values.
- `configs/security.yaml` must not get narrower without a matching `docs/threat-model.md` change. In particular, check that nothing was removed from `redact_fields`, `prohibited_actions` or `egress_allowlist`.
- Workflows in `.github/workflows/` keep `permissions: contents: read`, use no `pull_request_target`, reference no secrets, and run no deploy step.
- `.claude/hooks/*.sh` keep the fail-closed contract: no `set -e`, every error path emits `ask`, exit code is always 0. `.claude/settings.json` may only gain entries. A removed hook or `deny` rule is a blocker unless the PR states why.
- **P1:** in `docker-compose.yml`, every port stays on `127.0.0.1`, every image stays pinned, no secret appears on a `command:` or `healthcheck:` line, and no `mem_limit` is used (rootless Podman can't apply it).
- **P1:** vLLM binds to `127.0.0.1` with no `--api-key` on its command line, and the runbook's pod table exposes no HTTP port. No IP address, pod ID or `ssh.runpod.io` user string appears in a tracked file.
- **P1:** `scripts/healthcheck.py` never prints response bodies or secrets. Its sentinel tests stay.
- **P1:** changes to the tunnel, the bootstrap or the runbook keep T13 and T18 in `docs/threat-model.md` consistent.
- **P2:** `--test-backends` stays refused unless `DEVGUARD_TEST_BACKENDS=1`.
- **P2:** search results keep `untrusted: true`. Nothing in `app/rag/` executes, follows or templates retrieved text into instructions.
- **P2:** models load offline (`HF_HUB_OFFLINE=1`) everywhere except `scripts/fetch_models.py`. The model revision stays pinned by full commit SHA. `truststore` is used only in the fetch script, and TLS verification is never turned off (`verify=False`).
- **P2:** ingestion reads only manifest `local_path` files. No URL, shell or arbitrary path input.
- **P3:** the reranker revision stays pinned by full SHA; `fetch_models.py` fetches it as `model.safetensors` only (no `*.bin` or pickle pattern); it loads offline, with no `trust_remote_code`.
- **P4:** every MCP tool is registered through `GuardedTool` (`app/mcp_server/server.py`), never `@server.tool`, so identity, role and the strict argument model run before the handler. Argument models keep `extra="forbid"`, strict types, length bounds, and enums or anchored patterns for identifiers. No tool takes a URL, SQL, shell or path argument, and no value from an argument is joined into a path.
- **P4:** tool errors are the §18 envelope only: no exception text, stack trace, environment value or submitted value. The call log is one line per call (tool, sub, role, outcome, correlation ID, duration) and never logs arguments or results.
- **P4:** bearer tokens are HS256 only, with `exp`, `iat`, `sub`, `aud` and `iss` required and a TTL of at most 60 minutes. `MCP_AUTH_SECRET` is never printed, logged or committed. `scripts/mint_dev_token.py` prints the token to stdout only.
- **P4:** the MCP server binds `127.0.0.1` only, keeps DNS-rebinding protection on, and keeps the body limit (`configs/mcp.yaml`). Changing any of these needs a threat-model change (T1, T13).
- **P4:** test keys are generated at runtime and never tracked (`test_no_test_key_pem_or_derived_snap_table_tracked`). Signature results never carry a string-to-sign, key or signature.
- **P4:** all P4 tools are read-only. A write tool (`create_support_ticket`) arrives only with the P6 approval and idempotency flow, never as a stub that writes.
- **P5:** `sanitize_input` runs before anything is stored in state or sent to the model. A new state field, prompt input or log line holds only sanitized values. The secret-in-state test (`test_secrets_never_reach_state_llm_or_answer`) stays.
- **P5:** untrusted text (the user message and retrieved excerpts) goes only into the user turn's `<question>`/`<evidence>` blocks, with brackets neutralised. The system prompt never interpolates either.
- **P5:** `/api/v1` accepts only API-audience tokens. The agent mints its own short-lived MCP token per run and never forwards the API token. The API binds `127.0.0.1` only. `--fake-llm` stays refused without `DEVGUARD_TEST_BACKENDS=1`.
- **P5:** API errors are the §18 envelope (401, 404, 422, 500) with no submitted value or exception text. Request logs never include message text.
- **P5:** prohibited requests call no tool; high-risk requests call no tool and stay draft-only until the P6 approval path exists.
- **P6:** `create_support_ticket` is the only write tool, and `sandbox_tickets` its only target. It re-checks the approval under `SELECT ... FOR UPDATE` (exact action hash, caller, thread, unexpired, `approved`) and inserts under the unique `idempotency_key`. Any weakening of these checks, the unique constraint or the four-eyes table constraint is a blocker.
- **P6:** only `approver_roles` (`configs/security.yaml`) decide, never the requester. `execute_approved_action` reads the decision from the database, never from the resume payload.
- **P6:** every thread, approval and ticket lookup is owner-scoped: someone else's thread gets 404. `/api/v1/chat` stays one-shot and never creates an approval (`hitl=False`).
- **P6:** `Agent.run` sanitizes before `graph.ainvoke`, because LangGraph checkpoints raw input. The secrets dump (`test_crash_resume.py`) and the history walk (`test_agent_hitl.py`) stay.
- **P6:** PostgreSQL stays on `127.0.0.1`. No DSN, password or connection string is printed, logged or committed.
- **P7:** services connect as `devguard_app`, `devguard_ticket` or `devguard_audit`, never as the owner, and refuse to start without their role password. A new grant is justified against the role table in `docs/p7-mcp-hardening/spec.md` (Behaviour §4) and adds a may / may-not assertion to `test_db_roles.py`. Giving a runtime role the owner role, DDL, `TRUNCATE` or read access to the audit table is a blocker.
- **P7:** the audit table has no argument, message, payload or free-text column. Adding one is a blocker.
- **P7:** every outbound destination comes from configuration and passes `app/security/egress.py` at start-up, and HTTP clients keep redirects off. A tool argument that becomes a URL, host or path is a blocker.
- **P7:** both servers keep `proxy_headers=False` and a loopback host. Trusting forwarded headers needs the proxy design in `docs/p7-mcp-hardening/transport.md` and a threat-model change.
- **P7:** the body-size and pre-auth limits run before authentication and parsing. Raising a value in `limits.*` (`configs/security.yaml`) needs a stated reason.
- **P7:** a GitHub Action added for scanning or security is pinned by full commit SHA, not by tag (the `trivy-action` tags were force-pushed in 2026).
- **P9:** `alert-receiver` in `docker-compose.yml` keeps no `ports:`, `user: "65534:65534"`, `read_only`, `cap_drop: [ALL]`, `no-new-privileges` and a read-only script mount; the receiver keeps the signature check, its 600 s window and the 64 KiB cap. Langfuse's whitelist stays exactly the hostname `alert-receiver`. Adding `LANGFUSE_WEBHOOK_WHITELISTED_IPS` or `_IP_SEGMENTS` is a blocker (T20).
- **P9:** the UI shows API text only through `st.text`: never `st.markdown`, a widget label or `unsafe_allow_html`. The token never reaches page state, a URL or a log. `scripts/ui_server.py` keeps `127.0.0.1` and usage statistics off.
- **P9:** `GET /api/v1/alerts` and `GET /api/v1/approvals?status=pending` stay approver-only. A received alert never triggers an action, a tool call or an outbound request.
- **P8:** spans are opened only through the helpers in `app/observability/tracing.py`, which accept only `ALLOWED_ATTRS` with token-shaped values. Widening the allowlist, or putting message, answer, chunk, argument or memory text into a span, its input/output or a score comment, is a blocker. `mask_otel_spans` never catches its own errors (fail closed). The LangChain callback handler is never used for tracing.
- **P8:** preference reads stay scoped to `owner_id = sub`; a pattern becomes active only through a different approver (four-eyes CHECK); `devguard_app` keeps no `DELETE` on the memory tables. Secret- or personal-data-shaped pattern text is refused, never redacted.
- **P8:** fencing works from `injection_markers`. Narrowing that list needs a threat-model change and a fresh `scripts/scan_markers.py` count. Patterns stay in the untrusted block and never become sources.
- **P8:** `LANGFUSE_HOST` passes the egress check at start-up, and tests keep `DEVGUARD_TRACING=0`.

### Compliance
- Public ASPI/SNAP documents are `reference_only`: they are never committed, and any quoted excerpt is at most `citations.max_excerpt_chars.reference_only` (300) characters.
- Every `synthetic-` document keeps `(SYNTHETIC — not an official ASPI/BI document)` on its first line and in its manifest title. It uses no ASPI or Bank Indonesia logos, layout, or wording that suggests endorsement.
- Manifest permission evidence records who checked, when, and what was found. Versions, dates and URLs are never guessed; unknown values stay `null`.
- Nothing is added that connects to production systems or performs payment, refund, reversal, settlement or merchant-activation actions (PRD §1.4).
- **P1:** Langfuse stays self-hosted with telemetry off, and traces carry only the fixed synthetic probe. Adding a Langfuse Cloud host to `egress_allowlist` needs a threat-model change.
- **P1:** values in the runbook's measured-values table are labelled as measured, and a deviation from the plan (such as the RTX A5000 instead of the RTX PRO 4000) is recorded as found, not presented as planned (CLAUDE.md: targets are never presented as results).
- **P2:** a PR touching `evals/`, `docs/`, `tests/fixtures/` or `knowledge_base/` pastes local output of `uv run pytest tests/unit/test_licence_hygiene.py`. It skips in CI, where the PDFs are absent, so CI green proves nothing here.
- **P2:** extracted-text caches (`knowledge_base/sources/.*.txt`) stay inside the git-ignored `knowledge_base/sources/`.
- **P2:** retrieval numbers in docs carry their date, dataset version and config, and are labelled measured, not targets.
- **P3:** reranking numbers in docs are labelled measured, with date, dataset version and config, and are stated against the gate whatever the result. The measured 0.800 Recall@5, against a 0.85 gate, is never reworded as a pass.
- **P3:** eval JSON reports hold IDs, ranks, scores and timings only, never chunk text.
- **P4:** the extracted SNAP response-code table (`knowledge_base/sources/derived/`) is never committed, and its descriptions are capped at 300 characters in answers. Committed schemas derived from a reference-only document encode field names and rules only, with a section citation and no copied prose.
- **P4:** `NOT_FOUND` never carries a meaning; suggestions are labelled `suggestion, not a match`. Synthetic tables, schemas and fixtures keep their SYNTHETIC label.
- **P4:** the PKCS#1 v1.5 / base64 assumption of `snap-asymmetric-access-token-v1` stays labelled in results and in `docs/p4-deterministic-mcp-tools/validator-report.md` until a source confirms it.
- **P4:** fixture and smoke-test numbers in docs are labelled measured, with date and fixture-set version.
- **P5:** live agent results are labelled measured with model, GPU, date and dataset version, and a failed flow is reported as failed. The exit criterion (4/4 flows) is not claimed while flow 1 fails live.
- **P5:** only sanitized, synthetic text is sent to the model on the RunPod pod, through the SSH tunnel; no new egress host is added.
- **P6:** tables and checkpoints hold sanitized synthetic text, IDs and hashes only (TRD §17). Ticket title and body that look like secrets are refused, not rewritten. Retention (30-day checkpoints, 90-day audit) is not yet enforced; P8 owns it.
- **P6:** crash/resume and approval results are labelled measured, with the process IDs and ticket counts. "No duplicate action" is claimed only from the recorded run.
- **P7:** `security-report.md` results are measured and dated. A PARTIAL §13.4 control stays PARTIAL until the missing evidence exists, and the live-injection echo finding (5/10) is reported as measured, never reworded.
- **P7:** audit rows hold pseudonymous synthetic `sub` values and IDs only. Retention is 90 days through the developer-run `scripts/purge_audit.py`; nothing deletes rows automatically.
- **P7:** every HIGH or CRITICAL scan finding (pip-audit, Trivy) is fixed by a version bump or listed in `security-report.md` with its code path, reachability and mitigation. An untriaged finding fails the dependency-hygiene control.
- **P9:** `docs/p9-demo-ui-alerts/demo-report.md` keeps AC #21 (a real Langfuse alert shown) NOT MET until a non-`synthetic-` alert ID, time and screenshot are recorded. A synthetic test alert is never presented as Langfuse evidence.
- **P8:** `observability-report.md` keeps AC #23 (live part) and AC #25 open until the live echo run and the alert screenshots exist. Deferred steps 10 and 11 are never ticked without evidence; echo rates are labelled measured, against P7's 5/10.
- **P8:** memory holds only allowed enum values and reviewed pattern text; Langfuse gets synthetic `sub` values only, and real identities would need a hash first.

## Severity levels

| Level | Examples | Action |
|---|---|---|
| **Blocker** | A licensed source, secret or key committed; approval or redaction weakened; a hook or deny rule removed; CI red | Blocks merge |
| **Major** | A new checker rule without tests; a synthetic document missing its label; an unquoted full date; `docs/index.md` or `tasks.md` out of step | Must be fixed before merge, or the PR states why it is deferred |
| **Minor** | Unclear wording, a weak test name, a missing docstring on a non-obvious rule | Comment; author's choice |
| **Info** | Suggestions and alternatives | Comment only |

## Exclusions

- `uv.lock` contents (only check that it changed together with `pyproject.toml`)
- The wording of `docs/QRIS_DevGuard_AI_PRD_TRD.md` (it is the source requirements document, not reviewed here)
- `.gitkeep` files and anything under `evals/reports/`
- Git LF/CRLF line-ending warnings
- Markdown table alignment and formatting-only changes
- Debt already accepted: tests import `scripts/` via `sys.path` (`docs/structure.md` § Deviations)
- **P1:** the numbers in `docs/runpod-deployment.md` § Measured values. The user reports them from live runs; check only that each is labelled with its source.
- **P1:** environment-variable names in `docker-compose.yml` copied from Langfuse's official compose file. Check only pins, ports and secrets.
- **P2:** the wording of queries in `evals/datasets/retrieval_golden.yaml`. Check only schema, IDs and review fields.
- **P2:** torch wheel and platform markers in `uv.lock`. Check only that torch resolves from the `pytorch-cpu` index.
- **P3:** the wording of `set: hard` queries (check schema, IDs and review fields only).
- **P3:** the latency values in `docs/p3-reranking-eval/rerank-report.md` depend on the machine. Check only that they are labelled measured.
- **P4:** synthetic values inside `knowledge_base/fixtures/payloads/*.yaml` and `signatures/cases.yaml`. Check only IDs, expected outcomes and that no real credential appears.
- **P4:** MCP SDK internals (`mcp==2.3.0` is pinned). Check only that tools go through `GuardedTool` and that the SDK pin moves together with a re-run of `tests/integration/test_mcp_server.py`.
- **P4:** smoke-test latencies in `validator-report.md` depend on the machine.
- **P5:** the wording of `compose-v1` and of model answers (flows check properties, not wording); the per-node latencies and token counts in `agent-flow-report.md` (machine- and GPU-dependent).
- **P5:** the keyword lists in `configs/agent.yaml`. Check only that a change keeps `test_agent_classify.py` green and adds a row for the new case.
- **P6:** LangGraph's own checkpoint tables and migrations (`langgraph-checkpoint-postgres` 3.2.0 is pinned). Check only that `scripts/db_migrate.py` still calls `setup()`.
- **P6:** the wording of the ticket title and body drafts (check only that they are built from sanitized text and pass the tool's checks).
- **P7:** the wording of attack phrases in `evals/datasets/injection_cases.yaml` and `tests/fixtures/injection/`. Check only that they are synthetic, keep the SYNTHETIC marker and never reach the approved corpus or Qdrant.
- **P7:** Trivy findings inside third-party images we don't build. Check only that each HIGH or CRITICAL finding is triaged in `security-report.md`.
- **P7:** the formatting of the generated block in `security-report.md`. Check only that it was regenerated by the script, not edited.
- **P9:** Streamlit layout and wording. Check only the plain-text rule and that no token appears.
- **P9:** the `pandas`, `pyarrow` and `altair` entries in `uv.lock` that arrive with the `ui` extra.
- **P8:** the wording of `compose-v2`, the preference note lines and the phrase patterns in `configs/agent.yaml` (check only that each phrase maps to an allowed key and value, with a `test_memory_policy.py` case).
- **P8:** Langfuse SDK internals (`langfuse>=5,<6`, locked). Check only that spans go through the `tracing.py` helpers.

## What a human must decide

- Whether to keep accepting the reference-only risk for ASPI documents (no published licence), and whether to re-review if terms appear.
- Changing the reference-only excerpt limit, or adding or removing a source.
- Whether a synthetic value could still be mistaken for an official one, for example a path or service code that matches the real SNAP one.
- When to make `agent-config-eval` blocking (`continue-on-error: false`), and when to enable branch protection on `main`.
- **Organisation-level controls this repo cannot configure:** OS-level sandboxing for agent sessions, outbound domain allowlisting, and credential isolation (where RunPod, Langfuse and GitHub credentials live). These are managed settings, owned outside this repo.
- **P8:** whether to keep the measured fencing false positive (the SOP line telling the reader to run a validator) or exempt read-only tool names from `tool_invocation`; and whether to allow one CI secret for an LLM judgement job (triage, changelog).
