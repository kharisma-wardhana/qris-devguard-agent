"""Run the Streamlit demo UI on loopback (docs/p9-demo-ui-alerts/spec.md Behaviour §7; AC #19).

Usage:
  uv sync --extra ui                                      # once: Streamlit
  uv run python scripts/api_server.py                     # the API it calls (other terminal; needs the MCP server)
  uv run python scripts/ui_server.py                      # http://127.0.0.1:8501

Binds 127.0.0.1 only (configs/agent.yaml ui.host; anything else is refused) with Streamlit's usage statistics off.
The UI mints short-lived demo tokens from MCP_AUTH_SECRET (environment or .env): demo-only, see the p9 runbook.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
APP = ROOT / "app" / "ui" / "streamlit_app.py"


def command(host: str, port: int) -> list[str]:
    """Streamlit's own argv. Run in this process (not a child), so stopping this process stops the UI."""
    return ["streamlit", "run", str(APP),
            "--server.address", host, "--server.port", str(port), "--server.headless", "true",
            "--browser.gatherUsageStats", "false", "--server.enableCORS", "true",
            "--server.enableXsrfProtection", "true", "--global.developmentMode", "false",
            "--server.fileWatcherType", "none", "--client.toolbarMode", "minimal"]


def main(argv: list[str] | None = None) -> int:
    from app.agent import config

    ui = config.agent()["ui"]
    if ui["host"] != "127.0.0.1":
        print("error: configs/agent.yaml ui.host must be 127.0.0.1 (loopback only)", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description="Run the QRIS DevGuard demo UI on loopback.")
    parser.add_argument("--port", type=int, default=ui["port"])
    parser.add_argument("--api-url", default=ui["api_url"], help="the /api/v1 base URL (loopback)")
    args = parser.parse_args(argv)
    if not args.api_url.startswith("http://127.0.0.1:"):
        print("error: --api-url must be a loopback URL (http://127.0.0.1:<port>)", file=sys.stderr)
        return 1
    if importlib.util.find_spec("streamlit") is None:
        print("error: Streamlit is not installed; run: uv sync --extra ui", file=sys.stderr)
        return 1
    from app.mcp_server.auth import AuthConfigError, load_secret
    try:
        load_secret()                                   # fail early; the value is never printed
    except AuthConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    os.environ["DEVGUARD_UI_API_URL"] = args.api_url
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.chdir(ROOT)
    print(f"demo UI on http://{ui['host']}:{args.port} using API {args.api_url} (usage statistics off)", flush=True)
    from streamlit.web import cli as stcli
    sys.argv = command(ui["host"], args.port)
    try:
        return stcli.main(standalone_mode=False) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
