# Observability report: memory, tracing, scores, fencing, retention (P8)
*Author: Kharisma Wardhana. Stage: Test. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p8-memory-observability/spec.md*

Measured results only. Targets from the spec are never reported as results. Two steps were **deferred by the
developer on 2026-11-12** and have no evidence yet: the alerts (step 10, AC #25) and the live pod session
(step 11, the live part of AC #23). Both are marked *open* below.

## Status by acceptance criterion

| AC | What | Status | Evidence |
|---|---|---|---|
| 1 | Preference save → version 1, update → version 2 with one history row, same value → unchanged | met | `test_memory_store.py`, `test_api_memory.py` (db) |
| 2 | Key or value outside the list → 422 envelope, nothing written | met | `test_api_memory.py`. As built: code `INVALID_ARGUMENT`, the API's existing 422 code (plan.md step 3) |
| 3 | Chat phrase saves; default, unrecognised, secret and refusal reason codes | met | `test_agent_memory.py` (db), `test_memory_policy.py` |
| 4 | Delete stops the preference being applied; after retention neither row nor history exists | met | `test_api_memory.py`, `test_retention.py::test_deleted_preference_is_gone_with_its_history_after_retention` |
| 5 | Expired preference not applied or listed; retention removes it and keeps one a day inside expiry | met | `test_memory_store.py`, `test_retention.py` |
| 6 | Isolation: A's preference never listed for or applied to B (API and prompt); B's delete of A's key → 404 | met | `test_api_memory.py`, `test_agent_memory.py::test_one_users_preference_never_reaches_another_users_prompt` |
| 7 | Pattern on a foreign thread → 404; secret- or personal-data-shaped text → 422, nothing stored | met | `test_api_memory.py`, `test_memory_policy.py` |
| 8 | Developer/kb_maintainer decide → 403; proposer → 403 and the database rejects it directly; non-pending → 409 | met | `test_api_memory.py`, `test_memory_store.py` (four-eyes CHECK) |
| 9 | Active matching pattern in the untrusted block, not in `sources`; invented citation removed; inactive never shown | met | `test_agent_memory.py::test_active_pattern_is_an_untrusted_uncited_note`, `test_inactive_patterns_are_never_shown`, `test_memory_store.py` (expired, deleted) |
| 10 | Pattern delete blanks title and text in row and history; proposer/admin only | met | `test_memory_store.py`, `test_api_memory.py` |
| 11 | A preference never changes the tool calls | met | `test_agent_memory.py::test_a_preference_never_changes_tool_calls` |
| 12 | One trace per run: node spans, generation, MCP call spans, with run, session, model, prompt and corpus version, latency, usage | met (in-memory exporter) | `test_tracing_e2e.py::test_one_trace_with_node_generation_and_mcp_spans_on_both_sides` |
| 13 | Server `search_qris_spec` spans share the agent's trace ID | met (in-memory exporter) | same test: `retrieval.bm25_encode`, `retrieval.dense_embed`, `retrieval.fused_query` under `mcp_server.search_qris_spec` |
| 14 | No secret, message or answer text in any span, event or score through escalation, approval and resume | met | `test_tracing_e2e.py::test_no_secret_in_any_span_through_escalation_approval_and_resume` (db), `test_spans_never_carry_message_answer_or_secrets` |
| 15 | Mask hook raising → nothing from that batch exported | met | `test_observability.py::test_mask_hook_raising_drops_the_whole_batch` |
| 16 | Langfuse unreachable → chat still 200 within the timeout | met | `test_tracing_e2e.py::test_chat_returns_200_when_langfuse_never_answers` (blackholed loopback port; 3 requests, each under 10 s) |
| 17 | Host outside `egress_allowlist` → tracing off, one log line, nothing sent | met | `test_observability.py::test_host_outside_the_egress_allowlist_disables_tracing_without_a_client` |
| 18 | Score values match fixtures | met | `test_scores.py` (31 cases), `test_tracing_e2e.py` (scores on a real run) |
| 19 | Feedback: owner → recorded; non-owner → 404; other value → 422 | met | `test_api_memory.py`, `test_api_server.py` (subprocess) |
| 20 | `APPROVAL_*` / `AUDIT_UNAVAILABLE` / `INTERNAL_ERROR` → exactly one `incident_event`; crash → `api-error` trace with no text | met | `test_scores.py`, `test_tracing_e2e.py::test_an_approval_mismatch_gives_exactly_one_incident_event` (db), `test_an_api_crash_gives_an_api_error_trace_with_the_code_only` |
| 21 | Marker sentence replaced in the prompt; `sources` keep the original quote | met | `test_agent_fence.py::test_marker_sentence_is_replaced_in_the_prompt_and_kept_in_sources` |
| 22 | Marker sentence removed from the answer with one limitation; `answer_echo` recorded; no marker → no change | met | `test_agent_fence.py` |
| 23 | CI: the P7 injection suite passes all 10 gate checks. Live: echo rate after fencing next to P7's 5/10, and the pre-fence rate | **CI met; live open** | `test_injection.py` (10/10 gate, pre-fence echo recorded and false for the fake LLM). Live: step 11 deferred, nothing measured |
| 24 | Marker count over every retrievable chunk; P5/P6 flows pass with no fencing applied | met | Measured below; `eval_agent.py` CI mode 5/5 with zero fenced sentences |
| 25 | Both alerts exist and show ALERT after the controlled test, with screenshots | **open** | Step 10 deferred. `alerts.md` holds the intended settings; no alert was created and no test run |
| 26 | Retention: dry run counts only; real run deletes exactly the rows outside each period; keeps `awaiting_approval`; no FK error | met | `test_retention.py` (db), `test_purge_retention_cli.py` (subprocess) |
| 27 | `devguard_app` cannot delete memory or run the retention deletes; the owner can | met | `test_db_roles.py` (role matrix), `test_retention.py::test_the_app_role_cannot_purge_and_nothing_is_deleted`, `test_purge_retention_cli.py` |
| 28 | Full suite and local `-m db` pass | see Regression | |

## Memory

P8 tests by file (collected 2026-11-12): `test_memory_policy.py` 35, `test_memory_store.py` 13,
`test_api_memory.py` 21, `test_agent_memory.py` 9, plus the memory-route cases in `test_api_server.py`
(subprocess authn/authz). Memory is in PostgreSQL migration `0003`, applied to the local database on
2026-11-12 (`db_migrate.py`: "applied 1 (0003_long_term_memory)").

## Tracing and scores

- **Coverage.** `test_observability.py` 21 (the real Langfuse SDK 5.2.0 export path with an in-memory exporter),
  `test_tracing_e2e.py` 8, `test_scores.py` 31.
- **Mask check.** With the mask hook replaced by a no-op, the leak test fails: the test depends on the hook,
  not on the allowlist alone.
- **Local Langfuse, 2026-11-12.** Langfuse 4.76.0 answered `/api/public/health` with OK. The MCP server and the
  API (fake LLM) started with "tracing on" and served 5 runs. The first took 52.1 s (cold start: the MCP server
  loads the CPU embedding model on its first search) and the next four 1.2–1.6 s from the client.
  **Not verified:** that these traces appear in the Langfuse UI. That check belongs to step 10.
- **`answer_supported` is structural, not semantic** (spec Flagged concerns). It proves every finding and next
  step points at retrieved evidence or a validator result. It does not prove the cited text says what the
  claim says. On the harness corpus a SNAP signature question retrieves no evidence, so the fake model's next
  step cites nothing and the run scores `answer_supported=0`: the score works as designed.

## Fencing

- **False-positive scan** (`scripts/scan_markers.py`, local Qdrant, retrievable documents, 2026-11-12): **3
  sentences in 2 of 278 chunks**.

  | Document | Version | Chunks | Matched chunks | Sentences | Categories |
  |---|---|---|---|---|---|
  | `snap-data-technical-spec` | 1.0.2 | 213 | 0 | 0 | none |
  | `snap-technical-security-standard` | 1.0.2 | 37 | 1 | 2 | `secret_variable`: 2 |
  | `synthetic-qris-mpm-service-spec` | 0.1.0 | 18 | 0 | 0 | none |
  | `synthetic-qris-troubleshooting-sop` | 0.1.0 | 10 | 1 | 1 | `tool_invocation`: 1 |

- **Known false positive.** The SOP line "Run `validate_test_signature` with the fixture ID." is removed from the
  prompt when retrieved. The developer kept the marker as is (2026-11-12). The `sources` quote is never
  altered, so a reader still sees the original.
- **On the CI harness corpus:** 1 sentence (the same SOP line). With the P7 planted sections added: 4 sentences
  (3 of the 5 planted sections match a marker; the other 2, an invented code meaning and a "signature is valid"
  claim, carry no marker category; those are handled by the validators and `policy_check`).
- **Not held out.** The marker list was written by someone who had seen the P7 attack phrases. It holds generic
  categories, not the canaries, but the 10 cases are not a held-out set (spec Flagged concerns; a held-out set
  is P9+ work).
- **Matcher fixes found by tests.** Hard-wrapped excerpts split one sentence over two lines, which let a planted
  attack sentence pass unmatched; sentences now split only at end punctuation or a blank line. Two markers were
  widened, still as generic categories: possessives in `override`, and underscores in a `secret_variable` prefix.
- **Live echo rate after fencing: not measured** (step 11 deferred). The P7 baseline stays 5/10.

## Retention

- **Dry run on the local database, 2026-11-12:** 0 rows in every table. The oldest local data dates from P6,
  under 30 days old, so nothing is past its period yet. This proves the command runs as the owner on the real
  schema; the deletion behaviour itself is proven on seeded rows in `test_retention.py`.
- **Tests:** `test_retention.py` 5 (db), `test_purge_retention_cli.py` 3.
- **Langfuse retention** is not available on OSS (Enterprise licence). Traces hold no content by construction;
  removing them means removing the local Langfuse volumes.

## Regression

Full-suite results per step are in the step commit messages. The run for this report is pasted in the step 12
commit and in Phase 4.
