# Agent flow and approval report: p6-checkpoint-hitl
*Author: Kharisma Wardhana. Stage: Test. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*

**These are measured results**, not targets (CLAUDE.md), from one live session on 2026-11-11.

The raw flow report is `evals/reports/agent-flows-live-2026-11-11T085049.json`. It is git-ignored and holds IDs, statuses, timings and token counts only.

## Setup (live)

- **Model:** Qwen3-8B-FP8 on vLLM 0.32.0 through `scripts/tunnel.sh`, with temperature 0, thinking off, JSON-schema output and prompt `compose-v1`.
- **GPU and cost:** not recorded for this session. The first attempt failed with "vLLM not reachable": the tunnel was open, but nothing was listening on the pod yet. It succeeded once vLLM was serving.
- **Local side:**
  - PostgreSQL 17, with `scripts/db_migrate.py` applied: `0001` plus the checkpoint tables.
  - Qdrant with the P2 collection.
  - `scripts/mcp_server.py` with the ticket store.
  - `scripts/api_server.py`, which started with "PostgreSQL checkpointer, approvals on".
- **Change since P5:** evidence ordering, the flow 1 fix (spec AC #12). Reranking stays off.

## Live scripted flows

`uv run python scripts/eval_agent.py --live --api-smoke`, run by the user:

```
FAIL  flow-1-invalid-rsa-signature         total 45147 ms  failed: sources_include
PASS  flow-2-bad-timestamp                 total 5917 ms
PASS  flow-3-unknown-response-code         total 5347 ms
PASS  flow-4a-version-not-in-corpus        total 825 ms
SKIP  flow-4b-two-versions-in-evidence     not run in live mode
API smoke: ready=200 {'mcp': 'up', 'llm': 'up'} chat=200 ['NOT_FOUND']
3/4 flows passed (live mode)
```

| Flow | Tools (outcome, ms) | Sources cited | Findings | LLM (attempts, ms, prompt/completion) | Total ms |
|---|---|---|---|---|---|
| 1 | search OK 39,279 · signature OK 292 | SNAP §2.1.6#2 only | 1 confirmed | 2, 5,488, 1,178 / 177 | 45,147 |
| 2 | search OK 463 · payload OK 86 | synthetic spec §1.3 | 3 confirmed | 1, 5,296, 810 / 356 | 5,917 |
| 3 | search OK 557 · lookup OK 51 | synthetic spec §2/§3 (5) | 1 unverified (rule 4) | 1, 4,684, 1,100 / 305 | 5,347 |
| 4a | search OK 802 | none (clarification) | 0 | not called | 825 |

**Flow 1 is still a FAIL, unchanged from P5.**
- **The ordering worked.** A local re-run of the same search showed that the evidence the model received now starts with the three SNAP §2.1.5 chunks: CIT-000007, CIT-000010, CIT-000011. Before them came §6.1, §2.1.6#2 and §2.1.6.
- **The model still chose §2.1.6#2,** the 5th passage. Its first JSON attempt failed the schema again, and the retry produced that answer.
- **Everything else in flow 1 held.** The validator verdict (`INVALID`/`SIG_WRONG_SEPARATOR`) and the confirmed finding are intact, and every cited ID resolves.
- **Conclusion:** evidence ordering alone does not make this model cite the endpoint section. See "Follow-up".
- **Timing:** flow 1's 39.3 s search is the cold BGE-M3 load on the MCP server's first search.

## Live approval demo (Journey D, the real model)

This ran through the API on a thread, with real tokens minted for three different users:

```
message status: awaiting_approval | approval: pending | evidence_ids: ['CIT-000008', 'VAL-0']
requester decides: 403 FORBIDDEN | approver decides: 200 | approval: executed | action: created
second decision: 409 | tickets for thread: 1 | ids: {"thread_id": "2ora3ycPR5TFyY5OKzoP8vOlwp0e35w9", "approval_id": "a8551332-7587-4f20-be80-6e25ef246d8f", "ticket_id": "6a690715-fd09-464f-9f9e-1e8f06a60257"}
```

- The escalation was diagnosed first, through search and the signature validator. The run then paused with one pending approval. Its draft cites one retrieved chunk plus the validator verdict.
- **Four-eyes held:** the requester got 403. A different `support_engineer` approved, which resumed the run. Exactly one ticket was created, and a second decision got 409.
- **Logs:** the API log had 4 request lines and the MCP log had 13 tool-call lines. Neither contained message text, payload values or tokens.

## Crash/resume (TRD §22.4): local, real processes, fake LLM

`tests/integration/test_crash_resume.py` ran against the local PostgreSQL on 2026-11-11:

```
CRASH-RESUME EVIDENCE {"thread_id": "F9TWXCpRBLwFkGnNDVj7Cve_T38rm3eA", "run_id": "4b6a17a6-9311-468f-8fd3-0d5194bb075f", "approval_id": "bef7199b-27f8-4f01-be6e-61dc3e4a2cdd", "ticket_id": "e7653a7d-5b55-4b9b-8351-bb1d3f31d2bb", "api_killed_pid": 1464, "api_restarted_pid": 43056, "tickets_for_key": 1, "second_decision": 409, "second_resume": 409}
```

- The API process was killed mid-approval, with no graceful shutdown, and a new process recovered the same run and approval from PostgreSQL. Approving then created exactly one ticket, and a repeat decision or resume created none.
- An injected crash between the ticket insert and the next checkpoint resumes to the same `ticket_id` (`test_agent_hitl.py`).
- **The P6 exit criterion is met locally:** "crash/resume test passes; no duplicate action". These database tests skip in CI, because CI has no PostgreSQL (intake decision).

## Finding fixed in this phase: raw input reached checkpoints

The step-7 secrets dump found the synthetic `client_secret` value in the checkpoint tables.

- **Cause:** LangGraph checkpoints the graph's raw input before any node runs, so the unsanitized message was persisted before `sanitize_input`. P5 had the same flaw in memory; its test checked only the final state.
- **Fix:** `Agent.run` now sanitizes before invoking the graph.
- **Proof:** the dump over every table and checkpoint now finds nothing. A new test walks the whole checkpoint history; it fails on the previous code and passes now.

## Follow-up (for the user's decision)

**Flow 1 must cite SNAP §2.1.5 live.** Evidence ordering is recorded as insufficient. Remaining options, in the recommended order:
1. **Attach sources deterministically.** When the endpoint's own section was retrieved, code adds it to the sources of the confirmed validator finding, the same way verdicts are assembled today. This is cheap, deterministic, and in line with "facts are assembled by code".
2. **Rerank on the GPU pod.**
3. **Not recommended:** relaxing the property to "§2.1.5 retrieved". That weakens the TRD check.
