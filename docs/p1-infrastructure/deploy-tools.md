# Deploy, status and rollback: p1-infrastructure
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-07. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to an MCP server or a credential. The full session procedure is `docs/runpod-deployment.md`.

## This feature

| Action | How | Who |
|---|---|---|
| Deploy (local stack) | `podman compose up -d` | Developer, on their own machine |
| Deploy (GPU) | Create the pod in the RunPod console, then run `bootstrap_vllm.sh` fetched by commit SHA | Human (budget owner) |
| Status | `uv run python scripts/healthcheck.py` (7 PASS/FAIL lines), `podman compose ps`, the RunPod console | Anyone with local access |
| Rollback (GPU) | Terminate the pod. Nothing persists on it | Human (budget owner) |
| Rollback (local) | `podman compose down`. Adding `-v` also deletes the local data | Developer |
| Rollback (code) | `git revert` the merge or step commits, then push | Human (repo owner) |

## Scoped MCP tools

The `runpod_deploy`, `runpod_status` and `runpod_rollback` proposals in `docs/scope-and-source-inventory/deploy-tools.md` still apply. P1 adds one:

| Tool | Does | Narrowest scope needed | Approval |
|---|---|---|---|
| `stack_status` | Runs the 7 health checks and returns the PASS/FAIL list and latencies, never bodies, hosts or secrets | Read access to the local `.env` values the health check already uses; no RunPod credential | Not required (read-only, no arguments) |

`stack_status` takes no arguments: no URL, host or command (TRD §13.3).
