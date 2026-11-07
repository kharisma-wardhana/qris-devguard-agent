# Plan: Qwen3-8B is served by vLLM on one RunPod GPU, and the local PostgreSQL, Qdrant and Langfuse stack can reach it with health checks passing
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Implements: docs/p1-infrastructure/spec.md*

## Affected files

- `pyproject.toml`, `uv.lock`: edit. Adds `httpx` and `psycopg[binary]`.
- `configs/model.yaml`: new. vLLM and model settings (spec AC #9).
- `deploy/runpod/bootstrap_vllm.sh`: new. Pod-side driver check, vLLM install, model download and serve.
- `tests/unit/test_bootstrap_vllm.py`: new. Static checks, stubbed-run checks and config agreement.
- `.gitattributes`: new. Forces LF line endings for `*.sh`, so scripts don't break on Windows checkouts. Added during step 1.
- `scripts/healthcheck.py`: new. The 7-check health CLI.
- `tests/unit/test_healthcheck.py`: new. Mocked HTTP and DB tests: verdicts, request shape, secret hiding.
- `tests/integration/test_healthcheck_cli.py`: new. Runs the CLI as a subprocess against closed ports.
- `docker-compose.yml`: new. The local stack: PostgreSQL, Qdrant and Langfuse v4 (web, worker, ClickHouse, Redis, MinIO).
- `deploy/postgres/init-databases.sh`: new. Creates the `langfuse` database and user next to `devguard`.
- `deploy/clickhouse/memory.xml`: new. ClickHouse server memory cap (added in step 6, replacing container memory limits).
- `.env.example`: edit. New keys, `LANGFUSE_HOST` and `VLLM_BASE_URL` set to local, SSH placeholders, Langfuse headless-init keys.
- `scripts/init_env.py`: new. Creates `.env` from `.env.example` with random secrets. Refuses to overwrite an existing `.env`.
- `tests/unit/test_init_env.py`: new.
- `configs/security.yaml`: edit. In `egress_allowlist`, replaces `cloud.langfuse.com` with `localhost`.
- `tests/unit/test_compose.py`: new. Loopback ports, pinned images, memory limits, secrets only from environment variables.
- `tests/unit/test_repo_hygiene.py`: edit. Adds a check for IPv4 literals and `ssh.runpod.io` user strings in tracked files.
- `docs/threat-model.md`: edit. T13 control, a new T18 for pod SSH exposure, the egress note.
- `scripts/tunnel.sh`: new. Opens the SSH tunnel using `RUNPOD_SSH_*` from `.env`.
- `docs/runpod-deployment.md`: new. Session runbook, cost estimate, measured values.
- `README.md`: edit. Links the runbook.
- `CLAUDE.md`: merge. Agent configuration, below.
- `docs/p1-infrastructure/tasks.md`: new.
- `docs/index.md`: edit. Links the plan and tasks.

## Work sequence

1. **Model config and pod bootstrap script.** `configs/model.yaml`, `deploy/runpod/bootstrap_vllm.sh`, `tests/unit/test_bootstrap_vllm.py`, `.gitattributes`. Spec AC #9, #10.
2. **Health-check CLI.** `pyproject.toml`, `uv.lock`, `scripts/healthcheck.py`, `tests/unit/test_healthcheck.py`, `tests/integration/test_healthcheck_cli.py`. Spec AC #6, #7, #8.
3. **Local stack definition and env.** `docker-compose.yml`, `deploy/postgres/init-databases.sh`, `.env.example`, `scripts/init_env.py`, `tests/unit/test_init_env.py`, `configs/security.yaml`, `tests/unit/test_compose.py`, `tests/unit/test_repo_hygiene.py`. Spec AC #2, #3, #5 (AC #4 moved to step 6). The Langfuse and MinIO versions are pinned here, read from their release pages.
4. **Threat model.** `docs/threat-model.md`. Spec AC #13.
5. **Tunnel helper and runbook.** `scripts/tunnel.sh`, `docs/runpod-deployment.md`, `README.md`. Spec AC #12.
6. **Application-level memory caps** (added 2026-11-06 after the first bring-up attempt failed with `crun: open memory.max`). Files: `docker-compose.yml` (remove `mem_limit`, add `NODE_OPTIONS` and Redis `--maxmemory`, mount the ClickHouse config), `deploy/clickhouse/memory.xml`, `tests/unit/test_compose.py`, `docs/runpod-deployment.md` (memory note). Spec AC #4.
7. **Bring-up fixes** (added 2026-11-06 after the first bring-up). Files: `scripts/healthcheck.py` and `tests/unit/test_healthcheck.py` (OTLP trace), `docker-compose.yml` and `tests/unit/test_compose.py` (Redis password from environment, not command line), `.env.example` (`127.0.0.1` URLs). Spec AC #5, #6. **User action afterwards:** in your `.env`, change `localhost` to `127.0.0.1` in `DATABASE_URL`, `QDRANT_URL`, `LANGFUSE_HOST` and `VLLM_BASE_URL`; optionally rotate `REDIS_AUTH`. I can't read or edit `.env`.
8. **Local stack bring-up.** No new files.
   - Run `uv run python scripts/init_env.py`, then `podman compose up -d`.
   - Paste `podman compose ps` and the health-check output. Expect `postgres`, `qdrant`, `langfuse_health` and `langfuse_trace` PASS, and the three vLLM checks FAIL, because no pod is running yet.
   - Spec AC #1.
   - I run this locally, after asking first, because it pulls about 3 GB of images.
9. **Live GPU run (user).** You create the pod, bootstrap, tunnel and run the health check, then terminate the pod. I record the measured values in `docs/runpod-deployment.md`. Spec AC #11.
10. **`CLAUDE.md` merge.** Appends to `## Commands` and `## Test scope`. Agent configuration.
11. **Bootstrap fixes from the live run** (added 2026-11-07, after the first live start failed with `FileNotFoundError: 'ninja'` while FlashInfer JIT-compiled its sampler). Files: `deploy/runpod/bootstrap_vllm.sh`, `tests/unit/test_bootstrap_vllm.py`, `docs/runpod-deployment.md`. The script puts the venv's `bin/` on `PATH` and makes sure `ninja` is there before serving. On a failed start it prints the log from the first `ERROR` line, not only the last 40 lines (the 40-line tail hid the cause). Spec AC #10, #11.

## Test coverage

These rows from `test-scope-by-stage.md` matched:
- **Backend service / script only.** It applies to steps 1, 2, 3 and 5.
- **Phase 0 detected an existing project.** It applies to steps 3 and 4.
- **Any database.** It matched because the project now has PostgreSQL. P1 creates databases but no schema and no queries on stored data, so no regression test is owed yet. The row is recorded in `CLAUDE.md` for later phases.

Not matched: UI (none) and accounts/roles (no application auth in P1; Langfuse's login is third-party).

- **Step 1:**
  - Unit tests: `bash -n`, the bind address is `127.0.0.1`, no secrets, and config agreement.
  - Integration test at the entry point: run the script with a stubbed `nvidia-smi` and stubbed `pip`/`uv`/`vllm` on `PATH`. Expect a non-zero exit and a clear message when `VLLM_API_KEY` is unset, and when the driver is below 580.
- **Step 2:**
  - Unit tests with `httpx.MockTransport` and an injected DB connector: all-pass, each single failure, timeouts, the request body, TTFT recorded, and sentinel secrets absent from stdout, stderr and the JSON line.
  - Integration test: the CLI as a subprocess against closed `127.0.0.1` ports. Expect exit 1, 7 FAIL lines, and no sentinel values in the output.
- **Step 3:**
  - Unit tests on the parsed compose file and on `.env.example`.
  - Integration test for `scripts/init_env.py` as a subprocess in a temp directory: it creates `.env`, replaces every `replace-me`, refuses to overwrite, and prints no secret.
  - Regression: `tests/unit/test_repo_hygiene.py` keeps its existing tests unchanged, and the full suite passes after the `security.yaml` edit.
- **Step 4:** documentation only. Spec AC #13 is checked at Phase 4 by quoting the changed rows.
- **Step 5:** `scripts/tunnel.sh` passes `bash -n`, and its tunnel target is `127.0.0.1:8080`. That's a unit check in `tests/unit/test_bootstrap_vllm.py`'s companion section. The runbook is checked at Phase 4 against AC #12.
- **Step 7:** unit tests: the OTLP request shape and basic auth (replacing the ingestion-endpoint test, whose behaviour the spec no longer requires); no `${REDIS_AUTH` on any `command:` or `healthcheck:`; every local URL in `.env.example` uses `127.0.0.1`. Regression: all other tests unchanged.
- **Step 6:** unit tests in `tests/unit/test_compose.py` for the caps and for the absence of `mem_limit`. The earlier memory-limit test is replaced, not kept, because the spec criterion it checked changed.
- **Step 11:** stubbed-run tests: with `ninja` only in the venv's `bin/`, a stub `vllm` that needs `ninja` on `PATH` starts and the script reports ready (regression for the live failure); a stub `vllm` that logs an early `ERROR` line followed by 60 lines of noise and exits makes the script print that `ERROR` line. The poll interval becomes `VLLM_POLL_S` (default 10) so these tests run in seconds. Regression: all existing bootstrap tests unchanged.
- **Steps 8–9:** pasted output from real runs. These are the evidence for AC #1 and #11.
- **Step 10:** covered by the existing `agent-config-eval` workflow.

## Agent configuration changes

- **`CLAUDE.md` (step 10):**
  - Append to `## Commands`: `uv run python scripts/init_env.py`, `podman compose up -d`, `bash scripts/tunnel.sh` and `uv run python scripts/healthcheck.py`.
  - Append to `## Test scope`: the database row.
  - No other section changes. I'll show the diff and wait for `approve write`.
- **Hooks:** none added. Verified in Phase 0: `eval.sh` passes 4/4. The existing deploy gate already asks before `ssh` and `runpodctl`, so `scripts/tunnel.sh` and pod work will prompt you. That's intended.
  - *Correction, 2026-11-07 (Phase 5):* the claim that `scripts/tunnel.sh` prompts was wrong. The hook sees only the command string, and the `ssh` call is inside the script. `deploy-gate.sh` now also asks before *running* `scripts/tunnel.sh` or `bootstrap_vllm.sh`, but not before reading them, and `eval.sh` has a pair for it.
- **`.claude/settings.json`:** unchanged. The `.env` read deny already covers the new secrets.
- **CI:** no new job. `ci.yml` runs the new tests, and `agent-config-eval` is verified present, exactly once.
- **Subagents:** none.

## Rollback

- **Code:** all changes are on `feature/p1-infrastructure`. Drop the branch or revert its commits.
- **Local stack:** `podman compose down -v` removes the containers and volumes.
- **RunPod:** terminate the pod. Nothing persists after termination.
