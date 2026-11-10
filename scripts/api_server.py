"""Run the /api/v1 app on loopback (p5 spec).

Usage:
  uv run python scripts/mcp_server.py                     # the agent's tools (other terminal)
  bash scripts/tunnel.sh                                  # vLLM on the RunPod pod (other terminal)
  uv run python scripts/api_server.py                     # http://127.0.0.1:8000
  uv run python scripts/mint_dev_token.py --sub dev-1 --role developer --audience api   # a client token

Needs MCP_AUTH_SECRET, VLLM_BASE_URL and VLLM_API_KEY (environment or .env). Binds 127.0.0.1 only.
`--fake-llm` (tests only) is refused unless DEVGUARD_TEST_BACKENDS=1.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_env_file() -> None:
    """Fill VLLM_* from the repo's .env when they are not in the environment (values are never printed)."""
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("VLLM_BASE_URL", "VLLM_API_KEY") and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip()


def main(argv: list[str] | None = None) -> int:
    from app.agent import config
    from app.mcp_server.auth import AuthConfigError, load_secret

    api = config.agent()["api"]
    parser = argparse.ArgumentParser(description="Run the QRIS DevGuard API on loopback.")
    parser.add_argument("--port", type=int, default=api["port"])
    parser.add_argument("--mcp-url", default=config.agent()["tools"]["mcp_url"])
    parser.add_argument("--fake-llm", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.fake_llm and os.environ.get("DEVGUARD_TEST_BACKENDS") != "1":
        print("error: --fake-llm is for tests only (set DEVGUARD_TEST_BACKENDS=1)", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("mcp", "httpx", "httpx2", "uvicorn.access", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        secret = load_secret()
    except AuthConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _load_env_file()

    import uvicorn

    from app.agent.graph import Agent
    from app.agent.tools import ToolClient
    from app.api.app import create_app, http_ping

    if args.fake_llm:
        from app.agent.llm import FakeLLM
        llm: object = FakeLLM()

        async def llm_ping() -> bool:
            return True
    else:
        from app.agent.llm import VllmLLM
        llm = VllmLLM()
        llm_ping = llm.ping
    app = create_app(agent=Agent(ToolClient(args.mcp_url, secret), llm), secret=secret,
                     mcp_ping=lambda: http_ping(args.mcp_url), llm_ping=llm_ping)
    print(f"API on http://{api['host']}:{args.port} (loopback only)", flush=True)
    uvicorn.run(app, host=api["host"], port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
