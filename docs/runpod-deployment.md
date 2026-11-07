# RunPod deployment runbook

How to run one working session: local services up, vLLM on a RunPod GPU, health check green, then tear everything down. Design and reasons: `docs/p1-infrastructure/spec.md`.

**Budget rule:** a pod bills while it exists, so terminate it at the end of every session. The total budget for P1–P9 is $20, about 35 GPU-hours at $0.57/hr.

## Placeholders used here

| Placeholder | Where it comes from |
|---|---|
| `<POD_IP>`, `<POD_SSH_PORT>` | The pod's **Connect** tab, under "SSH over exposed TCP". They change for every new pod. Put them in `.env` only. |
| `<COMMIT_SHA>` | A commit **already pushed** to GitHub that contains `deploy/runpod/bootstrap_vllm.sh`. Get it with `git rev-parse HEAD` after `git push`. |

Never commit real values. `tests/unit/test_repo_hygiene.py` fails if an IP address or a RunPod SSH user appears in a tracked file.

## One-time setup

1. **Create `.env`:** `uv run python scripts/init_env.py`.
   - This fills every secret with a random value and never prints them.
   - Set `RUNPOD_SSH_KEY_PATH` to the private key whose public half is in RunPod → Settings → SSH Public Keys.
2. **Add the vLLM API key to RunPod.** In RunPod → Secrets, create a secret named `VLLM_API_KEY` with the same value as `VLLM_API_KEY` in your `.env`. Copy it from the file in your editor; don't echo it in a terminal.
3. **Check the Podman VM's memory:** `podman info --format '{{.Host.MemTotal}}'`. It should be at least about 16 GB. Rootless Podman here can't enforce container memory limits, so ClickHouse, Langfuse and Redis cap themselves (about 7.25 GiB together; see `docker-compose.yml`), and the VM's own limit is the outer bound.

## Each session

### 1. Local services (on your machine)

```bash
podman compose up -d
podman compose ps          # all 7 running; postgres, minio, redis, clickhouse "healthy"
```

The first start pulls about 3 GB of images and runs Langfuse's migrations, which takes a few minutes. Langfuse's UI is at http://localhost:3000. Log in with `LANGFUSE_INIT_USER_EMAIL` and the password in `.env`.

### 2. Create the pod (RunPod console)

| Setting | Value |
|---|---|
| Template | RunPod PyTorch (official; it runs an SSH server) |
| GPU | 1 × RTX PRO 4000 (24 GB) |
| Container disk | 50 GB. The weights need about 9 GB; vLLM and torch need about 10 GB |
| Volume disk | 0 GB. Nothing persists, by design |
| Expose TCP ports | `22` |
| Expose HTTP ports | **none** |
| Environment variable | `VLLM_API_KEY` = `{{ RUNPOD_SECRET_VLLM_API_KEY }}` |

When it's running, copy the "SSH over exposed TCP" host and port into `.env` as `RUNPOD_SSH_HOST` and `RUNPOD_SSH_PORT`.

### 3. Bootstrap vLLM on the pod

```bash
ssh root@<POD_IP> -p <POD_SSH_PORT> -i <your key>          # the deploy-gate hook will ask first
# --- now on the pod ---
echo "${VLLM_API_KEY:+key is set}"                          # must print "key is set"
#   if it prints nothing, the secret did not reach the SSH session:
#   source /etc/rp_environment 2>/dev/null || { read -rs VLLM_API_KEY && export VLLM_API_KEY; }
curl -fsSL -o bootstrap_vllm.sh \
  https://raw.githubusercontent.com/kharisma-wardhana/qris-devguard-agent/<COMMIT_SHA>/deploy/runpod/bootstrap_vllm.sh
less bootstrap_vllm.sh                                      # read it: it runs as root
BOOTSTRAP_DRY_RUN=1 bash bootstrap_vllm.sh                  # driver check + the exact serve command
bash bootstrap_vllm.sh                                      # install, download, serve; waits until healthy
```

What happens:
1. The script stops immediately if the key is missing or the driver is older than 580.
2. Otherwise it installs `vllm==0.32.0` (a few minutes), starts vLLM on `127.0.0.1:8080`, waits for `/health`, and prints `nvidia-smi` memory.
3. vLLM keeps running after you log out, because the script starts it with `nohup`. Its log is at `/root/vllm.log`.

**If the driver is older than 580** (CUDA 12.9 fallback):
1. Terminate the pod first.
2. Either create another pod and try again, since the driver varies by host, or install vLLM's CUDA 12.9 build. The 0.32.0 release page lists it as `vllm/vllm-openai:v0.32.0-cu129`; check the matching wheel instructions there before using it, and note it needs driver ≥ 575.
3. Don't debug on a running pod.

### 4. Tunnel (on your machine, its own terminal)

```bash
bash scripts/tunnel.sh       # localhost:8080 -> pod 127.0.0.1:8080; leave it running
```

### 5. Health check (on your machine)

```bash
uv run python scripts/healthcheck.py
```

Expected output is `7/7 checks passed` and exit code 0. Each run appends a line to `evals/reports/healthcheck.jsonl` (git-ignored), which the Phase 6 control band reads.

### 6. End the session

1. Stop the tunnel (Ctrl+C).
2. **Terminate the pod** in the RunPod console. "Stop" isn't enough: terminating is what ends billing.
3. Optionally, `podman compose stop` on your machine. Your data stays in the volumes, and `podman compose down -v` deletes it.

## Cost per session (estimate)

| Phase | Time | Cost at $0.57/hr |
|---|---|---|
| Install vLLM + torch | about 3–5 min | about $0.03–0.05 |
| Download about 9 GB of weights + load + CUDA graph capture | about 3–6 min | about $0.03–0.06 |
| Startup total | about 6–11 min | **about $0.06–0.11** |
| Working time | per hour | $0.57 |

The measured startup time from the first live run is recorded below.

## Measured values (P1 live run)

*From the first successful run, 2026-11-07 (`docs/p1-infrastructure` spec AC #11).*

| Item | Value |
|---|---|
| Date | 2026-11-07 |
| GPU / host driver / CUDA | **NVIDIA RTX A5000** (24 GB, Ampere), not the planned RTX PRO 4000 / 595.91.07 / CUDA not captured |
| vLLM version | 0.32.0 |
| Model / revision | Qwen/Qwen3-8B-FP8 @ `220b46e` |
| Max model len / GPU memory utilization | 16384 / 0.90 |
| Startup time (bootstrap start → healthy) | about 110 s on the retry, with vLLM installed and weights cached. The from-scratch time wasn't captured, because the first start failed (see Troubleshooting, `ninja`) |
| GPU memory used after load | 21,735 MiB of 24,564 MiB |
| Health check | 7/7 PASS |
| vllm_chat time-to-first-token / total | 693.7 ms / 693.9 ms |
| Session cost (RunPod billing) | *pending: read from RunPod billing* |

**GPU note:** the A5000 has no native FP8, so vLLM ran the FP8 checkpoint through a fallback kernel. It served correctly, but the A5000's latency and price per hour aren't the RTX PRO 4000 figures this runbook's cost table assumes. Pick the GPU deliberately for P9 benchmarks, and record which one ran.

## Troubleshooting

- **`FileNotFoundError: [Errno 2] No such file or directory: 'ninja'`, then `Engine core initialization failed`:** FlashInfer compiles its sampler on first use and needs `ninja` on `PATH`. Fixed in the bootstrap script (P1 step 11), which now puts the venv's `bin/` on `PATH`. With an older copy of the script, run `export PATH=/root/vllm-venv/bin:$PATH` and rerun it. On a failed start the script now prints the log from the first `ERROR` line, which is where the real cause is.
- **`langfuse_trace` FAIL with HTTP 401:** the project keys didn't match. Langfuse's headless init only runs on the first start, so if you regenerated `.env` after that, run `podman compose down -v`, then `up -d`. This deletes local data.
- **ClickHouse restarting or OOM-killed:** raise the Podman VM memory. Set `memory=24GB` in `%UserProfile%\.wslconfig`, then run `wsl --shutdown`.
- **Tunnel says "address already in use":** something on your machine already uses port 8080. Stop it, or change the port in `configs/model.yaml`, `scripts/tunnel.sh`, `deploy/runpod/bootstrap_vllm.sh` and `.env` together. The tests check that they agree.
- **`vllm_chat` FAIL but `vllm_models` PASS:** look at `/root/vllm.log` on the pod for generation errors.
