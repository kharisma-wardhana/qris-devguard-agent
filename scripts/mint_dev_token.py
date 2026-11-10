"""Print a short-lived bearer token for the local MCP server.

Usage:
  uv run python scripts/mint_dev_token.py --sub dev-1 --role developer [--ttl 30]

Roles come from configs/security.yaml; the TTL is capped by configs/mcp.yaml (60 min).
The token is printed to stdout only. Treat it as a secret: don't paste it into chats, docs or logs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.mcp_server import config
    from app.mcp_server.auth import AuthConfigError, load_secret, mint

    parser = argparse.ArgumentParser(description="Mint a short-lived MCP bearer token.")
    parser.add_argument("--sub", required=True)
    parser.add_argument("--role", required=True, choices=sorted(config.roles()))
    parser.add_argument("--ttl", type=int, default=None, help="minutes")
    args = parser.parse_args(argv)
    try:
        print(mint(args.sub, args.role, secret=load_secret(), ttl_minutes=args.ttl))
    except (AuthConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
