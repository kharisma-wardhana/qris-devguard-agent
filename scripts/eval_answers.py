"""Golden-answer run (docs/p9-final-evaluation-packaging/spec.md Behaviour §2.3; AC #8, #13).

Usage:
  uv run python scripts/eval_answers.py --live                 # real MCP server, Qdrant, vLLM (pod + tunnel)
  uv run python scripts/eval_answers.py                        # CI mode: MCP harness + fake LLM (no services)
  uv run python scripts/eval_answers.py --allow-unreviewed     # CI mode only: also run items not yet reviewed

Each golden item goes through the real agent. The answers file (evals/reports/answers-<mode>-<ts>.json, git-ignored)
holds the redacted answer, its citations with excerpts capped at 300 characters, and three deterministic checks:
citation validity, fabricated crypto guidance and a secret scan. Answer support is graded later by
scripts/judge_answers.py. In --live mode only reviewed items run; the report states how many.

Exit 0 when every cited ID was retrieved in its run and there is no fabricated crypto guidance and no secret finding;
1 otherwise or on a setup error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def exit_code(summary: dict[str, Any]) -> int:
    clean = (summary["citations_valid"] == summary["citations_total"] and summary["fabricated_crypto"] == 0
             and summary["secret_findings"] == 0)
    return 0 if clean and summary["items"] > 0 else 1


async def run_items(items: list[dict[str, Any]], agent: Any, client: Any) -> list[dict[str, Any]]:
    from app.evaluation import golden

    records = []
    for item in items:
        client.retrieved = set()
        out = await agent.run(sub="eval-answers", role="developer", message=item["question"],
                              context=item.get("context"))
        rec = golden.build_record(item, out, set(client.retrieved))
        records.append(rec)
        c = rec["checks"]
        flags = [f for f, bad in (("citations", c["citations"]["invalid_ids"]), ("crypto", c["fabricated_crypto"]),
                                  ("secret", c["secret_findings"])) if bad]
        print(f"{rec['id']}  {rec['observed_behaviour']:<9} expected {rec['expected_behaviour']:<9} "
              f"citations {c['citations']['valid']}/{c['citations']['total']}"
              + (f"  CHECK FAILED: {', '.join(flags)}" if flags else ""), flush=True)
    return records


def main(argv: list[str] | None = None) -> int:
    from app.evaluation import golden, settings

    parser = argparse.ArgumentParser(description="Run the golden-answer items through the agent.")
    parser.add_argument("--dataset", type=Path, default=ROOT / settings()["golden"]["dataset"])
    parser.add_argument("--live", action="store_true", help="real MCP server, Qdrant and vLLM")
    parser.add_argument("--allow-unreviewed", action="store_true", help="CI mode only: run unreviewed items too")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N items (smoke runs)")
    parser.add_argument("--report-dir", type=Path, default=ROOT / settings()["reports_dir"])
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    import logging
    for noisy in ("mcp", "httpx", "httpx2", "devguard.mcp", "devguard.agent", "openai", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.live and args.allow_unreviewed:
        print("error: --allow-unreviewed is for CI mode only; live results count reviewed items only", file=sys.stderr)
        return 1
    try:
        data = golden.load_dataset(args.dataset)
        errors = golden.schema_errors(data)
    except Exception as exc:  # noqa: BLE001
        print(f"error: dataset unreadable: {type(exc).__name__}", file=sys.stderr)
        return 1
    if errors:
        print(f"error: dataset fails its schema ({len(errors)} errors); first: {errors[0]}", file=sys.stderr)
        return 1
    items = data["items"]
    pending = set(golden.unreviewed(items))
    run = [i for i in items if args.allow_unreviewed or i["id"] not in pending]
    if args.limit:
        run = run[: args.limit]
    if not run:
        print(f"error: no reviewed items to run ({len(pending)} of {len(items)} not reviewed)", file=sys.stderr)
        return 1

    from app.agent.graph import Agent

    mode = "live" if args.live else "ci"
    if args.live:
        sys.path.insert(0, str(ROOT / "scripts"))
        from api_server import _load_env_file  # same .env handling as the API

        from app.agent import config
        from app.agent.llm import VllmLLM
        from app.api.app import http_ping
        from app.mcp_server.auth import AuthConfigError, load_secret

        try:
            secret = load_secret()
        except AuthConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _load_env_file()
        llm = VllmLLM()
        url = config.agent()["tools"]["mcp_url"]
        down = [n for n, ok in (("mcp server", asyncio.run(http_ping(url))), ("vLLM", asyncio.run(llm.ping()))) if not ok]
        if down:
            print(f"error: not reachable: {', '.join(down)} (start scripts/mcp_server.py and scripts/tunnel.sh)",
                  file=sys.stderr)
            return 1
        client = golden.recording_client(url, secret)
        records = asyncio.run(run_items(run, Agent(client, llm), client))
    else:
        from app.agent.harness import McpHarness
        from app.agent.llm import FakeLLM
        with McpHarness(rate_limits=False) as h:      # behaviour run, not a limiter test (CLAUDE.md)
            client = golden.recording_client(h.url, h.secret)
            records = asyncio.run(run_items(run, Agent(client, FakeLLM()), client))

    summary = golden.summarise(records)
    report = {"mode": mode, "dataset_version": data.get("dataset_version"),
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "items_total": len(items), "items_run": len(records), "unreviewed_skipped": len(
                  [i for i in items if i["id"] in pending and not args.allow_unreviewed]),
              "summary": summary, "records": records}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"answers-{mode}-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    cv = summary["citation_validity"]
    print(f"{summary['items']} items ({mode} mode): behaviour matched {summary['behaviour_matches']}, "
          f"citation validity {'n/a' if cv is None else cv}, fabricated crypto {summary['fabricated_crypto']}, "
          f"secret findings {summary['secret_findings']}, tool errors {summary['tool_errors']}  report: {out}")
    return exit_code(summary)


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    sys.exit(main())
