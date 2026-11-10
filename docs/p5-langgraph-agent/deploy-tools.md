# Deploy, status and rollback: p5-langgraph-agent
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to an MCP server or a credential beyond the local, loopback-only API, the P4 MCP server, and the SSH tunnel to the user's own RunPod pod.

## This feature

The agent persists nothing in P5 (in-memory checkpointer). "Deploying" it means the MCP server, the vLLM tunnel and the API are running on the developer machine.

| Action | How | Who |
|---|---|---|
| Deploy (local) | `podman compose up -d qdrant`, then `uv run python scripts/mcp_server.py`. Start the pod per `docs/runpod-deployment.md` and run `bash scripts/tunnel.sh` in your own terminal. Then `uv run python scripts/api_server.py` | Developer (pod: human, it costs money) |
| Use | `uv run python scripts/mint_dev_token.py --sub <id> --role <role> --audience api`, then `POST http://127.0.0.1:8000/api/v1/chat` with `Authorization: Bearer <token>` | Developer |
| Status | `GET /health/ready` reports `mcp` and `llm` up or down (503 if either is down). `uv run python scripts/eval_agent.py` checks the flows without services; `--live --api-smoke` checks them against the real stack | Developer |
| Rollback (code) | `git revert` the merge or the step commits. P4 tools, validators and search do not depend on P5. The only shared-code change (the token `audience` parameter) defaults to P4 behaviour | Human (repo owner) |
| Rollback (data) | Nothing to delete: no state outlives the API process | — |
| End a live session | Stop the tunnel, then **terminate** the RunPod pod (stopping isn't enough) | Human |

## Scoped MCP tools

The deploy/status/rollback tools proposed by earlier features still apply. P5 proposes one more. It is **documented, not wired**:

| Tool | Scope it would need | Never |
|---|---|---|
| `agent_flows_status` (read-only) | Run `scripts/eval_agent.py` in CI mode (harness + fake LLM); return pass/fail per flow and the dataset version | Start a pod, open a tunnel, call vLLM, or return answer text |

No tool may start or terminate a RunPod pod or open the tunnel. Those stay human actions, because they cost money and cross the T13/T18 boundary.
