# Intent: A LangGraph agent turns a developer's sanitized question into a cited, contract-shaped diagnosis, calling the P4 tools for every validation claim
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

## Problem statement

P1–P4 left every part the agent needs: the model on vLLM, hybrid search with citations, a reranker, and four deterministic MCP tools behind auth. But nothing joins them into a diagnosis, so the PRD's P5 exit criterion, *four scripted flows pass*, isn't met:

- **No agent exists.** A developer can search the docs or call a validator by hand, but nothing takes a question such as "my access-token signature is rejected", decides what to check, runs the right tool, and explains the result with citations (PRD Journeys A–C).
- **The invariants have no enforcer.** CLAUDE.md requires that:
  - a validation statement follows a deterministic tool call;
  - every material doc claim cites a chunk retrieved in that run;
  - missing evidence leads to abstention;
  - conflicting versions are shown, not merged.

  Today nothing applies these rules to generated text.
- **No structured answer exists.** PRD §5 defines a six-part response contract: summary, findings (confirmed/likely/unverified), validation results, next steps, sources, and confidence/limitations. No code produces or checks it.
- **No entry point exists.** TRD §18 defines `/api/v1` with the error envelope. There is no API yet.

## Proposed outcome

- **A LangGraph agent** with the TRD §11.1 `DevGuardState` and the read-only part of the §11.2 node sequence:

  `sanitize_input → classify_intent → classify_risk → retrieve_evidence → rerank_evidence → validate_evidence → select_tools → execute_read_tools → compose_diagnosis → policy_check → finalize`

  It routes by the §11.3 rules:
  - a validation question calls its validator before any validation statement;
  - missing or ambiguous evidence leads to an abstention or a clarification question;
  - high-risk, write or malformed requests never reach a tool.
- **Tools through the MCP server.** The agent is a real MCP client with a bearer token carrying the user's role, so P4's auth, role matrix and strict arguments apply to every agent call.
- **Reranking switchable, fused order by default.** `rerank_evidence` keeps the fused order and marks the evidence as not reranked (lower confidence). Config can turn the reranker on later.
- **Answers that follow the PRD §5 contract**, checked by `policy_check`:
  - every source resolves to a chunk retrieved in that run;
  - no "valid signature" claim without a `VALID` validator result;
  - no response-code meaning without a `FOUND` lookup;
  - secret-shaped text removed;
  - otherwise it abstains.
- **A minimal API.** FastAPI with `POST /api/v1/chat`, `/health/live` and `/health/ready`, and the §18 error envelope.
- **The four TRD §22.3 scripted flows:**
  1. invalid RSA signature;
  2. wrong timestamp or payload field;
  3. unknown response code;
  4. old or conflicting spec version.

  They pass in CI with a deterministic fake LLM, and once against Qwen3-8B on the RunPod pod. The live results are recorded as measured.

## Affected users and systems

- Kharisma Wardhana: reviews the flows and the answer contract, and runs the one live pod session.
- `app/agent/` (new): state, nodes, routing, the MCP client, LLM client, prompts and policy check.
- `app/api/` (new): the FastAPI app, the `/api/v1/chat` route, health routes and the error envelope.
- `app/mcp_server/`, `app/validators/`, `app/rag/`: called and reused, ideally unchanged.
- `configs/`: agent settings (model, temperature, token limits, timeouts, the rerank switch, the retrieval `top_k`) and API settings.
- vLLM on RunPod (`configs/model.yaml`, `scripts/tunnel.sh`): used for one live session.
- `pyproject.toml` / `uv.lock`: LangGraph, FastAPI and an OpenAI-compatible client, pinned exactly.
- `evals/`: the four scripted flows and a recorded live-run report.
- CI: must pass with no pod, no Qdrant, no models and no PDFs.
- Downstream:
  - P6 adds the PostgreSQL checkpointer, the approval interrupt, `create_support_ticket` and the threads/approvals API;
  - P7 adds hardening;
  - P8 adds memory and Langfuse.

## Constraints

- **Deadline:** P5 is done by 2026-11-12. All of P1–P9 must be done by 2026-11-20.
- **GPU spend:** one short pod session for the live run. The P4 intent recorded $19.57 left for P5–P9. The pod is terminated after the session (T18).
- **The LLM never decides validity.** It only explains validator output. Unknown codes never get a meaning, and `INCONCLUSIVE` is never turned into a verdict.
- **No secrets in state.** `sanitize_input` redacts the `configs/security.yaml` fields and patterns before anything is stored in state or sent to the model. State holds sanitized values, fixture IDs and hashes only.
- **Retrieved text and user input are untrusted.** They never reach the system or instruction slot and never expand tool authority. Tools are chosen from an allowlist per intent, never named by the model.
- **Read-only.** No write tool and no side effect in P5. A request to create a ticket gets a draft-only answer, and P6 adds the approval path.
- **Synthetic data only**, and `sandbox` only.
- **CI:** tests use a deterministic fake LLM and in-process tools. No pod, model download or service is needed.
- **Config, not code:** model ID, temperature, token budgets, timeouts and retrieval parameters live in `configs/*.yaml`.
- **Must not break:** P2–P4 behaviour and all existing tests.

## Open questions

- Which LangGraph version, and does P5 compile the graph with an in-memory checkpointer, leaving PostgreSQL to P6? How do the 14 TRD nodes map onto P5? Which nodes are real, which are pass-through, and which (approval, action, memory) are deferred?
- How are intent and risk classified: deterministic rules (keywords, presence of a code, payload or fixture ID), the LLM, or rules first with an LLM fallback? What makes a request "high risk" or "malformed" here?
- How does the agent get the tool arguments: endpoint ID, spec version, fixture ID, test case reference? Does the user supply them as structured fields in the chat request, or does the agent extract them? If extracted, how does that stay deterministic?
- What exactly does the model get and return? Is the output JSON matching the §5 contract, and how are retrieved chunks passed so they stay data, not instructions? What happens when the model's JSON is malformed?
- How does `policy_check` check claims mechanically? Which claims count as "material", and how is a sentence tied to a citation ID?
- How is flow 4 (old or conflicting spec version) produced, when the corpus has one version per document? The P2 synthetic conflict fixtures exist under `tests/fixtures/rag/`. Is a second document version ingested, or is the conflict shown from fixtures only?
- How does the agent authenticate to the MCP server: a token minted per request for the API caller's identity, or a service token? How does the API know the caller's identity and role before P7's full auth?
- What does the live pod run measure? Pass/fail per flow, latency, token counts? Where is it recorded, and what counts as a pass when the real model's wording varies?
