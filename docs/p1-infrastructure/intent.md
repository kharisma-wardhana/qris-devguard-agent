# Intent: Qwen3-8B is served by vLLM on one RunPod GPU, and the local PostgreSQL, Qdrant and Langfuse stack can reach it with health checks passing
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*

## Problem statement

Phase P0 is signed off, but nothing runs yet. There is no model endpoint, no database, no vector store and no tracing backend.

Every later phase depends on these four services existing and being reachable:
- P2 ingestion needs Qdrant.
- P3 reranking needs the corpus.
- P5 and P6 need the agent with its checkpoints in PostgreSQL.
- P8 needs Langfuse.
- P9 needs vLLM latency numbers.

The PRD's P1 exit criteria are not met: *health checks pass; baseline model responds*.

The GPU budget is small and fixed. That makes how the system is deployed part of the problem: a setup that keeps the GPU pod running, or re-downloads model weights on every start, would use up the budget before P9.

## Proposed outcome

- **vLLM on RunPod.** It serves `Qwen3-8B` in FP8 on one RunPod RTX PRO 4000 (24 GB) pod, through the OpenAI-compatible `/v1` API. An API key is required, and the pod is reached only through RunPod's authenticated proxy.
- **Local services.** PostgreSQL, Qdrant and a self-hosted Langfuse run on the developer's machine under Podman, from one compose file. Data persists across restarts.
- **One command to check everything.** A single health-check command reports PASS or FAIL for each service:
  - vLLM: `/health`, `/v1/models`, and one authenticated chat completion
  - PostgreSQL
  - Qdrant
  - Langfuse, including receiving one test trace
- **Measured, not assumed.** The exact model revision, vLLM image, GPU, context length and observed VRAM are written down from a real run.
- **Documented start and stop.** Starting and stopping the GPU pod is documented, so the pod only runs while it is actually needed.

## Affected users and systems

- Kharisma Wardhana, who runs the deploy and pays for GPU hours.
- RunPod: one GPU pod (RTX PRO 4000, 12 vCPU, 31 GB RAM, 50 GB container disk, $0.57/hr).
- The local machine: 32 GB RAM, 8 cores, 200 GB free disk, Podman.
- New services: vLLM, PostgreSQL, Qdrant and Langfuse with its own dependencies.
- `.env.example`, `configs/` and the README deploy section.
- `.github/workflows/ci.yml`. CI must keep passing without a GPU or any of these services running.
- Downstream phases P2–P9, which build on these endpoints.

## Constraints

- **Budget:** at most $20 of GPU for P1–P9, about 35 GPU-hours at $0.57/hr. P1 itself should use only a small fraction of that.
- **Deadlines:** P1–P9 must be done by 2026-11-20. Capstone submission is 2026-12-05.
- **GPU:** 24 GB VRAM on a Blackwell-generation card. Model serving must fit, with room left for the KV cache.
- **Model:** Qwen3-8B, FP8, Apache-2.0 licence. The revision is pinned and recorded.
- **Local machine:** 32 GB RAM and 8 cores must run PostgreSQL, Qdrant and the Langfuse stack together.
- **Container runtime:** Podman, not Docker.
- **Public repo:** no credentials, endpoints with real tokens, or pod IDs are committed. Secrets go only in `.env` (git-ignored) and in RunPod secrets.
- **The agent deploys nothing.** Pods are created, started and stopped by the user.
- **Data:** synthetic only. No production systems are contacted.

## Open questions

- Which vLLM image and version supports both Blackwell (RTX PRO 4000) and Qwen3 FP8, and what is the exact model revision to pin?
- Should the model weights live on a persistent RunPod volume, or be downloaded on each start? Which costs less over about 35 hours of use?
- Is reaching vLLM through RunPod's public HTTPS proxy, protected by the vLLM API key, acceptable under the threat model (T13), or is another layer needed?
- What maximum context length and GPU memory setting fit in 24 GB with acceptable KV-cache headroom?
- Will the embedding model and reranker (needed from P2/P3) share the GPU pod or run on the local CPU? P1 should leave room for whichever is chosen.
- Does `podman compose` work on this machine, and which Langfuse self-host version should be pinned?
- Which metric and baseline should the Phase 6 monitoring script watch, given it was deferred from P0 to this feature?
