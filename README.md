# QRIS DevGuard AI

A self-hosted agentic assistant for diagnosing **sandbox** QRIS/SNAP API integration issues: signatures, timestamps, payloads and response codes. Deterministic validators decide validity, every documentation claim cites a chunk retrieved in that run, and the only write action (a sandbox support ticket) needs a second person's approval. It runs Qwen3-8B (FP8) on vLLM on a RunPod GPU, with hybrid retrieval and reranking over Qdrant, a LangGraph agent with a PostgreSQL checkpointer, an MCP tool server, Langfuse tracing and a Streamlit demo UI.

It is a capstone project: **synthetic data only, no production connectivity, no real payment, refund or merchant action.**

- Requirements: [`docs/QRIS_DevGuard_AI_PRD_TRD.md`](docs/QRIS_DevGuard_AI_PRD_TRD.md). Documentation index: [`docs/index.md`](docs/index.md)
- Scope and threat model: [`docs/scope.md`](docs/scope.md), [`docs/threat-model.md`](docs/threat-model.md)
- Final evaluation and report: [`docs/p9-final-evaluation-packaging/`](docs/p9-final-evaluation-packaging/intent.md)

## Status

Each phase has its own folder under `docs/` with its spec, plan and measured report. Values below are measured results, not targets; the release gate (below) is the single PASS/FAIL view.

| Phase | Outcome | Evidence |
|---|---|---|
| P0 Scope and sources | Signed off 2026-11-06 | [`docs/scope-and-source-inventory/`](docs/scope-and-source-inventory/intent.md) |
| P1 Infrastructure | 7/7 health checks passed on 2026-11-07 | [`docs/p1-infrastructure/`](docs/p1-infrastructure/intent.md) |
| P2–P3 Retrieval and reranking | On all 50 labelled queries: candidate recall@20 0.92; recall@5 0.80 fused and 0.80 reranked | [`rerank-report.md`](docs/p3-reranking-eval/rerank-report.md) |
| P4 Deterministic MCP tools | Validator fixtures 110/110 (payload 51, signature 35, response codes 24) | [`validator-report.md`](docs/p4-deterministic-mcp-tools/validator-report.md) |
| P5–P6 Agent, checkpoint, approval | Live scripted flows 3/4 on 2026-11-11 (flow 1 a recorded fail); crash/resume exactly one ticket | [`agent-flow-report.md`](docs/p6-checkpoint-hitl/agent-flow-report.md) |
| P7 MCP hardening | §22.5 security suite 20/20 cases PASS on 2026-11-11 | [`security-report.md`](docs/p7-mcp-hardening/security-report.md) |
| P8 Memory and observability | Memory policy, redacted traces and scores done; live echo rate and alert ALERT state still open | [`observability-report.md`](docs/p8-memory-observability/observability-report.md) |
| P9 Demo UI and alerts | Streamlit UI and alert receiver done; a real Langfuse alert delivery still open | [`demo-report.md`](docs/p9-demo-ui-alerts/demo-report.md) |
| P9 Final evaluation | Release gate, golden answers, benchmark and packaging: tooling done, live session pending | [`release-report.md`](docs/p9-final-evaluation-packaging/release-report.md) |

## Prerequisites

| Tool | Notes |
|---|---|
| Git | Any recent version |
| [uv](https://docs.astral.sh/uv/) | Installs Python 3.11+ and every dependency; tested with uv 0.12 |
| Podman with `podman compose` | Runs PostgreSQL, Qdrant and Langfuse; tested with Podman 6.1. Docker Compose also reads `docker-compose.yml` |
| bash | Linux/macOS bash, or Git Bash on Windows (the setup script and the tunnel are bash) |
| Disk | Several GB free: the CPU embedding stack, two pinned model snapshots and the container images (sizes not measured here) |
| RunPod account + SSH key | Only for the GPU part (vLLM). Everything else runs locally |
| Anthropic API key | Only for the golden-answer judge (`uv sync --extra embed --extra ui --extra judge`); never needed to run the system |

Two public ASPI SNAP PDFs are **reference-only**: they are not in the repository. Download them by hand from the portal pages in [`knowledge_base/manifests/sources.yaml`](knowledge_base/manifests/sources.yaml) and save them at its `local_path`s. The setup script checks them and tells you exactly which file is missing; it never downloads them.

## Setup

```bash
git clone <this repository> && cd qris-devguard-agent
bash scripts/setup.sh                    # full local setup, safe to re-run
bash scripts/setup.sh --quick            # tests + API only: no embedding stack, models or ingest
bash scripts/setup.sh --synthetic-only   # without the reference PDFs (does NOT reproduce the measured results)
bash scripts/setup.sh --help             # the 13 steps and the flags
```

The script runs, in order: prerequisite check, `.env` with random secrets (`scripts/init_env.py`; values are never printed), `uv sync`, `podman compose up -d`, database migrations, test keys, the reference-PDF check, the response-code extract, model download, ingest, the source-manifest check, the SSH tunnel (only when a pod is configured in `.env`) and the health check. It stops at the first failure with the reason and the next action, and writes `evals/reports/setup-<timestamp>.json`.

Without a pod the health check reports the three vLLM checks as FAIL and the setup still completes: that is expected until the next section.

## RunPod

The GPU part is manual by design: creating a pod costs money and needs your RunPod account, so no script here creates, starts or stops one. Follow [`docs/runpod-deployment.md`](docs/runpod-deployment.md):

1. Create the pod (template, GPU, disk, exposed TCP port 22, the `VLLM_API_KEY` secret) — section "Create the pod".
2. Copy the pod's "SSH over exposed TCP" host and port into `.env` as `RUNPOD_SSH_HOST` and `RUNPOD_SSH_PORT`.
3. Bootstrap vLLM on the pod (`deploy/runpod/bootstrap_vllm.sh`; model and revision come from `configs/model.yaml`).
4. Re-run `bash scripts/setup.sh`: it opens the tunnel (`scripts/tunnel.sh`) and the health check should now be 7/7.

## Run

```bash
uv run python scripts/mcp_server.py      # MCP tools on http://127.0.0.1:8765/mcp (terminal 1)
uv run python scripts/api_server.py      # /api/v1 on http://127.0.0.1:8000 (terminal 2; needs the tunnel)
uv sync --extra ui && uv run python scripts/ui_server.py   # demo UI on http://127.0.0.1:8501 (terminal 3)
uv run python scripts/mint_dev_token.py --sub dev-1 --role developer --audience api   # a token for curl
```

`POST /api/v1/chat` takes `{message, context}` and answers in the PRD §5 shape (summary, findings, validation results, next steps, sources, confidence and limitations). Threads, approvals, memory and alerts are described in the phase docs linked from [`docs/index.md`](docs/index.md); the demo walkthrough is [`docs/p9-demo-ui-alerts/runbook.md`](docs/p9-demo-ui-alerts/runbook.md).

## Live session

All live measurements happen in one pod session, in the order of [`docs/p9-final-evaluation-packaging/runbook.md`](docs/p9-final-evaluation-packaging/runbook.md): health check, the four scripted agent flows, the 100 golden questions, the live injection cases, the vLLM benchmark (inside the pod) and the controlled Langfuse alert test. Then terminate the pod.

```bash
uv run python scripts/eval_agent.py --live --api-smoke     # four scripted flows on the real stack
uv run python scripts/eval_answers.py --live               # 100 golden questions -> answers file + deterministic checks
uv run python scripts/eval_security.py --live-injection    # the 10 injection cases on the live model
```

## Evaluate and release gate

```bash
uv run pytest                                              # the test suite (no services needed; db tests skip without PostgreSQL)
uv run python scripts/eval_validators.py                   # validator fixtures, must be 100%
uv run python scripts/eval_security.py                     # §22.5 security suite (start PostgreSQL first)
uv run python scripts/eval_crash_resume.py                 # crash/resume evidence (needs PostgreSQL)
uv sync --extra embed --extra ui --extra judge            # all three: a sync without an extra uninstalls it
uv run python scripts/judge_answers.py evals/reports/answers-live-<timestamp>.json   # Claude Opus 5.5 judge (your API key)
uv run python scripts/export_alerts.py                     # real Langfuse alert deliveries
uv run python scripts/deploy_manifest.py --check           # deployment manifest completeness
uv run python scripts/release_gate.py                      # PASS/FAIL on every gate -> release-report.md
```

The release gate reads the thresholds in `configs/eval_thresholds.yaml` (targets; never edited to pass) and the newest evidence in `evals/reports/`. A gate without evidence reads NOT RUN, evidence older than 14 days or for an outdated dataset reads STALE, and neither counts as PASS. Gates that need the live model accept only live evidence.

## Benchmark

TTFT/TPOT are measured with `vllm bench serve` **inside the pod** (fixed settings: random dataset, 1024 input and 256 output tokens, 100 prompts, fixed seed, concurrency 1 and 4); the exact commands are in the runbook. Copy the result JSONs into `evals/reports/`, then:

```bash
uv run python scripts/bench_report.py evals/reports/bench-c1.json evals/reports/bench-c4.json \
  --gpu "<GPU from nvidia-smi>" --driver "<driver>" --cuda "<CUDA>"
```

Concurrency 1 is reported against the PRD §22 targets (p95 TTFT ≤ 5 s, p95 TPOT ≤ 120 ms); it is not a release gate. Retrieval before/after reranking: `uv run python scripts/eval_retrieval.py` (with reranking it takes hours on CPU; `--no-rerank` for a quick run).

## Expected health-check output

`uv run python scripts/healthcheck.py` prints one line per check and appends it to `evals/reports/healthcheck.jsonl`:

```text
PASS  vllm_health          ...  ms
PASS  vllm_models          ...  ms
PASS  vllm_chat            ...  ms  (ttft ... ms)
PASS  postgres             ...  ms
PASS  qdrant               ...  ms
PASS  langfuse_health      ...  ms
PASS  langfuse_trace       ...  ms
7/7 checks passed
```

Without a pod and tunnel, the three `vllm_*` lines read FAIL and the rest PASS (4/7). Any other FAIL is a local problem: `podman compose ps` and `podman compose logs <service>`.

## Teardown

```bash
# 1. Terminate the pod in the RunPod console (Terminate, not Stop): billing ends only when the pod is gone.
kill "$(cat var/tunnel.pid)" 2>/dev/null   # 2. the tunnel the setup script started
#    3. Ctrl+C the MCP server, API and UI terminals
podman compose down                        # 4. stop the local services; data volumes are kept
podman compose down -v                     #    only if you want to delete PostgreSQL, Qdrant and Langfuse data
```

## Reproducibility

| TRD §26.3 item | Where it is recorded |
|---|---|
| GPU SKU and region, driver, CUDA | [`deployment-manifest.yaml`](docs/p9-final-evaluation-packaging/deployment-manifest.yaml) (`gpu.*`), from `nvidia-smi` on the pod |
| Container images and versions | `docker-compose.yml` (pinned tags) and their digests in the manifest (`images.*`) |
| Model, revision, quantization, vLLM version, context length | `configs/model.yaml`, copied into the manifest (`model.*`, `vllm.*`) |
| Environment variables and which are secrets | `.env.example` and the table below |
| Volume mounts and backups | `docker-compose.yml` named volumes; back up with `podman volume export <volume>`; the pod keeps nothing (volume disk 0 GB) |
| One-command local startup | `bash scripts/setup.sh` |
| Fresh RunPod deployment | [`docs/runpod-deployment.md`](docs/runpod-deployment.md) |
| Knowledge ingestion | `uv run python scripts/ingest.py` (setup step 10) |
| Evaluation and benchmark | sections "Evaluate and release gate" and "Benchmark" above |
| Expected health-check results | section "Expected health-check output" above |
| Teardown | section "Teardown" above |
| Dependency lockfile hash | `uv.lock` SHA-256 in the manifest (`lockfile.sha256`) |

`uv run python scripts/deploy_manifest.py --fill` writes the locally readable fields, and `--setup-report` merges a fresh-clone setup run; the pod fields are pasted from the pod shell. The `reproducible_deployment` gate passes only when the manifest is complete, a fresh clone reached 7/7 within 14 days, and the fresh pod needed no step this README doesn't state.

## Secrets

`scripts/init_env.py` creates `.env` from `.env.example` and fills every `replace-me` with a random value; `set-me` values are yours. `.env` is git-ignored and never printed by any script.

| Variable | Secret | Set by |
|---|---|---|
| `APP_SECRET_KEY`, `MCP_AUTH_SECRET` | yes | `init_env.py` |
| `POSTGRES_PASSWORD`, `DATABASE_URL` (contains it) | yes | `init_env.py` |
| `DEVGUARD_APP_DB_PASSWORD`, `DEVGUARD_TICKET_DB_PASSWORD`, `DEVGUARD_AUDIT_DB_PASSWORD` | yes | `init_env.py` |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_INIT_USER_PASSWORD`, `LANGFUSE_DB_PASSWORD` | yes | `init_env.py` |
| `SALT`, `ENCRYPTION_KEY`, `NEXTAUTH_SECRET`, `CLICKHOUSE_PASSWORD`, `REDIS_AUTH`, `MINIO_ROOT_PASSWORD` | yes | `init_env.py` |
| `VLLM_API_KEY` | yes | `init_env.py`; copy it into the RunPod secret `VLLM_API_KEY` |
| `LANGFUSE_ALERT_WEBHOOK_SECRET` | yes | `init_env.py`, then replaced with the secret Langfuse shows when you create the webhook |
| `RUNPOD_SSH_HOST`, `RUNPOD_SSH_PORT` | no, but private (they locate your pod) | you, per pod |
| `RUNPOD_SSH_KEY_PATH` | no (a path; the key file itself is secret) | you |
| `ANTHROPIC_API_KEY` | yes | you, in your shell, only for the judge; not in `.env` |
| `APP_ENV`, `QDRANT_URL`, `LANGFUSE_HOST`, `LANGFUSE_INIT_USER_EMAIL`, `VLLM_BASE_URL`, `MODEL_ID`, `EMBEDDING_MODEL_ID`, `RERANKER_MODEL_ID`, `DEVGUARD_GPU_LABEL`, TTL days | no | defaults in `.env.example` |

## Development

```bash
uv run pytest                                   # all tests
uv run pytest -m db                             # PostgreSQL tests (start it: podman compose up -d postgres)
uv run python scripts/check_sources.py          # validate the source manifest
```

`CLAUDE.md` lists every command and the project's conventions; `docs/tech.md` and `docs/structure.md` describe the stack and the layout.
