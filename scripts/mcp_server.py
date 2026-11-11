"""Run the P4 MCP server (Streamable HTTP, bearer auth) on loopback.

Usage:
  uv run python scripts/gen_test_keys.py            # once: test keys for validate_test_signature
  uv run python scripts/mcp_server.py               # http://127.0.0.1:8765/mcp (configs/mcp.yaml)
  uv run python scripts/mint_dev_token.py --sub dev-1 --role developer   # a token for a client

Needs MCP_AUTH_SECRET (environment or .env from scripts/init_env.py). Binds 127.0.0.1 only.
search_qris_spec also needs Qdrant and the embedding stack (`uv sync --extra embed`); without them it
answers SEARCH_UNAVAILABLE and the three validators keep working.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.mcp_server import config
    from app.mcp_server.auth import AuthConfigError, load_secret
    from app.db.roles import RolePasswordMissing
    from app.mcp_server.server import build_app, build_server
    from app.mcp_server.tools import default_context

    cfg = config.mcp()["server"]
    parser = argparse.ArgumentParser(description="Run the QRIS DevGuard MCP server on loopback.")
    parser.add_argument("--port", type=int, default=cfg["port"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("mcp", "httpx", "uvicorn.access"):           # SDK/HTTP request logs can carry argument text
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        secret = load_secret()
    except AuthConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    import uvicorn

    try:
        ctx = default_context()                         # p7: ticket and audit roles, never the owner
    except RolePasswordMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    app = build_app(build_server(secret, ctx, port=args.port), port=args.port, audit=ctx.audit)
    print(f"MCP server on http://{cfg['host']}:{args.port}{cfg['path']} (loopback only)", flush=True)
    uvicorn.run(app, host=cfg["host"], port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
