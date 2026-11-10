# Plan: A LangGraph agent turns a developer's sanitized question into a cited, contract-shaped diagnosis, calling the P4 tools for every validation claim
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*
*Implements: docs/p5-langgraph-agent/spec.md*

## Affected files

New: application code
- `app/agent/__init__.py`: package.
- `app/agent/config.py`: loads `configs/agent.yaml`. Keyword lists, regexes, LLM, timeouts, retrieval and API settings.
- `app/agent/state.py`: `DevGuardState` (TRD §11.1 plus the spec's extra fields) and the answer and finding types.
- `app/agent/sanitize.py`: redaction of `configs/security.yaml` fields and patterns in the message and context. Keeps the auth scheme; detects key material.
- `app/agent/classify.py`: deterministic intent and risk rules, plus code and version extraction.
- `app/agent/tools.py`: MCP client over Streamable HTTP. It mints a per-run MCP token, retries once on transient failures, and maps error envelopes to limitations.
- `app/agent/llm.py`: OpenAI-compatible client for vLLM (JSON schema output, `enable_thinking: false`, usage capture) and `FakeLLM` (deterministic; scriptable outputs for policy tests; records its input).
- `app/agent/prompts.py`: the `compose-v1` system prompt, the untrusted `<question>`/`<evidence>` user turn, and the output JSON schema.
- `app/agent/answer.py`: assembles `validation_results`, `sources` and `confidence`, and builds the deterministic fallback, refusal, draft-only, clarification and abstention answers.
- `app/agent/policy.py`: `policy_check` rules 1–8 and `policy_actions`.
- `app/agent/graph.py`: the 12 nodes, routing, `InMemorySaver`, `run_id`/`thread_id`, and the trace (timings, tokens).
- `app/agent/harness.py`: CI-mode wiring. It builds an in-memory corpus (committed synthetic docs plus the P2 conflict fixtures, `FakeEmbedder`) and generates test keys in a temp directory. It then serves the real P4 MCP app with that context on a free loopback port in a background thread, using `FakeLLM`. Used by tests and `scripts/eval_agent.py` (CI mode) only.
- `app/api/__init__.py`: package.
- `app/api/app.py`: FastAPI app with `POST /api/v1/chat`, `/health/live`, `/health/ready`, the §18 envelope handlers (including 422), bearer auth with the API audience, and one log line per request.
- `app/api/models.py`: strict request and response models (message, context blocks, answer).

New: scripts
- `scripts/api_server.py`: runs the API on `127.0.0.1:8000` (`--fake-llm` refused unless `DEVGUARD_TEST_BACKENDS=1`).
- `scripts/eval_agent.py`: runs `evals/datasets/agent_flows.yaml` in CI mode (default) or `--live`. Writes `evals/reports/agent-flows-<ts>.json`; exit 0 only if every flow passes.

New: config, data, docs
- `configs/agent.yaml`: LLM (model from `configs/model.yaml`, temperature 0, `max_tokens`, `enable_thinking: false`), timeouts, `retrieval.top_k: 6`, `rerank: false`, keyword lists, regexes, API host, port and audience, MCP token TTL.
- `evals/datasets/agent_flows.yaml`: flows 1, 2, 3, 4a and 4b with request, context and expected properties (CI and live variants where they differ).
- `docs/p5-langgraph-agent/agent-flow-report.md`: measured live-run results (step 9).

New: tests
- `tests/unit/test_agent_sanitize.py`
- `tests/unit/test_agent_classify.py`
- `tests/unit/test_agent_tools.py`: the MCP client against the harness server.
- `tests/unit/test_agent_llm.py`: schema, fallback, untrusted-block placement, fake LLM.
- `tests/unit/test_agent_policy.py`
- `tests/unit/test_agent_graph.py`: routing and the flows through the graph with the harness.
- `tests/unit/test_api.py`: FastAPI `TestClient` with the harness.
- `tests/integration/test_api_server.py`: `scripts/api_server.py` and `scripts/mcp_server.py` as subprocesses, fake LLM.
- `tests/integration/test_eval_agent_cli.py`: `scripts/eval_agent.py` as a subprocess (pass, and a wrong expectation exits 1).

Edited
- `pyproject.toml` / `uv.lock`: `langgraph==1.2.20`, `fastapi==0.162.2`, `openai==3.49.0`, pinned exactly.
- `app/mcp_server/auth.py`: `mint(..., audience=None)` and `HS256Verifier(secret, audience=None)`. Both default to the MCP audience, so P4 behaviour is unchanged.
- `scripts/mint_dev_token.py`: `--audience mcp|api`, default `mcp`.
- `tests/unit/test_mcp_auth.py`: regression tests. The default audience is unchanged; an API-audience token is refused by the MCP verifier and accepted by the API verifier.
- `tests/integration/test_mcp_server.py`: regression tests. `mint_dev_token.py` without `--audience` still mints an MCP token; `--audience api` mints one the MCP server rejects with 401.
- `docs/threat-model.md`: T3, T6, T7, T8 and T14 gain P5 evidence. T1 notes the API audience.
- `docs/tech.md`, `docs/structure.md`, `README.md`: new packages, commands and config.
- `docs/index.md`: links the plan, tasks and report.
- `CLAUDE.md`: see Agent configuration changes.

Not changed: `app/mcp_server/server.py`, `tools.py`, `models.py`; `app/validators/*`; `app/rag/*`; `configs/security.yaml`, `configs/mcp.yaml`, `configs/retrieval.yaml`, `configs/model.yaml`; `.claude/*`; `.github/*`.

## Work sequence

1. **Dependencies and config.** Pin the three packages and add `configs/agent.yaml`, `app/agent/config.py`, `app/agent/state.py` and the package `__init__`s. (spec AC #18)
2. **Sanitize and classify.** Write `sanitize.py` and `classify.py`. (AC #3, #4, #5)
3. **Audience-aware tokens and the MCP client.** Edit `auth.py` and `mint_dev_token.py`, write `harness.py` (MCP server in a thread, in-memory corpus, keys) and `app/agent/tools.py`. (AC #7)
4. **LLM client, fake LLM and prompts.** Write `llm.py` and `prompts.py`. (AC #9, #10)
5. **Answer assembly and policy check.** Write `answer.py` and `policy.py`. (AC #2, #8)
6. **Graph.** Write `graph.py`: nodes, routing, checkpointer, trace. (AC #5, #6, #11, #12, #13, #14 in CI mode)
7. **API.** Write `app/api/*` and `scripts/api_server.py`. (AC #1, #2, #17, #18)
8. **Scripted flows and the eval CLI.** Write `agent_flows.yaml` and `scripts/eval_agent.py`. (AC #11–#15)
9. **Live run and report.**
   - **You:** start the RunPod pod and the SSH tunnel (`docs/runpod-deployment.md`); this is the GPU spend.
   - **Me:** start local Qdrant, the MCP server and the API; run `scripts/eval_agent.py --live`; write `agent-flow-report.md`.
   - **You:** terminate the pod when the run ends. (AC #16)
10. **Threat model and steering docs.** Update `threat-model.md`, `tech.md`, `structure.md` and `README.md`. (spec Flagged concerns)
11. **`CLAUDE.md` merge.** Shown as a diff, written only after `approve write`. (Agent configuration)

Steps 2, 3 and 4 have no dependency on each other and share no file, so they could run in parallel worktrees. With a two-day deadline and small modules, I'll run them sequentially in this session unless you ask for worktrees.

## Test coverage

Matched rows from `test-scope-by-stage.md`:
- **backend service / script entry point:** integration tests at the entry points;
- **roles/permissions:** authentication and authorization integration tests;
- **existing project:** regression tests;
- **always:** the agent-config eval.

Not matched: UI/e2e (the API returns JSON, with no screen), database (P5 persists nothing; the PostgreSQL checkpointer is P6) and Qdrant config (unchanged).

- **Step 1:** unit tests check that the config loads, the API binds loopback, `rerank` is `false`, temperature is 0, and thinking is disabled.
- **Step 2:** unit tests are table-driven: every redact field and pattern in message, headers and nested body; the auth scheme kept; a PEM block detected; every intent and risk rule, including Indonesian keywords, ambiguous read+write, and prohibited actions; code and version regexes.
- **Step 3:**
  - **Regression:** `mint`/`HS256Verifier` default to the MCP audience; `mint_dev_token.py` without `--audience` behaves as in P4.
  - **Unit:** the MCP client against the harness server calls each tool; the token it sends is MCP-audience, 5 minutes, for the caller's `sub` and `role`; `FORBIDDEN` and `SEARCH_UNAVAILABLE` become limitations; one retry on a refused connection, none on an envelope.
  - **Auth integration:** `--audience api` minted by the real script is refused (401) by the real MCP server subprocess.
- **Step 4:** unit tests cover the JSON schema accepted and refused, one retry then the fallback, untrusted blocks only in the user turn, the injection text not reaching the system prompt, and `FakeLLM` recording its input.
- **Step 5:** unit tests give each policy rule 1–8 a breaking `FakeLLM` output, which is corrected or abstains. They also check that sources and verdicts come from tool output, never model text, and that all §5 sections are present.
- **Step 6:** unit tests drive the graph with the harness:
  - prohibited and high-risk requests call no tool;
  - a missing argument gets a clarification;
  - each intent calls only its validator;
  - a `kb_maintainer` signature question gets `FORBIDDEN` turned into a limitation;
  - flows 1, 2, 3, 4a and 4b hold their properties;
  - no secret appears in the checkpointer state.
- **Step 7:**
  - **Unit (`TestClient`):** 422 envelope with no echoed value; answer shape; `run_id`/`thread_id` unique; `/health/ready` gives 503 when a dependency is down.
  - **Auth integration (`test_api_server.py`):** with the real API and MCP subprocesses, no token, an expired token, an MCP-audience token and a foreign secret each get 401. A `developer` token gets an answer. A `kb_maintainer` signature question gets a limitation, not a verdict.
  - **Entry point:** the API binds loopback only; `--fake-llm` is refused without `DEVGUARD_TEST_BACKENDS=1`.
- **Step 8:** **integration:** `scripts/eval_agent.py` as a subprocess exits 0 with every flow passing and writes its report. It exits 1 on a temp dataset with one wrong expectation.
- **Step 9:** no new test type. It runs the step 8 CLI with `--live`, and the output is pasted into the report.
- **Step 10:** the existing `test_repo_hygiene.py` and `test_licence_hygiene.py`.
- **Step 11:** the agent-config eval (`.claude/hooks/eval.sh`, the existing `agent-config-eval` job) in Phase 4.
- **Every step:** the full `uv run pytest` is gated on its own exit code (`set -o pipefail`). Existing tests are not edited except the two regression files named above.

## Agent configuration changes

- **`CLAUDE.md` (step 11, its own diff and `approve write`):**
  - `## Commands` gains `api_server.py`, `eval_agent.py` (CI and `--live`) and `mint_dev_token.py --audience api`.
  - `## Test scope` gains rows only if this spec matched one not already listed. The roles/permissions row from P4 already covers the API, so probably none.
  - `## Common mistakes` gains only lines learned during the build (append-only).
  - `## Project status` gets its application-code line extended with `app/agent/` and `app/api/`.
- **Subagents:** none.
- **Hooks:** none new. `deploy-gate.sh` already asks on `tunnel.sh`, which step 9 needs; that prompt is expected.
- **CI:** no change to workflows. The new tests run in the existing `test` job with the fake LLM and in-process tools. `agent-config-eval` is verified present.

## Rollback

Revert the PR. P5 persists nothing and changes no schema, Qdrant collection or P4 tool behaviour. The only shared-code edit is the `audience` parameter, which defaults to P4's value. Stop `scripts/api_server.py` if it is running.
