"""Answer assembly in the PRD §5 shape (p5 spec; AC #2).

The model writes summary, findings, next steps and limitations (checked by app.agent.policy). Everything
here is built by code from tool output: validation results, sources and confidence, plus the fixed
answers for refusal, draft-only, clarification, abstention and the deterministic fallback.
"""

from __future__ import annotations

from typing import Any

SECTIONS = ("summary", "findings", "validation_results", "next_steps", "sources", "confidence", "limitations")
VALIDATORS = ("validate_test_signature", "validate_request_payload", "lookup_response_code")
KEY_WARNING = ("Secret-looking material (a private key, token or secret field) was removed from your request. "
               "Never send real keys, tokens or signatures; use sandbox fixture IDs instead.")


def decisive(r: dict[str, Any]) -> bool:
    """A validator result that settles its question: VALID/INVALID, payload valid true/false, FOUND/NOT_FOUND."""
    if not r.get("ok"):
        return False
    d = r.get("data", {})
    if r["tool"] == "validate_test_signature":
        return d.get("status") in ("VALID", "INVALID")
    if r["tool"] == "validate_request_payload":
        return isinstance(d.get("valid"), bool)
    if r["tool"] == "lookup_response_code":
        return d.get("status") in ("FOUND", "NOT_FOUND")
    return False


def validation_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in results:
        d, tool = r.get("data", {}), r["tool"]
        if not r.get("ok"):
            out.append({"tool": tool, "reference": None, "result": d.get("code", "TOOL_UNAVAILABLE"),
                        "details": [], "limitations": ["The tool did not return a verdict; nothing is concluded from it."]})
        elif tool == "validate_test_signature":
            out.append({"tool": tool, "reference": {"test_case": d.get("test_payload_ref"), "fixture_id": d.get("fixture_id"),
                                                     "profile": d.get("profile")},
                        "result": d.get("status"), "details": [d.get("diagnostic_code")],
                        "limitations": list(d.get("assumptions", [])) + ["Synthetic test material only."]})
        elif tool == "validate_request_payload":
            out.append({"tool": tool, "reference": {"endpoint_id": d.get("endpoint_id"), "schema_id": d.get("schema_id"),
                                                     "rules_version": d.get("rules_version")},
                        "result": "valid" if d.get("valid") else "invalid",
                        "details": [f"{e['code']} at {e['pointer']}" for e in d.get("errors", [])],
                        "limitations": [f"{w['code']} at {w['pointer']}" for w in d.get("warnings", [])]})
        elif tool == "lookup_response_code":
            m = d.get("match") or {}
            out.append({"tool": tool, "reference": {"code": d.get("code"), "api_family": d.get("api_family"),
                                                     "spec_version": d.get("spec_version")},
                        "result": d.get("status"),
                        "details": [m["message"]] if d.get("status") == "FOUND" and m.get("message") else [],
                        "limitations": ["Suggestions are similar codes, not matches."] if d.get("suggestions") else []})
    return out


def sources(evidence: list[dict[str, Any]], cited: set[str], version_conflict: bool) -> list[dict[str, Any]]:
    by_id = {c["citation_id"]: c for c in evidence}
    keep = [by_id[c] for c in sorted(cited) if c in by_id]
    if version_conflict:                       # keep every version of a conflicting document, unmerged
        docs = {c["document_id"] for c in keep}
        versions: dict[str, set[str]] = {}
        for c in evidence:
            versions.setdefault(c["document_id"], set()).add(c["document_version"])
        for c in evidence:
            if c["document_id"] in docs and len(versions[c["document_id"]]) > 1 and c not in keep:
                if c["document_version"] not in {k["document_version"] for k in keep if k["document_id"] == c["document_id"]}:
                    keep.append(c)
    return [{"citation_id": c["citation_id"], "title": c.get("title"), "document_id": c["document_id"],
             "document_version": c["document_version"], "section": c.get("section"), "page": c.get("page"),
             "source_url": c.get("source_url"), "quote_or_excerpt": c.get("quote_or_excerpt"),
             "synthetic": c.get("permission_basis") == "team_authored"} for c in keep]


def confidence(flags: dict[str, Any], results: list[dict[str, Any]], fallback: bool) -> str:
    if fallback or flags.get("no_evidence") and not any(decisive(r) for r in results) or flags.get("search_unavailable"):
        return "low"
    if any(decisive(r) for r in results) and not flags.get("version_conflict"):
        return "high"
    return "medium"


def empty(summary: str, limitations: list[str], next_steps: list[str] | None = None,
          confidence_level: str = "low") -> dict[str, Any]:
    return {"summary": summary, "findings": [], "validation_results": [],
            "next_steps": [{"text": s, "citation_ids": []} for s in (next_steps or [])],
            "sources": [], "confidence": confidence_level, "limitations": limitations}


def refusal() -> dict[str, Any]:
    return empty("This request asks for an action that QRIS DevGuard never performs (payments, refunds, reversals, "
                 "settlement, merchant activation, production calls or production credentials). No tool was run.",
                 ["Prohibited in the MVP (TRD §15.1); this is a sandbox diagnostic assistant."])


def draft_only(message: str) -> dict[str, Any]:
    out = empty("This request would create or change something. P5 only drafts it; nothing was executed.",
                ["Human approval and the sandbox ticket tool arrive in P6. No side effect happened."],
                ["Review the draft below; once approval exists (P6), submit it through the approval flow."])
    out["draft"] = {"action": "create_support_ticket", "status": "draft_only", "summary": message[:300]}
    return out


def clarification(missing: list[str], note: str | None = None) -> dict[str, Any]:
    lines = [f"Please provide: {', '.join(missing)}."] if missing else []
    return empty(note or "More information is needed before any check can run.",
                 ["No validator was run, so nothing is concluded yet."] + lines,
                 [f"Add {m} to the request context." for m in missing], confidence_level="low")


def abstention(reason: str) -> dict[str, Any]:
    return empty("No supported diagnosis can be given from the available evidence.", [reason],
                 ["Provide the API family, endpoint and spec version, and a fixture or test-case reference."])


def fallback(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic answer when the model fails: verdicts only, from validator output."""
    findings = []
    for i, r in enumerate(results):
        if decisive(r):
            vr = validation_results([r])[0]
            findings.append({"text": f"{r['tool']} returned {vr['result']}"
                                     + (f" ({'; '.join(str(x) for x in vr['details'])})" if vr["details"] else "") + ".",
                             "status": "confirmed", "citation_ids": [], "validator_refs": [i]})
    return {"summary": "The answer could not be generated; these are the deterministic validator results only.",
            "findings": findings, "next_steps": [], "limitations": ["Answer generation failed; no explanation is given."]}
