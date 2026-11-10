"""Run the scripted agent flows (TRD §22.3; p5 spec AC #11-#16).

Usage:
  uv run python scripts/eval_agent.py                       # CI mode: MCP harness + fake LLM (no services)
  uv run python scripts/eval_agent.py --live                # live: real MCP server, Qdrant, vLLM (tunnel)
  uv run python scripts/eval_agent.py --live --api-smoke    # plus one request through the running API

Flows pass on properties (intent, route, tools, verdicts, policy outcomes, contract, citations), never on
wording. Writes evals/reports/agent-flows-<timestamp>.json (IDs, statuses, timings, token counts; no answer
text). Exit 0 only if every flow run in this mode passes.
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATASET = ROOT / "evals" / "datasets" / "agent_flows.yaml"


def merged(flow: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode != "live" or "live" not in flow:
        return flow
    live = flow["live"]
    return {**flow, "request": live.get("request", flow["request"]), "expect": {**flow["expect"], **live.get("expect", {})}}


def check(flow: dict[str, Any], out: dict[str, Any]) -> list[str]:
    """Names of the expected properties that do not hold. Empty means the flow passed."""
    from app.agent.answer import SECTIONS
    from app.agent.policy import claims_valid_signature

    exp, a, fails = flow["expect"], out["answer"], []
    if not set(SECTIONS) <= set(a):
        fails.append("all_sections")
    source_ids = {s["citation_id"] for s in a.get("sources", [])}
    cited = {c for f in a.get("findings", []) for c in f.get("citation_ids", [])} | \
            {c for s in a.get("next_steps", []) for c in s.get("citation_ids", [])}
    if not cited <= source_ids:
        fails.append("citations_in_sources")
    for k in ("intent", "route"):
        if k in exp and out[k] != exp[k]:
            fails.append(f"{k}={out[k]}")
    if "tools" in exp and [t["tool"] for t in out["tool_calls"]] != exp["tools"]:
        fails.append("tools")
    vr = a.get("validation_results") or [{}]
    if "validator" in exp:
        v = exp["validator"]
        if vr[0].get("result") != v["result"] or ("details" in v and vr[0].get("details") != v["details"]):
            fails.append("validator")
    findings = a.get("findings", [])
    if exp.get("confirmed_on_validator") and not any(f["status"] == "confirmed" and 0 in f["validator_refs"] for f in findings):
        fails.append("confirmed_on_validator")
    if exp.get("no_confirmed") and any(f["status"] == "confirmed" for f in findings):
        fails.append("no_confirmed")
    if "no_confirmed_about_code" in exp:
        code = exp["no_confirmed_about_code"]
        if any(f["status"] == "confirmed" and (code in f["text"] or 0 in f["validator_refs"]) for f in findings):
            fails.append("no_confirmed_about_code")
    if exp.get("no_valid_signature_claim"):
        texts = [a.get("summary", "")] + [f["text"] for f in findings] + [s["text"] for s in a.get("next_steps", [])]
        if any(claims_valid_signature(t) for t in texts):
            fails.append("no_valid_signature_claim")
    if "summary_startswith" in exp and not a.get("summary", "").startswith(exp["summary_startswith"]):
        fails.append("summary_startswith")
    for s in exp.get("summary_contains", []):
        if s not in a.get("summary", ""):
            fails.append(f"summary_contains:{s}")
    if exp.get("version_conflict"):
        versions: dict[str, set[str]] = {}
        for s in a.get("sources", []):
            versions.setdefault(s["document_id"], set()).add(s["document_version"])
        if not (out["evidence_flags"].get("version_conflict") and any(len(v) > 1 for v in versions.values())
                and any("more than one version" in lim for lim in a.get("limitations", []))):
            fails.append("version_conflict")
    if "sources_include" in exp:
        si = exp["sources_include"]
        if not any(s["document_id"] == si["document_id"] and str(s.get("section") or "").startswith(si["section_prefix"])
                   for s in a.get("sources", [])):
            fails.append("sources_include")
    return fails


def summary(flow: dict[str, Any], out: dict[str, Any], fails: list[str]) -> dict[str, Any]:
    a = out["answer"]
    return {"id": flow["id"], "passed": not fails, "failures": fails, "intent": out["intent"], "route": out["route"],
            "risk_level": out["risk_level"],
            "tools": [{"tool": t["tool"], "outcome": t["outcome"], "ms": t["ms"]} for t in out["tool_calls"]],
            "validation_results": [{"tool": v["tool"], "result": v["result"]} for v in a.get("validation_results", [])],
            "finding_statuses": [f["status"] for f in a.get("findings", [])],
            "sources": [{"document_id": s["document_id"], "document_version": s["document_version"],
                         "section": s.get("section"), "citation_id": s["citation_id"]} for s in a.get("sources", [])],
            "policy_rules": sorted({p["rule"] for p in out["policy_actions"]}), "policy_actions": len(out["policy_actions"]),
            "confidence": a.get("confidence"), "evidence_flags": {k: v for k, v in out["evidence_flags"].items()
                                                                   if k != "version_mismatch"},
            "trace": out["trace"]}


async def run_flows(flows: list[dict[str, Any]], agent: Any, mode: str) -> list[dict[str, Any]]:
    results = []
    for raw in flows:
        if mode not in raw.get("modes", ["ci", "live"]):
            results.append({"id": raw["id"], "passed": None, "skipped": f"not run in {mode} mode"})
            continue
        flow = merged(raw, mode)
        req = flow["request"]
        out = await agent.run(sub="eval-agent", role=flow.get("role", "developer"), message=req["message"],
                              context=req.get("context"))
        results.append(summary(flow, out, check(flow, out)))
    return results


async def api_smoke(secret: str) -> dict[str, Any]:
    import httpx

    from app.agent import config
    from app.mcp_server.auth import mint
    api = config.agent()["api"]
    base = f"http://{api['host']}:{api['port']}"
    token = mint("eval-agent", "developer", secret=secret, audience=api["audience"], ttl_minutes=5)
    async with httpx.AsyncClient(timeout=300) as c:
        ready = await c.get(f"{base}/health/ready")
        r = await c.post(f"{base}/api/v1/chat", headers={"Authorization": f"Bearer {token}"},
                         json={"message": "What does 4044700 mean?",
                               "context": {"api_family": "qris-mpm", "spec_version": "0.1.0", "response_code": "4044700"}})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return {"ready_status": ready.status_code, "ready_checks": ready.json().get("checks"), "chat_status": r.status_code,
            "chat_result": [v["result"] for v in body.get("answer", {}).get("validation_results", [])]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scripted agent flows.")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--live", action="store_true", help="real MCP server, Qdrant and vLLM")
    parser.add_argument("--api-smoke", action="store_true", help="with --live: one request through the running API")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals" / "reports")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    import logging
    for noisy in ("mcp", "httpx", "httpx2", "devguard.mcp", "devguard.agent", "openai", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from app.agent.graph import Agent
    from app.agent.tools import ToolClient

    try:
        data = yaml.safe_load(args.dataset.read_text(encoding="utf-8"))
        flows = data["flows"]
    except Exception as exc:  # noqa: BLE001
        print(f"error: dataset unreadable: {type(exc).__name__}", file=sys.stderr)
        return 1
    mode = "live" if args.live else "ci"
    smoke = None
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
        results = asyncio.run(run_flows(flows, Agent(ToolClient(url, secret), llm), mode))
        if args.api_smoke:
            smoke = asyncio.run(api_smoke(secret))
    else:
        from app.agent.harness import McpHarness
        from app.agent.llm import FakeLLM
        with McpHarness() as h:
            results = asyncio.run(run_flows(flows, Agent(ToolClient(h.url, h.secret), FakeLLM()), mode))

    ran = [r for r in results if r["passed"] is not None]
    ok = bool(ran) and all(r["passed"] for r in ran)
    for r in results:
        status = "SKIP" if r["passed"] is None else ("PASS" if r["passed"] else "FAIL")
        extra = r.get("skipped") or (f"total {r['trace'].get('total_ms')} ms" + (f"  failed: {', '.join(r['failures'])}"
                                                                              if r["failures"] else ""))
        print(f"{status}  {r['id']:<36} {extra}")
    if smoke is not None:
        print(f"API smoke: ready={smoke['ready_status']} {smoke['ready_checks']} chat={smoke['chat_status']} "
              f"{smoke['chat_result']}")
    report = {"mode": mode, "dataset_version": data.get("dataset_version"), "passed": ok,
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "flows": results, "api_smoke": smoke}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"agent-flows-{mode}-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"{len([r for r in ran if r['passed']])}/{len(ran)} flows passed ({mode} mode)  report: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    sys.exit(main())
