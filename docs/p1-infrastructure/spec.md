# Spec: Qwen3-8B is served by vLLM on one RunPod GPU, and the local PostgreSQL, Qdrant and Langfuse stack can reach it with health checks passing
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Implements: docs/p1-infrastructure/intent.md*

## Behaviour

A developer session runs in this order. Each step is one command, documented in the deployment runbook.

1. **Start local services.** One `podman compose up -d` starts PostgreSQL, Qdrant and self-hosted Langfuse (web, worker, ClickHouse, Redis, MinIO) on the developer's machine. Every port is bound to `127.0.0.1`, and data persists in named volumes.
2. **Create the GPU pod** (done by the user in the RunPod console):
   - template: RunPod's official PyTorch template, which already runs an SSH server;
   - GPU: RTX PRO 4000;
   - networking: TCP port 22 exposed, and **no HTTP ports exposed**;
   - secret: `VLLM_API_KEY`, set as a RunPod secret.
3. **Bootstrap vLLM on the pod.** The user SSHes in and runs the repo's bootstrap script, fetched at a pinned commit. The script:
   1. checks the NVIDIA driver and stops with a clear message if it's below what the vLLM build needs, before spending time on downloads;
   2. installs the pinned vLLM version into its own virtual environment;
   3. downloads the pinned model revision;
   4. starts vLLM bound to `127.0.0.1:8080` inside the pod, with the API key required.
4. **Open the tunnel.** Locally, `ssh -N -L 8080:127.0.0.1:8080 …` forwards the pod's port 8080 to `localhost:8080`, using connection details read from `.env`. The pod exposes nothing to the internet except SSH (key-only).
5. **Run the health check.** One local command checks every service and prints one PASS/FAIL line each:
   - `vllm_health`, `vllm_models` and `vllm_chat` (a short non-thinking completion; records time-to-first-token and total latency);
   - `postgres`, `qdrant`, `langfuse_health`, and `langfuse_trace` (sends one test trace and confirms it was accepted).

   It exits 0 only when every check passes. It never prints API keys, passwords or the SSH target. Each run appends one JSON line of results to the git-ignored eval reports folder.
6. **End the session.** The user stops the tunnel and **terminates** the pod. Nothing is left running or billing on RunPod.

Measured values from the first successful run are recorded in the deployment runbook: driver version, CUDA version, peak VRAM, model load time, context length, and the health-check latencies.

## Architecture and the reason for it

Follows `docs/tech.md` and `docs/structure.md`: Python with uv and pytest, policy and model settings in `configs/`, scripts under `scripts/`, CLI tests in `tests/integration/`. It departs from the existing setup in four places, listed after the table.

| Decision | Reason |
|---|---|
| **Only vLLM runs on RunPod.** PostgreSQL, Qdrant and Langfuse run locally under Podman | Spends GPU hours only on the model (about 35 h budget for P1–P9). User choice |
| **Model `Qwen/Qwen3-8B-FP8` at revision `220b46e3b2180893580a4454f21f22d3ebb187d3`** | Apache-2.0, FP8 block-wise (block size 128), 32k native context, vLLM ≥ 0.8.5. User choice, pinned for reproducibility |
| **vLLM `0.32.0`** (latest stable, 18 Oct; its notes list SM120 support), installed with uv into a virtual environment on the PyTorch template | The official `vllm/vllm-openai` image has no SSH server, so it can't serve as the tunnel endpoint. Installing a pinned wheel on a template that already runs SSH avoids building and hosting a custom image |
| **CUDA build:** the default (CUDA 13.0, driver ≥ 580). Fallback: the CUDA 12.9 build if the host driver is older | The bootstrap script checks the driver first, so a mismatch costs seconds, not a failed 10-minute install |
| **Serve settings:** `--max-model-len 16384`, `--gpu-memory-utilization 0.90`, `--max-num-seqs 8`, `--served-model-name qwen3-8b`, `--reasoning-parser qwen3`, `--enable-auto-tool-choice --tool-call-parser hermes`, request logging off | FP8 weights take about 9 GB, leaving about 10 GB of KV cache (about 70k tokens at bf16 KV), enough for several 16k sequences. The tool-calling flags are set now because P5 needs them. All values live in config and are checked against reality in the first run |
| **Health-check chat:** non-thinking mode (`enable_thinking: false` through the chat template), temperature 0.7, top_p 0.8 | Qwen's recommended settings for non-thinking mode. Gives a short, fast, deterministic-length response for a latency probe |
| **Weights are downloaded to the container disk on every pod start.** No persistent volume | About 9 GB per start, roughly $0.01–0.03 of GPU time. A stopped pod's volume costs $0.20/GB/month and may not get its GPU back; a network volume locks the pod to one datacenter. Every session is also a from-scratch run, which proves the PRD's reproducibility requirement (FR-18) |
| **The pod is reached by SSH tunnel to vLLM on `127.0.0.1`,** with the API key kept as a second layer | vLLM's API key doesn't cover `/health`, `/metrics` or `/tokenize` (threat T13). With no HTTP port exposed, none of these are reachable from the internet. User choice |
| **The bootstrap script is fetched from the public repo at a pinned commit SHA,** never from a branch name | Code that runs as root on the pod is pinned to what was reviewed |
| **Langfuse v4 self-hosted**, following its official compose file: one exact version ≥ 4.75.0, chosen and pinned at build time. The optional AI-gateway service is left out. Langfuse telemetry is off | Langfuse self-hosting is v4 now. The compose file requires a version newer than 4.75.0. The AI gateway isn't needed and costs memory |
| **One PostgreSQL 17 instance** with two databases, `devguard` and `langfuse`, each with its own user | Saves one PostgreSQL container on a machine where Podman has about 16.5 GB |
| **Memory caps set inside each heavy service, not as container limits:** ClickHouse `max_server_memory_usage` 4 GiB (config file), Langfuse web and worker Node heap `--max-old-space-size=1536` each, Redis `--maxmemory 256mb`. PostgreSQL, Qdrant and MinIO keep their small defaults. The WSL VM's own memory limit (about 16.5 GB) is the outer bound | Rootless Podman on this machine has no cgroup controllers delegated (`controllers=[]`), so `mem_limit` makes containers fail to start (`crun: open memory.max`; found at step 6, 2026-11-06). Application-level caps work without cgroups and leave the user's other Podman projects untouched. User choice over switching Podman to rootful |
| **Health-check CLI** using `httpx` and `psycopg` as the only new runtime dependencies. Qdrant and Langfuse are checked over their REST APIs; the test trace goes through Langfuse's public ingestion API | Two small, widely used libraries; no Qdrant or Langfuse SDK needed just for checks |

**Changes to earlier decisions, each flagged:**
- **Egress allowlist (`scope-and-source-inventory`, `configs/security.yaml`):** `cloud.langfuse.com` is replaced with the local Langfuse host. `docs/threat-model.md` must change in the same commit, as `REVIEW.md` § Security requires.
- **`.env.example`:** `LANGFUSE_HOST` changes from the Cloud URL to `http://localhost:3000`.
- **`VLLM_BASE_URL`** changes from `http://vllm:8000/v1` to `http://localhost:8080/v1`, which is the tunnel's local end. Port 8080 was chosen by the user during step 1, replacing vLLM's default 8000.
- **TRD §20.2's illustrative launch** binds to `0.0.0.0`. This spec binds to `127.0.0.1`, because the tunnel replaces public exposure.

## Data

- **PostgreSQL** has two databases:
  - `devguard`: empty in P1; tables arrive in later phases.
  - `langfuse`: managed by Langfuse.

  Synthetic data only. Kept in a local named volume until it is deleted.
- **Qdrant:** no collections in P1. Local named volume.
- **Langfuse:** traces from health checks only, with no prompts beyond a fixed synthetic probe sentence. Stored locally in ClickHouse and MinIO; never sent to Langfuse Cloud.
- **Health-check results:** a JSON line per run with check name, status and latency figures. No secrets, no hostnames, no SSH target. Kept in the git-ignored `evals/reports/` folder; this is also the data source for the Phase 6 control band.
- **Secrets:**
  - vLLM API key;
  - PostgreSQL passwords;
  - Langfuse salt, encryption key and NextAuth secret;
  - ClickHouse, Redis and MinIO passwords;
  - Langfuse project keys.

  These live only in the git-ignored `.env` and in RunPod secrets. `.env.example` holds `replace-me` placeholders and generation hints. The SSH host, port and key path also live only in `.env`.
- **Model weights:** downloaded from Hugging Face on each pod start and deleted when the pod is terminated.

## Acceptance criteria

1. **The compose file starts everything.** It defines PostgreSQL, Qdrant, ClickHouse, Redis, MinIO, Langfuse web and Langfuse worker, and nothing else. On the developer machine, `podman compose up -d` brings every service to a running or healthy state within 5 minutes. Evidence: `podman compose ps` output, pasted.
2. **Every published port is bound to `127.0.0.1`.** Checked by a unit test that parses the compose file.
3. **Every image is pinned** to an explicit version tag or digest; none uses `latest` or no tag. Checked by a unit test.
4. **The heavy services are memory-capped inside the services themselves.** ClickHouse is capped by a mounted config file at ≤ 4 GiB; Langfuse web and worker each set a Node heap limit ≤ 1536 MiB; Redis sets `--maxmemory`; and these caps add up to at most 8 GiB. No service sets `mem_limit`, `mem_reservation` or `deploy.resources`, because rootless Podman here cannot apply them. Checked by a unit test.
5. **No secret values are in tracked files.**
   - Every password or secret in the compose file comes from an environment variable.
   - Every such variable has an entry in `.env.example` whose value contains `replace-me`.
   - No tracked file contains an IPv4 address other than `127.0.0.1` and `0.0.0.0`, or a `*.ssh.runpod.io` user string.

   Each of these is checked by a unit test.
6. **The health-check CLI's verdict is correct.**
   - It prints exactly one PASS/FAIL line for each of the 7 checks named in Behaviour step 5.
   - It exits 0 when every check passes, and 1 when any check fails.

   Unit tests cover all-pass, each single failure, and timeouts, using mocked HTTP and database connections.
7. **The health-check CLI never outputs secrets.** When the configured API key, passwords and Langfuse keys are set to distinctive sentinel values, none of those values appears in its standard output, its error output, or the JSON line. Checked by a unit test, and by an integration test that runs the CLI as a subprocess against closed local ports and expects exit 1 with every line FAIL.
8. **The health-check request matches the spec's settings.**
   - The `vllm_chat` check sends `chat_template_kwargs.enable_thinking = false` with temperature 0.7 and top_p 0.8.
   - It records a time-to-first-token in milliseconds and a total latency in the JSON line.

   Checked by a unit test that inspects the mocked request.
9. **One config holds the vLLM settings,** and the bootstrap script agrees with it. The model config has:

   | Setting | Value |
   |---|---|
   | Model | `Qwen/Qwen3-8B-FP8` |
   | Revision | `220b46e3b2180893580a4454f21f22d3ebb187d3` |
   | vLLM | `0.32.0` |
   | Max model length | 16384 |
   | GPU memory utilization | 0.90 |
   | Max sequences | 8 |
   | Served name | `qwen3-8b` |
   | Reasoning parser | `qwen3` |
   | Tool-call parser | `hermes` |

   The bootstrap script uses the same values. Checked by a unit test that compares them.
10. **The bootstrap script fails safely.**
    - It passes `bash -n`.
    - It binds vLLM to `127.0.0.1`.
    - It exits non-zero with a clear message when `VLLM_API_KEY` is unset, or when the driver is below the required version.
    - It contains no secret values.

    Checked by unit tests: static checks, plus running the script with a stubbed `nvidia-smi` and no key.
11. **The live run works.** The user runs the deployment once. The pasted output shows:
    - the driver version;
    - all 7 health checks PASS;
    - `nvidia-smi` peak memory under 24 GB.

    These values are recorded in the deployment runbook. The P1 GPU spend is $2 or less, read from the RunPod billing page.
12. **The deployment runbook covers a whole session.**
    - It documents, in order: local stack up, pod creation settings, bootstrap, tunnel, health check, stop, and terminate.
    - It states the per-session cost estimate.
    - It contains only placeholders for the host, port, key path and pod ID.
13. **The threat model is updated.**
    - T13's control is updated to the SSH tunnel with no exposed HTTP port.
    - A new entry covers the pod's SSH exposure: key-only root login on a public IP while the pod runs.
    - The egress allowlist change is reflected.
14. **CI still passes with nothing running.** `uv run pytest` passes in CI with no GPU and no services, and every test written before this feature still passes unchanged.

## Flagged concerns

- **Security: a public SSH port while the pod runs.** The PyTorch template allows key-only root login on a public IP. Mitigations: terminate the pod after every session, no password login, and a dedicated key. **Decides:** Kharisma Wardhana (accept for the capstone).
- **Supply chain: running a script from the repo as root on the pod.** Mitigations: fetch it by commit SHA, and keep it short enough to review. Installing vLLM from PyPI is pinned to `0.32.0`. **Decides:** Kharisma Wardhana.
- **Two SSH keys mentioned.** Your two commands used `id_ed25519_runpod` and `id_ed25519`. The key path is read from `.env`; use the one registered with RunPod. **Decides:** Kharisma Wardhana.
- **Driver and CUDA risk.** vLLM 0.32.0 defaults to CUDA 13, which needs driver ≥ 580. The RunPod host driver is unknown until the first pod starts. The bootstrap script checks it first, with the CUDA 12.9 build as the fallback; if neither fits, pick a different pod or host. **Decides:** Kharisma Wardhana, at the first run.
- **Local memory pressure.** Langfuse alone recommends 16 GiB, and the Podman VM has about 16.5 GB. The service-level caps total about 7.25 GiB for ClickHouse, Langfuse and Redis, plus PostgreSQL, Qdrant and MinIO at their defaults (well under 1 GB each when idle). These caps aren't hard container limits: an uncapped service can still grow. If the VM runs short, raise WSL's memory limit in `%UserProfile%\.wslconfig`, for example `memory=24GB`. That's a change to your machine. **Decides:** Kharisma Wardhana.
- **Earlier decision changed.** The P0 egress allowlist named Langfuse Cloud; this replaces it with the local Langfuse. **Decides:** Kharisma Wardhana. Documented in the threat model in the same change.
- **Budget.** Each session costs pod time while the weights download and the model loads, about 3–6 minutes. If something keeps failing, terminate the pod rather than debugging on the clock. **Decides:** Kharisma Wardhana.
- **Privacy:** none found. Checks covered: all trace content is a fixed synthetic probe, Langfuse is self-hosted with telemetry off, and the results file holds no host or secret.
- **Accessibility and UX:** none found. There is no user interface in this feature, only CLI output.

## Out of scope

- Embedding and reranker models. Decided in P2/P3; the current plan is local CPU, so vLLM keeps 90% of the GPU.
- Application code (FastAPI, LangGraph, MCP server), database tables and migrations, and Qdrant collections.
- `vllm bench serve` throughput benchmarking and the formal TTFT/TPOT report (P9). P1 records only the health-check latencies.
- Automating pod creation or termination. Pods are created and terminated by the user.
- TLS for the local services. They are loopback-only.
- Langfuse alerts (P8).

## Resolved questions

- **Which vLLM image or version supports Blackwell and Qwen3 FP8?** vLLM `0.32.0`, the latest stable (18 Oct), whose release notes mention SM120. It's installed as a wheel (default CUDA 13.0, driver ≥ 580; CUDA 12.9 build as fallback) on RunPod's PyTorch template, because the official image has no SSH server. Model `Qwen/Qwen3-8B-FP8` at revision `220b46e`.
- **Persistent volume, or download on each start?** Download on each start to the container disk, and terminate after each session. It's cheapest and has no idle cost.
- **Is the RunPod proxy plus API key enough?** Replaced by an SSH tunnel through full SSH over exposed TCP (public IP available on this pod), with vLLM on `127.0.0.1` and no HTTP port exposed. The API key stays as a second layer. *(user chose SSH)*
- **Context length and GPU memory setting?** `max-model-len 16384`, `gpu-memory-utilization 0.90`, `max-num-seqs 8`, verified against measured VRAM in the live run (AC #11).
- **Embedding and reranker on the GPU or local CPU?** Planned for local CPU, decided finally in P2/P3. P1 gives vLLM 90% of the GPU.
- **Does `podman compose` work, and which Langfuse version?** Yes: Podman 6.1.0 with podman-compose 1.6.0, in a WSL VM with about 16.5 GB and 18 CPUs. Langfuse is v4, one exact version ≥ 4.75.0 pinned at build time.
- **Which metric for Phase 6?** The `vllm_chat` time-to-first-token in milliseconds, from the health-check JSON lines. The baseline window is the first 8 successful runs.
