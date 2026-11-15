# Spec: One release-gate run decides PASS/FAIL on every P0 gate, and a first-time cloner can set up, run, evaluate and tear down the system from the README and one setup script
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-15. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-final-evaluation-packaging/intent.md*

## Behaviour

### 1. Setup script (first clone to running system)

A new bash script runs the local setup in this order and prints one line per step (`[n/N] name … ok | skipped (reason) | FAILED`):

1. Prerequisite check: `git`, `uv`, `podman` (with `podman compose`), `bash`, and the Python version from `pyproject.toml`. A missing tool stops the script with what to install.
2. `.env`: create it with `init_env.py`, or add missing keys with `init_env.py --add-missing`. It never overwrites a value and never prints one.
3. `uv sync` with the `embed` and `ui` extras.
4. `podman compose up -d`, then wait for the services to be ready, with a bounded timeout.
5. Database migrations (`db_migrate.py`).
6. Test keys (`gen_test_keys.py`), skipped when the keys already exist.
7. Reference PDFs: for each `approved` `reference_only` manifest entry, check that the file is at its `local_path` and that its checksum matches the manifest.
   - **Missing:** stop and print the portal page (`source_url`), the expected file name and where to put it. Nothing is downloaded.
   - **Checksum mismatch:** stop and name the entry. A different version would silently change the corpus.
8. Response-code extract (`extract_response_codes.py`), skipped when the derived file is current.
9. Embedding and reranker models (`fetch_models.py`), skipped when cached.
10. Ingest (`ingest.py`); unchanged sources are skipped by its own checksum and config rule.
11. Source manifest check (`check_sources.py`).
12. SSH tunnel: started with `scripts/tunnel.sh` only when `RUNPOD_SSH_HOST`, `RUNPOD_SSH_PORT` and `RUNPOD_SSH_KEY_PATH` are set in `.env`. Otherwise the step is `skipped (no pod configured)`. The script never creates, starts or stops a pod.
13. Health check (`healthcheck.py`). Without a tunnel, vLLM reads FAIL and the script says that is expected until a pod is up.

Rules:

- **Idempotent.** A second run on a set-up machine changes nothing and only reports `skipped` and `ok`.
- **Stops at the first failure,** with the step name, the reason and the next action, and exits non-zero.
- **Flags:**
  - `--quick` skips steps 3 (`embed` extra), 9 and 10, for tests and the API only.
  - `--synthetic-only` skips steps 7 and 8 and ingests only `team_authored` sources. It prints, and the health check line records, that this mode does not reproduce the measured results.
  - `--help` lists the steps and flags.
- **No secrets printed,** the same as `init_env.py`. It never reads a credential beyond passing `.env` names to the existing scripts.

### 2. Live evaluation (one pod session)

All live work happens in one pod session that the developer starts and stops, in a fixed order the README lists:

1. Health check 7/7 through the tunnel.
2. The four scripted agent flows, live (`eval_agent.py --live`).
3. **Golden-answer run.** The 100 golden questions go through the real agent (real MCP server, Qdrant and vLLM, as `eval_agent.py --live` does). Each answer, its §5 sections, citations, validator results and the cited excerpts (capped at 300 characters each) are written to a dated answers file. Deterministic checks are computed at this point and need no judge:
   - **citation validity:** every cited chunk ID was retrieved in that run.
   - **fabricated crypto guidance:** no answer calls a signature or payload valid unless a validator returned VALID, and no unknown response code gets a meaning.
   - **secret scan:** the answers file contains no value matching the redaction rules.
4. The P7 injection cases on the live model (`eval_security.py --live-injection`), which also gives P8 AC #23's live echo rate.
5. **Benchmark.** The developer runs `vllm bench serve` inside the pod shell with the fixed settings (random dataset, 1024 input / 256 output tokens, 100 prompts, fixed seed; concurrency 1 for the gate baseline, plus an informational concurrency-4 row). They copy the result JSON to `evals/reports/`. A parser turns it into the benchmark report with p50/p95 TTFT and TPOT, and adds the hardware and model identity (GPU SKU, driver, CUDA, vLLM version, model and revision, quantization, max context length). The tunnel TTFT from the health-check JSONL is shown next to it, informational only.
6. **Controlled alert test (Langfuse).** It drives `devguard-agent-p95-latency-high` with the live traffic above and `devguard-incident-event` with its documented trigger. Both must reach ALERT, and one real (non-synthetic) delivery must reach `alert-receiver` and show on the UI's Alerts page. This closes P8 AC #25 and P9 AC #21.

### 3. Judge (after the pod session, no pod needed)

A judge command reads the answers file and grades every item with **Claude Opus 5.5** (`claude-opus-5-5`) through the Anthropic Message Batches API.

- **Credential:** the developer runs it with their own credential from the environment. The command never prints it or writes it anywhere.
- **What it sends per item:** the synthetic question, the item's labelled expected facts and supporting sections, the agent's answer, and each cited excerpt capped at 300 characters, after the existing redaction pass. Nothing else.
- **What it gets back:** a structured verdict: `supported` (yes/no), per-citation `supports_claim` (yes/no), and a short reason (≤ 300 characters, instructed not to quote excerpts).
- **NOT JUDGED:** a refusal, an error, an expired batch request or an unparseable verdict counts as NOT JUDGED, never as supported. NOT JUDGED items count against both metrics.
- **No model fallback.** A verdict must come from the named model, and the Batches API does not accept `fallbacks` anyway.
- **Recorded with each run:** the model ID, effort, prompt version and batch ID.
- **Resumable:** a re-run with the same batch ID collects results without re-submitting.
- **Calibration:** the developer hand-grades a 20-item sample (fixed seed). The judge report states how often the developer and the judge agreed (percent agreement and Cohen's kappa). This is reported, not gated.
- **CI never calls Anthropic:** in CI the judge runs against a fake client with fixed verdicts.

Metrics:

- `golden_answer_supported` = supported answers ÷ 100. An abstain/NOT_FOUND item counts as supported only when the agent abstained or said NOT_FOUND.
- `citation_support_precision` = citations judged `supports_claim: yes` ÷ all citations judged.

**Amendment (2026-11-15, developer decision): the judge backend.** The judge runs as **Claude Code subagents on Claude Opus 5.5** in the developer's session, not through the Message Batches API:

- Each subagent starts with a fresh context, so it doesn't see the session that drafted the golden items and read the live answers.
- Each one receives exactly the per-item payloads `build_payload()` produces, plus the versioned rubric (`judge-v1`) and the verdict schema. Nothing else.
- About 25 items go to each subagent, and the verdicts are written to one file.
- `judge_answers.py --verdicts-file` reads that file through the same parser, NOT JUDGED rules, metrics and calibration code.
- The report records the backend (`claude-code-subagent`), the model, the prompt version, and the verdict file's name and SHA-256 in place of a batch ID.
- The Batches backend stays in the code, unused. No API key is used.

### 4. Release gate (local command)

A local command reads `configs/eval_thresholds.yaml` and, for each of the 11 gates and 4 critical gates, the newest matching evidence in `evals/reports/`. It runs no eval itself. For each gate it prints and writes:

- status: `PASS`, `FAIL`, `NOT RUN` or `STALE`
- measured value, threshold, evidence file path, SHA-256, run date, dataset version and mode

Rules:

- **No evidence** → NOT RUN. **Older than 14 days**, or a dataset version that doesn't match the dataset file → STALE. NOT RUN and STALE are not PASS.
- **Live-only gates** accept only live evidence: `golden_answer_supported`, `citation_support_precision`, `citation_validity`, `agent_scenarios_passed` and `langfuse_alert_fired`. Deterministic gates accept CI or local evidence: `validator_fixture_accuracy`, `crash_resume`, `hitl_approval_enforced` and the critical gates.
- **Retrieval gates** use the **all-50-query** values: `before_after.all.candidate_recall@20` and `reranked_recall@5`.
- **Overall verdict:** PASS only if every gate is PASS. Any critical gate that isn't PASS is an automatic FAIL. The verdict line names each gate that isn't PASS.
- **Benchmark:** TTFT/TPOT appear in a separate "reported, not gated" block against the PRD §22 targets, because `eval_thresholds.yaml` has no latency gate. Thresholds are not edited.
- **Output:** a dated JSON report in `evals/reports/` (git-ignored, like every report there), and a committed Markdown release report in this feature folder that carries the SHA-256 of every evidence file it used.
- **Exit codes:** `0` on overall PASS, `1` on FAIL / NOT RUN / STALE, `2` on a usage or read error.

### 5. Packaging

- **README rewritten as the first-clone guide:**
  - what it is, and its status (each phase with its measured outcome, no targets shown as results)
  - prerequisites
  - the setup script, including the PDF placement step
  - RunPod pod creation (from `docs/runpod-deployment.md`, linked rather than duplicated where it is long)
  - starting the servers and the UI
  - the live-session order (§2), the judge, the release gate and the benchmark
  - expected health-check output
  - teardown: stop the pod, then `podman compose down` without removing volumes, plus how to remove volumes deliberately
  - the TRD §26.3 reproducibility items, and a "which env vars are secrets" table
- **Deployment manifest:** a committed file with the TRD §26.2 step 15 fields. A script fills in what is readable locally (compose image digests, the `uv.lock` hash, and the model, revision, quantization and context length from `configs/model.yaml`). The developer pastes the pod-side values (GPU SKU, driver, CUDA, vLLM image digest) and records whether the fresh-clone and fresh-pod runs needed any step the README does not state.
- **Final report** in this feature folder, at most 5 printed pages:
  - use case
  - a Mermaid architecture diagram (TRD §8–10: UI → API → LangGraph → MCP tools → Qdrant/PostgreSQL/vLLM, with Langfuse and the alert receiver)
  - the measured numbers: TTFT/TPOT, retrieval before/after reranking, the 4 agent runs and crash/resume, the MCP attack results, the gate verdict with dataset versions, and the judge calibration
  - Langfuse dashboard and alert screenshots
  - limitations
- **Screenshots** as PNGs in `docs/p9-final-evaluation-packaging/screenshots/`, each checked against a written list before commit.
- **Addenda for earlier phases:** P8's observability report and `tasks.md`, and P9's demo report and `tasks.md`, each get one dated addendum line pointing to this feature's evidence for AC #23 (live), #25 and #21. Their recorded history is not rewritten.

## Architecture and the reason for it

- **The release gate reads reports and never runs evals.** Live evals cost pod time and the reranked eval takes hours on CPU. A judge that only reads is fast, deterministic and testable against fixture reports. This follows the existing "evidence is generated, not written" decision (`docs/structure.md`).
- **The golden run and the judge are two commands.** The pod session only produces answers. Judging can be redone, or recalibrated, without a pod, and the Anthropic credential never meets the pod or the servers.
- **The judge uses the Batches API.** It is offline work, about half the cost, and the batch ID makes it resumable. Fixed model ID, effort and prompt version live in `configs/`, not in code. *Amended 2026-11-15:* the backend actually used is fresh-context Claude Code subagents (Behaviour §3, Amendment), with the same payload, rubric and parser. Each subagent writes verdicts to a file whose SHA-256 is the record.
- **The judge is an eval tool, not part of the product.** It lives under `scripts/`, plus a small library module beside the existing eval code. Nothing in `app/` imports it, and the runtime egress allowlist (`configs/security.yaml`) is unchanged. The `anthropic` SDK goes into a new optional extra (`judge`), so the runtime, CI's `test` job and the container images never install it. **Departure from `docs/tech.md`:** a first external LLM API, and a new extra. It is recorded there and in the threat model as a new eval-time data flow (eval host → Anthropic API), not a runtime one.
- **The deterministic checks stay deterministic.** Citation validity, fabricated crypto guidance and the secret scan run in code on the answers file. The judge grades only answer support and citation support. This keeps the "LLM never decides crypto or payload validity" invariant.
- **The setup script is bash and only chains existing scripts.** That matches the user decision and `scripts/tunnel.sh`, and adds no second implementation of any step. It uses `set -u -o pipefail` with an explicit check after every step instead of `set -e`, so each failure gets its own message.
- **The benchmark parser is pure.** `vllm bench serve` runs in the pod by the developer. The repo only parses its JSON, which keeps the pod-side command reproducible from the README and the parser testable.
- **The crash/resume evidence comes from the existing test.** A small producer runs `tests/integration/test_crash_resume.py` (a `db` test) and records PASS, FAIL or NOT RUN (skipped) in a dated report. It follows `eval_security.py`'s rule that a skipped test is NOT RUN.
- **Critical and HITL gates map to existing security cases.** The mapping is fixed in config, and each gate names the case IDs that prove it:
  - `secret_leakage`: S13 and G-SECRETS, plus the answers-file secret scan
  - `unauthorized_side_effects`: S14
  - `approval_bypass`: S02, S11 and S12
  - `hitl_approval_enforced`: S11, S12 and S14
  - `fabricated_crypto_guidance`: the deterministic check on the golden answers and the live agent flows
- **Alert evidence is an export, not a claim.** A small exporter reads the receiver's stored alerts and writes the non-synthetic deliveries (ID, alert name, state, time) to a dated report. `langfuse_alert_fired` passes only on a non-synthetic delivery.
- **Retrieval is judged on all 50 queries** (user decision): the gate does not choose the easier subset.

## Data

- **Golden-answer dataset** (committed, synthetic, `dataset_version` 1.0.0). 100 items:
  - about 50 built on the 50 labelled retrieval-golden queries
  - about 30 new synthetic questions over the synthetic spec, the SOP, the response codes and validator scenarios using fixture IDs
  - about 20 that must abstain, say NOT_FOUND or refuse: unknown codes, payment or refund requests, production keys, out-of-scope questions

  Each item has: ID, question, optional context (fixture IDs only), expected behaviour (`answer`, `abstain`, `not_found`, `refuse`), expected key facts, supporting sections (`document_id` and section prefix), and reviewer and review date. The developer reviews every item.
- **Answers file** (git-ignored, under `evals/reports/`): the redacted answer, sections, citation IDs, validator results, cited excerpts ≤ 300 characters each, and the deterministic check results. Excerpts stay out of git, the same as the `reference_only` sources.
- **Judge verdicts** (git-ignored): per item and per citation, plus the model ID, effort, prompt version and batch ID. No excerpt text in the reasons.
- **Calibration labels** (committed): 20 item IDs with the developer's supported/unsupported grade.
- **Benchmark, crash/resume and alert export reports** (git-ignored, dated).
- **Release report JSON** (git-ignored) and **release report Markdown** (committed) with evidence SHA-256 values.
- **Deployment manifest** (committed): versions, digests and hashes only. No IPs, pod IDs, hostnames or secrets.
- **Screenshots** (committed PNGs, each ≤ 1 MB), reviewed before commit.
- **Retention:** reports follow the existing git-ignored practice. Nothing new goes into PostgreSQL, Qdrant or Langfuse beyond the traces the live runs already produce (P8 retention applies).
- **Sensitivity:** synthetic data only. The only third-party egress is the judge payload described in Behaviour §3.

## Acceptance criteria

**Setup script**

1. On a clean checkout, with a fake toolchain on `PATH` (integration test running the real script as a subprocess), the script runs the 13 steps in the stated order and exits 0. A second run reports every step `ok` or `skipped` and changes nothing.
2. When any one step's command fails, the script stops at that step, prints its name and the next action, exits non-zero, and runs no later step.
3. With a `reference_only` PDF missing, the script stops at step 7 and prints that entry's `source_url`, file name and `local_path`. With a wrong checksum it stops and names the entry. In neither case does it make a network request.
4. `--quick` skips steps 3 (`embed` extra), 9 and 10. `--synthetic-only` skips steps 7 and 8 and prints the "does not reproduce the measured results" line. `--help` lists every step and flag.
5. The tunnel step runs `scripts/tunnel.sh` only when all three `RUNPOD_SSH_*` values are set (not `set-me`). Otherwise it reports `skipped (no pod configured)`. The script contains no RunPod API or `runpodctl` call.
6. No value from `.env` appears in the script's output (sentinel secret test).

**Golden answers and judge**

7. The golden dataset has exactly 100 items that validate against its schema. Each `answer` item has at least one expected fact and one supporting section that exists in the approved, retrievable corpus. Every item has a reviewer and a review date.
8. The golden run writes one answers file record per item. Its citation-validity, fabricated-crypto-guidance and secret-scan checks are computed in code. Unit tests feed planted violations (an unretrieved citation ID, a "signature is valid" claim without a VALID validator result, a secret-shaped value) and each one is counted.
9. The judge payload for an item contains only the question, the expected facts and sections, the answer, and excerpts each ≤ 300 characters after redaction. A test with a planted secret and a 1,000-character excerpt proves both are removed or capped before the request is built.
10. With a fake client, a refusal, an error, an expired result and an unparseable verdict each count as NOT JUDGED and count against both metrics. The metrics match hand-computed values on a 6-item fixture.
11. The judge report records the model ID `claude-opus-5-5` (from `configs/`), effort, prompt version and batch ID. Re-running with that batch ID collects results without submitting a new batch. *Amended 2026-11-15:* for the subagent backend, the report records `backend: claude-code-subagent` and the verdicts file name and SHA-256 in place of the batch ID. A verdict missing from the file, or one that fails the schema, is NOT JUDGED.
12. Calibration: with a 20-item label file and fake verdicts, the report shows percent agreement and Cohen's kappa matching hand-computed values.
13. Live: the golden run and the judge run once over all 100 items. The measured `golden_answer_supported`, `citation_support_precision` and `citation_validity`, and the calibration agreement, are recorded in the release report. *(Measured, not targeted.)*

**Benchmark**

14. The parser turns a fixture `vllm bench serve` result JSON into p50/p95 TTFT and TPOT, and fails clearly on a missing field. The benchmark report names the GPU SKU, driver, CUDA, vLLM version, model and revision, quantization, max context length and the bench settings.
15. Live: one concurrency-1 run and one concurrency-4 run are recorded. The report shows them against the PRD §22 targets as "reported, not gated", with the A5000 FP8-fallback note if that GPU was used.

**Release gate**

16. Against fixture reports, the gate prints and writes PASS/FAIL/NOT RUN/STALE for all 15 gates, with value, threshold, evidence path, SHA-256, date, dataset version and mode.
17. A missing report gives NOT RUN, and a report older than 14 days or with a mismatched dataset version gives STALE. Neither ever gives PASS, and either one makes the overall verdict FAIL.
18. A CI-mode report offered for a live-only gate is not accepted (that gate reads NOT RUN).
19. Retrieval gates read the `all` subset: on the current P3 report shape, candidate recall@20 0.92 → PASS and reranked recall@5 0.80 → FAIL.
20. Any critical gate that isn't PASS makes the overall verdict FAIL, even when every other gate passes. Exit codes are 0 / 1 / 2 as specified, and an integration test runs the real command as a subprocess.
21. No threshold in `configs/eval_thresholds.yaml` changes in this feature. A test pins the file's gate names and values.
22. Live: the gate is run after the pod session and judge. The committed release report carries its verdict and every evidence SHA-256. *(Measured; the verdict is whatever the evidence says.)*

**Evidence producers**

23. The crash/resume producer records PASS when the test passes, FAIL when it fails, and NOT RUN when it is skipped (no PostgreSQL), in a dated report.
24. The alert exporter writes only non-synthetic deliveries. With a fixture store holding one synthetic and one real alert, it exports exactly the real one, and `langfuse_alert_fired` reads FAIL on synthetic-only evidence.
25. Live: P8 AC #23's live echo rate, and the ALERT state of both alerts (P8 AC #25), are recorded with run IDs and screenshots. One real delivery reaches the receiver and the UI (P9 AC #21). Each one met is recorded in this feature's report, and each one not met by 2026-11-20 is recorded NOT MET with the reason. The P8 and P9 reports and `tasks.md` gain one dated addendum line each.

**Packaging**

26. The README has these sections: status, prerequisites, setup, RunPod, run, live session, evaluate and gate, benchmark, health-check expected output, teardown, reproducibility (§26.3), secrets table. A test checks the headings, and that every script path and command it names exists in the repo.
27. The deployment manifest has every §26.2 step 15 field. A test fails on an empty required field and on any IPv4-shaped string, hostname-like pod address or secret-shaped value. `reproducible_deployment` passes only when the manifest is complete, a fresh-clone health check line with 7/7 PASS exists within 14 days, and the manifest records that no unlisted step was needed.
28. The final report has the required sections and a Mermaid diagram block, has at most 2,500 words outside code blocks and tables (a test counts them; the 5-page check is the developer's on the PDF), and links only to files that exist. Every screenshot it links exists and is ≤ 1 MB.
29. `docs/tech.md`, `docs/structure.md`, `docs/threat-model.md` (the judge data flow), `CLAUDE.md` `## Commands`, and `docs/index.md` are updated in the same change as the code they describe.
30. The full suite passes (`uv run pytest`), and the existing tests are unchanged.

## Flagged concerns

- **Third-party egress of excerpts (privacy and licensing).** Cited excerpts from `reference_only` ASPI/SNAP documents, which have no published terms, are sent to Anthropic's API for judging. They are capped at 300 characters per citation, the same limit as public quoting, and redacted first. *Decides:* Kharisma Wardhana. **Decided: option (a), 2026-11-15.**
- **Credential handling.** The judge needs the developer's Anthropic credential. The agent never reads, prints or uses it, and CI uses a fake client. The threat model gains the eval-time data flow and its controls. *Decides:* Kharisma Wardhana.
- **LLM-graded metrics (evaluation validity).** `golden_answer_supported` and `citation_support_precision` are decided by a model, a different one from the agent's Qwen. The 20-item calibration and its agreement figure are the only check on the judge, and the report states them next to the metrics. *Decides:* Kharisma Wardhana.
- **Subagent judge: independence and reproducibility (amended 2026-11-15).**
  - The judges are the same model family that drafted the expected facts. Fresh contexts keep them from seeing that session, but they aren't an independent model.
  - There is no batch to re-collect and no fixed effort setting, so a re-run gives fresh verdicts that may differ slightly. The verdicts file and its SHA-256 are the record.
  - The developer's blind 20-item calibration stays the check on the judge.
  - *Decides:* Kharisma Wardhana. **Decided: subagent backend, 2026-11-15.**
- **Labelling load against the deadline.** 100 items to review by 2026-11-20 (5 days), plus 20 calibration grades. If review falls behind, the run uses the reviewed items only, and the report states the item count. Unreviewed items are never counted. *Decides:* Kharisma Wardhana.
- **Spend.** Judge ≈ $3–6 (about half through the Batches API) and pod ≈ 2–5 GPU-hours (≈ $1–3) against the $20 total. These are estimates; the actual figures go in the report. *Decides:* Kharisma Wardhana.
- **Gates expected to FAIL.** `reranked_recall_at_5` (0.80 on all 50) and possibly `agent_scenarios_passed` (3/4 last live run). They are reported as measured, and thresholds are not edited. Whether the capstone is submitted with a FAIL verdict and stated limitations is a human call. *Decides:* Kharisma Wardhana.
- **Screenshots in a public repo (privacy).** Langfuse and UI screens can show hostnames, trace IDs, emails or tabs. Each screenshot is checked against the list before commit. *Decides:* Kharisma Wardhana.
- **Accessibility and UX:** none found. I checked the README and report for plain-text structure, and the Mermaid diagram also gets a short text description of the flow in the report. No new UI in this feature.
- **Brand:** none found. I checked that the report and README use the existing synthetic-labelling wording and no ASPI/BI branding.

## Out of scope

- Creating, starting, stopping or terminating RunPod pods from any script.
- Changing any threshold in `configs/eval_thresholds.yaml`, or adding a latency gate.
- Improving retrieval or agent behaviour to turn a FAIL gate into a PASS. That would be a new intent.
- A CI job for the release gate (user decision: local command only), or any CI file change.
- Automatic download of `reference_only` PDFs.
- PDF export of the report.
- An LLM judge for anything other than answer support and citation support.
- Changes to the demo UI, the API or the agent graph.

## Resolved questions

- **Golden-answer dataset:** a new 100-item synthetic dataset (about 50 from the labelled retrieval queries, about 30 new synthetic questions, about 20 abstain/NOT_FOUND/refuse). Claude drafts it and the developer reviews every item. *(User decision, 2026-11-15.)*
- **How support is judged:** Claude Opus 5.5 (`claude-opus-5-5`) as the judge for answer support and citation support, through the Batches API, run by the developer with their credential. It receives excerpts capped at 300 characters per citation plus the synthetic question and answer, after redaction (option a). Citation validity, crypto guidance and the secret scan stay deterministic. A 20-item calibration reports agreement. *(User decision.)* *Amended 2026-11-15 (developer decision):* run as fresh-context Claude Code subagents on Claude Opus 5.5 instead of the Batches API, with the same payload, rubric and parser (Behaviour §3, Amendment).
- **TTFT/TPOT:** `vllm bench serve` inside the pod, 1024/256 tokens, 100 prompts, fixed seed. Concurrency 1 is the baseline, concurrency 4 is informational, and the tunnel TTFT is shown for information. GPU: the RTX PRO 4000 if available, otherwise the A5000 with its FP8-fallback note. *(User decision.)*
- **`reproducible_deployment`:** a complete manifest, plus a green setup-script health check (7/7) on a fresh clone, plus a fresh pod created only from the README's steps. Any unlisted step is a FAIL and gets fixed in the README. *(User decision.)*
- **Release-gate evidence:** it reads the newest reports and never re-runs. 14-day freshness and a matching dataset version are required, or the gate reads STALE. Live-only gates accept only live evidence. Retrieval is judged on all 50 queries. *(User decision.)*
- **P8 AC #23 (live), AC #25, P9 AC #21:** closed in this feature in the same pod session and the controlled alert test. Evidence goes in this feature's report, with dated addenda in the P8 and P9 reports and task lists, and NOT MET is recorded if they aren't met by 2026-11-20. *(User decision.)*
- **Screenshots and diagram:** PNGs in `docs/p9-final-evaluation-packaging/screenshots/`, reviewed against a list, ≤ 1 MB each. The architecture diagram is Mermaid in the report. *(User decision.)*
- **Heavy setup steps and PDFs:** everything runs by default and is idempotent, with `--quick` and `--synthetic-only` flags. A missing or mismatched PDF stops the script with instructions, and it never downloads. *(User decision.)*
- **CI and the release gate:** local command only. Its tests run in the existing `test` job, and no CI file changes. *(User decision.)*
