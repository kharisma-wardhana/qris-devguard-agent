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
    tools = ToolClient(args.mcp_url, secret)
    db_info = _database()
    if db_info:
        # p6: durable threads and approvals. The checkpointer needs a running loop, so it opens in the lifespan.
        import contextlib

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        @contextlib.asynccontextmanager
        async def lifespan(app_):
            async with AsyncPostgresSaver.from_conn_string(db_info) as saver:
                app_.state.agent = Agent(tools, llm, checkpointer=saver, db_conninfo=db_info)
                yield

        app = create_app(agent=None, secret=secret, mcp_ping=lambda: http_ping(args.mcp_url), llm_ping=llm_ping,
                         db_conninfo=db_info, lifespan=lifespan)
        mode = "PostgreSQL checkpointer, approvals on"
    else:
        app = create_app(agent=Agent(tools, llm), secret=secret, mcp_ping=lambda: http_ping(args.mcp_url),
                         llm_ping=llm_ping)
        mode = "no database reachable: in-memory, approvals off (run scripts/db_migrate.py with Postgres up)"
    print(f"API on http://{api['host']}:{args.port} (loopback only; {mode})", flush=True)
    # Selector loop on every platform: psycopg async (the PostgreSQL checkpointer) cannot run on Windows' Proactor
    # loop, which uvicorn's "asyncio" setting picks on win32 (p6).
    uvicorn.run(app, host=api["host"], port=args.port, log_level="warning", access_log=False,
                loop="asyncio:SelectorEventLoop")
    return 0


def _database() -> str | None:
    """The conninfo when PostgreSQL is reachable and migrated (agent_threads exists), else None. Never printed."""
    import psycopg

    from app.db.connect import DatabaseNotConfigured, conninfo
    try:
        info = conninfo()
        with psycopg.connect(info) as c:
            if c.execute("select to_regclass('agent_threads')").fetchone()[0] is None:
                return None
        return info
    except (DatabaseNotConfigured, psycopg.Error):
        return None


if __name__ == "__main__":
    sys.exit(main())
