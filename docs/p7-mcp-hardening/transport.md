# Transport: TLS for a future public ingress
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p7-mcp-hardening/spec.md, Behaviour §7 (Transport) and AC #23*

**Status: documented, not deployed.** P7 has no public ingress and no TLS certificate (spec: Out of scope). This note says how one would be added without weakening the controls that P7 measured. What is measured today is the loopback-only binding (case G-TRANSPORT in `security-report.md`).

## Today

Every listener binds `127.0.0.1`, and no service is reachable from another machine.

| Service | Bind | Pinned by |
|---|---|---|
| API (`/api/v1`, `/health/*`) | `127.0.0.1:8000` (`configs/agent.yaml`) | `test_compose.py`; the API refuses a non-loopback host at start-up |
| MCP server (`/mcp`) | `127.0.0.1:8765` (`configs/mcp.yaml`) | `test_compose.py`; the server refuses a non-loopback host |
| PostgreSQL, Qdrant, Langfuse, ClickHouse, MinIO, Redis | `127.0.0.1` ports in `docker-compose.yml` | `test_compose.py` (every published port) |
| vLLM on the GPU pod | the pod's `127.0.0.1:8080`, reached by SSH tunnel | `test_bootstrap_vllm.py`; T13, T18 |

Both servers run uvicorn with `proxy_headers=False`. uvicorn otherwise trusts `X-Forwarded-For` from loopback, which is every caller here, so a local client could rotate the header past the per-address rate limit (found and fixed in P7 step 11, with regression tests).

## If the API is ever exposed

```text
internet ──HTTPS:443──► reverse proxy (TLS) ──HTTP──► 127.0.0.1:8000  API only
                                                      127.0.0.1:8765  MCP     never proxied
                                                      everything else         never proxied
```

1. **Only the API is exposed**, and only `/api/v1/*` and `/health/live`. Keep `/health/ready` internal, because it reports dependency state. The MCP server, databases, Langfuse and vLLM are never proxied. The agent reaches MCP on loopback, and MCP tokens have their own audience, which the API never accepts.
2. **TLS terminates at the proxy** (Caddy or nginx on the same host):
   - TLS 1.2 or newer, with modern cipher suites;
   - a certificate from an ACME CA;
   - plain HTTP redirected to HTTPS, and HSTS set;
   - the proxy talks to the API over loopback, so the plain-HTTP hop never leaves the machine.
3. **Client address and rate limits.** Behind a proxy, the API sees every request as coming from the proxy's address, so the per-address pre-auth bucket would become one global bucket. Either:
   - enable forwarded headers for **exactly** the proxy's address (`proxy_headers=True`, `forwarded_allow_ips=<proxy>`), and have the proxy **overwrite**, not append, `X-Forwarded-For`; or
   - rate-limit by address at the proxy and keep the API's per-subject limits.

   Never trust forwarded headers from any address that clients can reach directly. That is the step 11 finding.
4. **Size and time limits at the proxy too.** Set the request body cap to at most `limits.max_request_bytes` (`configs/security.yaml`) and give it a read timeout. The API's own 413 stays as the second layer.
5. **Egress is unchanged.** The proxy adds an inbound path only. Outbound destinations remain the start-up-checked `egress_allowlist`, and no client follows redirects.
6. **Before exposing it:** the threat model's single-person residual risk (no separation of duties) and the per-process limiter (spec Flagged concerns) must be revisited. Exposure is a new decision, not a configuration change.

## Evidence

- Measured: case G-TRANSPORT (`test_compose.py`, `test_api_listens_on_loopback_only`), and the `X-Forwarded-For` regression tests in case S05.
- Not measured: anything in "If the API is ever exposed". No proxy exists, so the §13.4 Transport security control is graded **PARTIAL** in `security-report.md`.
