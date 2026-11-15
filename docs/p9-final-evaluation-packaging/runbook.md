# Runbook: final evaluation session
*Author: Kharisma Wardhana. Written: 2026-11-15. Via /aspec:make-it-simple-v2. Implements [spec.md](spec.md) Behaviour §2–§5.*

Every step that reaches RunPod, Langfuse's UI or Anthropic is **yours to run**. The agent runs only local, read-only commands such as the release gate and the manifest check. The order matters: the release gate reads the newest evidence, and live-only gates accept only live reports.

## 0. Before the pod (no GPU cost)

1. `git pull`, then `bash scripts/setup.sh`. Locally this ends at 4/7 (the three vLLM checks FAIL), which is expected.
2. Golden items and calibration are reviewed: every item in `evals/datasets/answer_golden.yaml` has `reviewed_by` and `reviewed_on`. Only reviewed items run live.
3. Start PostgreSQL and collect the local evidence:
   ```bash
   podman compose up -d
   uv run python scripts/eval_validators.py
   uv run python scripts/eval_security.py
   uv run python scripts/eval_crash_resume.py        # must say PASS; NOT RUN means PostgreSQL isn't up
   ```

## 1. Pod session (GPU cost runs from here)

Create the pod and bootstrap vLLM ([`docs/runpod-deployment.md`](../runpod-deployment.md) sections 2–3), then follow this order.

| # | Step | Command (on your machine unless marked) |
|---|---|---|
| 1 | Tunnel and health check, 7/7 | `bash scripts/setup.sh` (re-run: it opens the tunnel) |
| 2 | Start the servers | `uv run python scripts/mcp_server.py` and `uv run python scripts/api_server.py` (own terminals) |
| 3 | Four scripted flows, live | `uv run python scripts/eval_agent.py --live --api-smoke` |
| 4 | Golden run (100 items, about 1–3 min each) | `uv run python scripts/eval_answers.py --live` |
| 5 | Live injection cases (P8 AC #23 live) | `uv run python scripts/eval_security.py --live-injection` |
| 6 | Benchmark (**in the pod shell**) | section 2 below |
| 7 | Controlled alert test (P8 AC #25, P9 AC #21) | section 3 below; the latency alert uses the traffic from steps 3–5 |
| 8 | Terminate the pod | RunPod console → Terminate. Billing stops only when the pod is gone |

If the golden run is interrupted, re-run it: it writes a new answers file each time, and the gate uses the newest live one.

## 2. Benchmark inside the pod

`bootstrap_vllm.sh` installs vLLM into `/root/vllm-venv`, which isn't on the SSH shell's `PATH`, so put it there first. Then check the flag names. The plain `--help` lists only the group names, so use `--help=all`. All of these flags were confirmed on vLLM 0.32.0 on 2026-11-15:

```bash
export PATH=/root/vllm-venv/bin:$PATH
vllm bench serve --help=all | grep -E -- '--(dataset-name|random-input-len|random-output-len|num-prompts|seed|max-concurrency|ignore-eos|percentile-metrics|metric-percentiles|save-result|result-dir|result-filename|tokenizer)\b'
```

Then run both concurrencies. Values come from `configs/evaluation.yaml` `bench:` and `configs/model.yaml`:

```bash
mkdir -p /root/bench
for C in 1 4; do
  OPENAI_API_KEY="$VLLM_API_KEY" vllm bench serve --backend openai --base-url http://127.0.0.1:8080 \
    --model qwen3-8b --tokenizer Qwen/Qwen3-8B-FP8 \
    --dataset-name random --random-input-len 1024 --random-output-len 256 --num-prompts 100 --seed 20261115 \
    --ignore-eos --max-concurrency "$C" --percentile-metrics ttft,tpot,itl --metric-percentiles 50,95 \
    --save-result --result-dir /root/bench --result-filename "bench-c$C.json"
done
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
nvidia-smi | grep -o 'CUDA Version: [0-9.]*'
```

- If requests come back 401, the benchmark is not sending the vLLM key. Check `--help` for a header option, and don't disable the key.
- `--metric-percentiles 50,95` is required: the default is `99`, and the parser needs `p95_*`.
- Copy `/root/bench/bench-c1.json` and `bench-c4.json` to `evals/reports/` (scp with the pod's SSH details, or paste the file contents). Then run, on your machine:

```bash
uv run python scripts/bench_report.py evals/reports/bench-c1.json evals/reports/bench-c4.json \
  --gpu "<name from nvidia-smi>" --driver "<driver_version>" --cuda "<CUDA Version>"
```

## 3. Controlled alert test

Follow [`docs/p8-memory-observability/alerts.md`](../p8-memory-observability/alerts.md) "Controlled test" and the webhook setup in [`docs/p9-demo-ui-alerts/runbook.md`](../p9-demo-ui-alerts/runbook.md):

1. The Langfuse webhook automation points at `http://alert-receiver/langfuse/alerts`, and the real webhook secret is pasted into `.env` (`LANGFUSE_ALERT_WEBHOOK_SECRET`). Then run `podman compose --profile alerts up -d alert-receiver`.
2. Fire `devguard-agent-p95-latency-high` (the delay run) and `devguard-incident-event` (the changed-approval run) as `alerts.md` describes.
3. Wait until both show ALERT in Langfuse, and the delivery shows on the UI's Alerts page as an approver.
4. Run `uv run python scripts/export_alerts.py`. It must print `real_alert_fired=True`.
5. Take the screenshots (section 6). Record the alert IDs, times and run IDs in the final report.

## 4. Judge and calibration (after the pod, no GPU)

```bash
uv sync --extra embed --extra ui --extra judge   # all three: a sync without an extra uninstalls it
export ANTHROPIC_API_KEY=...                # your key, in this shell only; never in .env or a file in the repo
uv run python scripts/judge_answers.py evals/reports/answers-live-<timestamp>.json
```

- The script prints the batch ID straight away. If it's interrupted, re-run it with `--batch-id <id>`, which collects the batch without paying again.
- **Calibration:** for the 20 IDs in `evals/datasets/judge_calibration.yaml`, read each live answer in the answers file **before** looking at the judge report. Set `developer_supported: true` only when the answer states every expected fact (or abstains, refuses or says NOT_FOUND as expected) and invents nothing. Then fill `graded_by` and `graded_on`, and re-run the judge command with `--batch-id` to compute agreement without a new batch.

## 5. Rebuild evidence and the release gate

1. **Fresh clone:** stop your stack (`podman compose down`), clone into a new empty folder, place the two PDFs, and run only what the README says (`bash scripts/setup.sh` with the pod configured). Copy its `evals/reports/setup-*.json` back to this repo's `evals/reports/`.
2. **Fresh pod:** the session's pod counts if it was created from the README and `docs/runpod-deployment.md` alone. Write down every step you needed that they don't state.
3. **Manifest:**
   ```bash
   uv run python scripts/deploy_manifest.py --fill
   uv run python scripts/deploy_manifest.py --setup-report evals/reports/setup-<timestamp>.json
   ```
   Then type `gpu.sku`, `gpu.driver`, `gpu.cuda`, `vllm.image`, `fresh_pod.date` and `fresh_pod.followed_readme` into `deployment-manifest.yaml`, and list any unlisted steps in `unlisted_steps`. No IP, pod address or secret goes in. Check it with `uv run python scripts/deploy_manifest.py --check`.
4. **Gate:** `uv run python scripts/export_alerts.py`, then `uv run python scripts/release_gate.py`. It rewrites the generated block of [`release-report.md`](release-report.md). Commit that file and the manifest.

## 6. Screenshots

Save PNGs in `screenshots/` with names that say what they show, each 1 MB or smaller:

| File | What |
|---|---|
| `langfuse-dashboard.png` | Langfuse dashboard with the run traces and scores |
| `alert-latency-alert-state.png` | `devguard-agent-p95-latency-high` in ALERT |
| `alert-incident-alert-state.png` | `devguard-incident-event` in ALERT |
| `ui-alerts-page.png` | the demo UI's Alerts page showing the real delivery |
| `ui-diagnosis.png` | a diagnosis in the §5 shape, with sources |
| `ui-approval.png` | a pending sandbox-ticket approval and its decision |

**Before committing each screenshot, check that it shows none of these:**

- [ ] an IP address, pod ID, RunPod host or SSH port
- [ ] an email address other than the synthetic `admin@devguard.local`
- [ ] an API key, token, password, webhook secret or `.env` content
- [ ] another browser tab or window, bookmarks bar, or desktop notification
- [ ] a real person's or merchant's name or data (only synthetic subjects such as `dev-1`)

Crop or retake rather than blur. A blurred secret is still a published secret.
