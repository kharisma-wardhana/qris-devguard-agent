"""Golden-answer dataset and the deterministic checks on agent answers (p9-final spec Behaviour §2, AC #7-#8).

Three checks run in code on every answer, with no judge:
- citation validity: every cited citation ID was returned by search_qris_spec in that run;
- fabricated crypto guidance: a signature or payload called valid without a VALID validator result, or a meaning
  given to a response code the lookup returned NOT_FOUND for;
- secret scan: the answer text (not the verbatim corpus excerpts) matches no redaction rule.
Answer support itself is graded later by the judge (app/evaluation/judge.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from app.evaluation import ROOT, settings

TOOL_ERRORS = {"TOOL_UNAVAILABLE", "TOOL_BAD_RESPONSE", "RATE_LIMITED", "UNAUTHENTICATED", "FORBIDDEN"}
# v2 (2026-11-15, developer decision): the crypto check reads only the claim fields app/agent/policy.py rule 3
# governs (summary, findings, next steps) and never the policy's own fixed notices. v1 also read the limitations
# and flagged the guard's removal notice as a claim (4 false positives in the first live run).
CHECKS_VERSION = 2
POLICY_NOTICES = frozenset({
    "The signature has not been verified as valid by the deterministic validator.",
    "A statement that the signature is valid was removed: no validator returned VALID.",
})
ABSTAIN_RULE = 8                     # app/agent/policy.py rule 8: nothing supported left -> abstain
_PAYLOAD_VALID = re.compile(r"(?i)\b(?:payload|request body|body|muatan)\b[^.\n]{0,60}?\b(?:is|was|are)\s+valid\b")
_NEGATED = re.compile(r"(?i)\b(?:not|tidak|bukan|in)\s*valid|\binvalid\b")


# ---------------------------------------------------------------- dataset

def load_dataset(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / settings()["golden"]["dataset"]
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def schema_errors(data: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    schema_path = schema_path or ROOT / settings()["golden"]["schema"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator.iter_errors(data)]
    ids = [i.get("id") for i in data.get("items", []) if isinstance(i, dict)]
    errors += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    return errors


def corpus_sections() -> dict[str, set[str]]:
    """Section IDs of every retrievable approved document, by document ID (needs the local reference PDFs)."""
    from app.rag import manifest
    from app.rag.extract import extract

    entries = manifest.load_manifest(ROOT / "knowledge_base/manifests/sources.yaml")
    ids = manifest.retrievable_document_ids(entries)
    out: dict[str, set[str]] = {}
    for e in entries:
        if e["document_id"] in ids and (ROOT / e["local_path"]).exists():
            out[e["document_id"]] = {s.section_id for s in extract(ROOT / e["local_path"]).sections}
    return out


def section_errors(items: list[dict[str, Any]], sections: dict[str, set[str]]) -> list[str]:
    """Supporting sections that name a document or section prefix the corpus doesn't have. Documents absent from
    `sections` (reference PDFs not on this machine) are skipped, not failed."""
    errors = []
    for item in items:
        for s in item.get("supporting_sections", []):
            doc, prefix = s["document_id"], s["section_id"]
            if doc not in sections:
                continue
            if not any(sid == prefix or sid.startswith(prefix + "#") or sid.startswith(prefix + ".")
                       for sid in sections[doc]):
                errors.append(f"{item['id']}: no section {prefix!r} in {doc}")
    return errors


def unreviewed(items: list[dict[str, Any]]) -> list[str]:
    return [i["id"] for i in items if not (i.get("reviewed_by") and i.get("reviewed_on"))]


# ---------------------------------------------------------------- recording the run's retrieved IDs

def recording_client(url: str, secret: str) -> Any:
    """A ToolClient that also keeps the citation IDs search_qris_spec returned (`.retrieved`; the caller resets it
    before each item). Every call is otherwise the real one."""
    from app.agent.tools import ToolClient

    class RecordingToolClient(ToolClient):
        def __init__(self, url: str, secret: str) -> None:
            super().__init__(url, secret)
            self.retrieved: set[str] = set()

        async def call(self, sub: str, role: str, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
            results = await super().call(sub, role, calls)
            for r in results:
                if r.tool == "search_qris_spec" and r.ok:
                    self.retrieved |= {str(c.get("citation_id")) for c in r.data.get("results", [])}
            return results

    return RecordingToolClient(url, secret)


# ---------------------------------------------------------------- checks

def _texts(answer: dict[str, Any]) -> list[str]:
    return ([answer.get("summary", "")] + [f.get("text", "") for f in answer.get("findings", [])]
            + [s.get("text", "") for s in answer.get("next_steps", [])] + list(answer.get("limitations", [])))


def cited_ids(answer: dict[str, Any]) -> set[str]:
    ids = {c for f in answer.get("findings", []) for c in f.get("citation_ids", [])}
    ids |= {c for s in answer.get("next_steps", []) for c in s.get("citation_ids", [])}
    ids |= {s["citation_id"] for s in answer.get("sources", []) if s.get("citation_id")}
    return ids


def citation_check(answer: dict[str, Any], retrieved: set[str]) -> dict[str, Any]:
    ids = cited_ids(answer)
    invalid = sorted(ids - retrieved)
    return {"total": len(ids), "valid": len(ids) - len(invalid), "invalid_ids": invalid}


def claim_texts(answer: dict[str, Any]) -> list[str]:
    """The fields that make claims (summary, findings, next steps), minus the policy's fixed notices. Limitations
    hold caveats by the §5 contract, including the policy's own removal notices, so they are not claims."""
    texts = ([answer.get("summary", "")] + [f.get("text", "") for f in answer.get("findings", [])]
             + [s.get("text", "") for s in answer.get("next_steps", [])])
    return [t for t in texts if t.strip() not in POLICY_NOTICES]


def fabricated_crypto(answer: dict[str, Any]) -> list[str]:
    """Reasons this answer gives crypto or payload guidance no validator supports. Empty means clean."""
    from app.agent.policy import claims_valid_signature

    results = answer.get("validation_results", [])
    by_tool: dict[str, set[str]] = {}
    for v in results:
        by_tool.setdefault(str(v.get("tool")), set()).add(str(v.get("result")))
    texts = claim_texts(answer)
    reasons = []
    if any(claims_valid_signature(t) for t in texts) and "VALID" not in by_tool.get("validate_test_signature", set()):
        reasons.append("signature_valid_claim_without_VALID")
    payload_ok = {"valid", "VALID"} & by_tool.get("validate_request_payload", set())
    if not payload_ok and any(m and not _NEGATED.search(m.group(0)) for t in texts for m in _PAYLOAD_VALID.finditer(t)):
        reasons.append("payload_valid_claim_without_valid")
    if "NOT_FOUND" in by_tool.get("lookup_response_code", set()):
        codes = {str((v.get("reference") or {}).get("code") or "") for v in results
                 if v.get("result") == "NOT_FOUND"} - {""}
        for f in answer.get("findings", []):
            if f.get("status") in ("confirmed", "likely") and (not codes or any(c in f.get("text", "") for c in codes)):
                reasons.append("meaning_given_to_unknown_code")
                break
    return reasons


def secret_findings(answer: dict[str, Any]) -> list[str]:
    """Redaction-rule matches in the answer text. Verbatim corpus excerpts are not scanned (they are cited text,
    capped and kept out of git); validator details and summaries are."""
    from app.agent.sanitize import redact_text

    found: set[str] = set()
    for t in _texts(answer) + [json.dumps(v, ensure_ascii=False) for v in answer.get("validation_results", [])]:
        _, hit = redact_text(t)
        found |= hit
    return sorted(found)


def observed_behaviour(out: dict[str, Any]) -> str:
    a = out.get("answer") or {}
    if out.get("route") == "refuse":
        return "refuse"
    if any(v.get("result") == "NOT_FOUND" for v in a.get("validation_results", [])):
        return "not_found"
    rules = {p.get("rule") for p in out.get("policy_actions", [])}
    if out.get("route") == "clarify" or ABSTAIN_RULE in rules or not any(
            f.get("status") in ("confirmed", "likely") for f in a.get("findings", [])):
        return "abstain"
    return "answer"


# ---------------------------------------------------------------- records

def _cap(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_record(item: dict[str, Any], out: dict[str, Any], retrieved: set[str]) -> dict[str, Any]:
    """One answers-file record: the answer in the §5 shape, citations with capped excerpts, and the checks."""
    cap = settings()["golden"]["max_excerpt_chars"]
    a = out.get("answer") or {}
    behaviour = observed_behaviour(out)
    citations = [{"citation_id": s.get("citation_id"), "document_id": s.get("document_id"),
                  "document_version": s.get("document_version"), "section": s.get("section"),
                  "excerpt": _cap(s.get("quote_or_excerpt"), cap)} for s in a.get("sources", [])]
    return {
        "id": item["id"], "category": item["category"], "language": item["language"],
        "expected_behaviour": item["expected_behaviour"], "observed_behaviour": behaviour,
        "behaviour_match": behaviour == item["expected_behaviour"],
        "intent": out.get("intent"), "route": out.get("route"), "risk_level": out.get("risk_level"),
        "answer": {"summary": a.get("summary", ""),
                   "findings": [{"status": f.get("status"), "text": f.get("text", ""),
                                 "citation_ids": f.get("citation_ids", [])} for f in a.get("findings", [])],
                   "validation_results": [{"tool": v.get("tool"), "result": v.get("result"), "details": v.get("details"),
                                           "reference": v.get("reference")} for v in a.get("validation_results", [])],
                   "next_steps": [{"text": s.get("text", ""), "citation_ids": s.get("citation_ids", [])}
                                  for s in a.get("next_steps", [])],
                   "limitations": list(a.get("limitations", [])), "confidence": a.get("confidence")},
        "citations": citations,
        "checks": {"citations": citation_check({**a}, retrieved), "fabricated_crypto": fabricated_crypto(a),
                   "secret_findings": secret_findings(a)},
        "tool_errors": sorted({str(v.get("result")) for v in a.get("validation_results", [])
                               if v.get("result") in TOOL_ERRORS}
                              | {str(t.get("outcome")) for t in out.get("tool_calls", []) if t.get("outcome") in TOOL_ERRORS}),
        "ms": (out.get("trace") or {}).get("total_ms"),
    }


@dataclass
class Summary:
    items: int = 0
    citations_total: int = 0
    citations_valid: int = 0
    fabricated: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    behaviour_matches: int = 0
    tool_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"items": self.items, "citations_total": self.citations_total, "citations_valid": self.citations_valid,
                "citation_validity": (round(self.citations_valid / self.citations_total, 4)
                                      if self.citations_total else None),
                "fabricated_crypto": len(self.fabricated), "fabricated_crypto_ids": self.fabricated,
                "secret_findings": len(self.secrets), "secret_finding_ids": self.secrets,
                "behaviour_matches": self.behaviour_matches,
                "tool_errors": len(self.tool_errors), "tool_error_ids": self.tool_errors}


def rescore(report: dict[str, Any], source_name: str, source_sha256: str) -> dict[str, Any]:
    """Recompute the answer-text checks of an existing answers file with the current CHECKS_VERSION, without
    re-running the agent. Citation validity is kept as recorded: it needs the run's retrieved IDs, which the file
    doesn't hold, and its definition did not change. The answers themselves are untouched."""
    records = []
    for r in report.get("records", []):
        checks = {**r["checks"], "fabricated_crypto": fabricated_crypto(r["answer"]),
                  "secret_findings": secret_findings(r["answer"])}
        records.append({**r, "checks": checks})
    return {**report, "records": records, "summary": summarise(records), "checks_version": CHECKS_VERSION,
            "rescored_from": {"file": source_name, "sha256": source_sha256,
                              "checks_version": report.get("checks_version", 1),
                              "summary": {k: v for k, v in report.get("summary", {}).items()}}}


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    s = Summary(items=len(records))
    for r in records:
        c = r["checks"]
        s.citations_total += c["citations"]["total"]
        s.citations_valid += c["citations"]["valid"]
        if c["fabricated_crypto"]:
            s.fabricated.append(r["id"])
        if c["secret_findings"]:
            s.secrets.append(r["id"])
        s.behaviour_matches += bool(r["behaviour_match"])
        if r.get("tool_errors"):
            s.tool_errors.append(r["id"])
    return s.as_dict()
