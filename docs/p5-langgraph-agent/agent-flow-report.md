# Agent flow report: p5-langgraph-agent
*Author: Kharisma Wardhana. Stage: Test. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

**These are measured results**, not targets (CLAUDE.md). They come from one live session on 2026-11-10, plus the CI-mode runs.

The raw live report is `evals/reports/agent-flows-live-2026-11-10T152127.json`. It is git-ignored and holds IDs, statuses, timings and token counts, with no answer text.

## Setup (live)

- **Model:** Qwen3-8B-FP8 on vLLM 0.32.0 (`configs/model.yaml`), reached through `scripts/tunnel.sh`.
- **Generation settings:** temperature 0, thinking disabled, JSON-schema output, prompt `compose-v1`.
- **GPU:** RTX A6000 (RunPod), as reported by the user. **Session cost: not recorded.**
- **Local side:** Qdrant with the P2 collection, `scripts/mcp_server.py` (BGE-M3 on CPU, reranking off), and `scripts/api_server.py`.
- **How the flows ran:** the agent ran in-process against the real MCP server over HTTP with auth (plan step 8). The API was exercised separately with `--api-smoke`.
- **Command:** `uv run python scripts/eval_agent.py --live --api-smoke`, run by the user. Dataset `agent_flows.yaml` 0.1.0.

## Live results

```
FAIL  flow-1-invalid-rsa-signature         total 24564 ms  failed: sources_include
PASS  flow-2-bad-timestamp                 total 6812 ms
PASS  flow-3-unknown-response-code         total 6108 ms
PASS  flow-4a-version-not-in-corpus        total 444 ms
SKIP  flow-4b-two-versions-in-evidence     not run in live mode
API smoke: ready=200 {'mcp': 'up', 'llm': 'up'} chat=200 ['NOT_FOUND']
3/4 flows passed (live mode)
```

**Result against the P5 exit criterion ("four scripted flows pass"):** 3 of 4 live flows passed. Flow 1 failed on one property. All 5 flows pass in CI mode. The failure is recorded here as found, and was not re-run until it passed.

| Flow | Tools (outcome, ms) | Verdict | Findings | Sources cited | Policy rules fired | LLM (attempts, ms, prompt/completion tokens) |
|---|---|---|---|---|---|---|
| 1 | search OK 16,777 · `validate_test_signature` OK 128 | `INVALID` / `SIG_WRONG_SEPARATOR` | 1 confirmed | `snap-technical-security-standard` 1.0.2 §2.1.6#2 | none | 2, 7,592, 1,178 / 253 |
| 2 | search OK 473 · `validate_request_payload` OK 37 | `invalid`, `SCHEMA_FORMAT at /headers/X-TIMESTAMP` | 3 confirmed | `synthetic-qris-mpm-service-spec` 0.1.0 §1.3 | none | 1, 6,242, 810 / 356 |
| 3 | search OK 433 · `lookup_response_code` OK 36 | `NOT_FOUND` | 1 unverified | synthetic spec §2 and §3 response codes | 4 (fixed summary; finding forced to unverified) | 1, 5,588, 1,045 / 305 |
| 4a | search OK 421 | none (clarification: corpus has 1.0.2, 1.0.1 asked) | 0 | none | none | not called |

### Per-node latency (ms)

| Node | Flow 1 | Flow 2 | Flow 3 | Flow 4a |
|---|---|---|---|---|
| retrieve_evidence | 16,800 | 484 | 445 | 432 |
| execute_read_tools | 143 | 58 | 53 | — |
| compose_diagnosis | 7,593 | 6,242 | 5,588 | — |
| every other node | ≤ 3 | ≤ 1 | ≤ 1 | ≤ 1 |
| **total** | **24,564** | **6,812** | **6,108** | **444** |

## What the results show

- **Flow 1 failed on citation choice, not retrieval or tools.**
  - A local diagnostic search afterwards, with the same query and `api_family: snap` (no GPU), returned SNAP §2.1.5 three times among the six passages the model received: hybrid ranks 7, 10 and 11. Ranks 1–4 were synthetic documents dropped by the family filter.
  - The model cited only §2.1.6#2 (Asymmetric Signature). That section is relevant, but it is not the access-token section the flow requires (TRD §22.3: "retrieve correct security section").
  - The validator verdict, the confirmed finding, the contract and the citation checks all held.
- **First JSON attempt failed in flow 1.** The model's first output did not match the schema, and the one retry succeeded. The deterministic fallback was not needed in any flow.
- **Cold first search.** Flow 1's search took 16.8 s because it was the MCP server's first search, so BGE-M3 loaded on CPU then. Later searches took about 0.4–0.5 s. P4 measured 29.6 s for the same cold load.
- **Model time dominates warm runs.** In flows 2 and 3, compose is about 90% of the total.
- **Rule 4 worked live.** The model's prose was replaced by the fixed "not found; no meaning is given" summary.
- **Logs stayed clean.** The API log held one request line, and the MCP log held 10 tool-call lines. Neither contained message text, payload values or tokens.

## CI-mode results (fake LLM, MCP harness)

`uv run python scripts/eval_agent.py`: **5/5 flows passed** (1, 2, 3, 4a, 4b). The same suite runs on every push via `tests/integration/test_eval_agent_cli.py`. Flow 4b, two versions of one document shown apart, is proven only here; see the spec's flagged concerns.

## Follow-up for P6 (agreed with the user, 2026-11-10)

**Flow 1 must cite SNAP §2.1.5 live.** The failure stays recorded as is. P6 carries the fix and re-runs the live flows. The candidates, in the recommended order:
1. Rerank the search pool on the GPU pod, so §2.1.5 moves above §2.1.6#2. This is the real fix for ordering, and P3 showed CPU reranking is too slow at about 160 s per query.
2. Order the evidence block by the requested API family and the endpoint's own section before the model sees it.
3. Not recommended: relaxing the property to "§2.1.5 retrieved" instead of "cited". That would weaken the TRD check into something this run already passes.
