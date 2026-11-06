# Deploy, status and rollback: scope-and-source-inventory
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to an MCP server or a credential.

## This feature

This feature ships documents, a manifest, a checker script and tests. There is no running service.

| Action | How | Who |
|---|---|---|
| Deploy | Merge the PR into `main` | Human (repo owner) |
| Status | The GitHub Actions `ci` run on `main` | Anyone with read access |
| Rollback | `git revert` the merge commit, or the step commits, then push | Human (repo owner) |

## Scoped MCP tools for later phases (P1 onward)

These are proposals for when there is something to deploy: the vLLM GPU pod and the app stack. Each tool needs its own credential with the narrowest scope that works. All of them are behind human approval (`configs/security.yaml` risk `high`).

| Tool | Does | Narrowest scope needed | Approval |
|---|---|---|---|
| `runpod_deploy` | Start or update the one project pod from a pinned image digest | RunPod API key limited to that project's pods; image allowlist | Required |
| `runpod_status` | Read pod state, GPU type, uptime and health endpoint result | Read-only pod access | Not required |
| `runpod_rollback` | Redeploy the previous pinned image digest, or stop the pod | Same as `runpod_deploy` | Required |

None of these may take an arbitrary URL, image name or shell command (TRD §13.3).
