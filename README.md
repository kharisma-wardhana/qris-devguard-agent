# QRIS DevGuard AI

A self-hosted agentic assistant for diagnosing **sandbox** QRIS/SNAP API integration issues. It covers signatures, timestamps, payloads and response codes, and its answers are backed by cited documentation and deterministic validators. It is a capstone project: synthetic data only, with no production connectivity.

- Requirements: [`docs/QRIS_DevGuard_AI_PRD_TRD.md`](docs/QRIS_DevGuard_AI_PRD_TRD.md)
- Phase P0 scope and boundaries: [`docs/scope.md`](docs/scope.md)
- Threat model: [`docs/threat-model.md`](docs/threat-model.md)
- SOP skill: [`skills/qris-integration-troubleshooting/SKILL.md`](skills/qris-integration-troubleshooting/SKILL.md)
- Source inventory: [`knowledge_base/manifests/sources.yaml`](knowledge_base/manifests/sources.yaml)
- Running the stack (local services + vLLM on RunPod): [`docs/runpod-deployment.md`](docs/runpod-deployment.md)

## Status

Phase P0 (scope and source inventory) is signed off. Phase P1 (infrastructure) is in progress: see `docs/p1-infrastructure/`.

## Development

```bash
uv sync                                   # install deps (.venv)
uv run pytest                             # all tests
uv run python scripts/check_sources.py    # validate the source manifest
uv run python scripts/init_env.py         # once: create .env with random secrets
podman compose up -d                      # local PostgreSQL, Qdrant, Langfuse
uv run python scripts/healthcheck.py      # PASS/FAIL per service
```
