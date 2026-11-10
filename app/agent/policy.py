"""policy_check: mechanical rules over the composed answer (p5 spec rules 1-8; AC #8).

Rules 3-5 use fixed patterns and status forcing. They cannot prove that a paraphrase is not a disguised
claim; the structural control is that verdicts and sources are assembled by code (app.agent.answer).
Every action is recorded so a reviewer can see what was changed.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.agent import answer
from app.agent.sanitize import redact_text

_VALID_CLAIM = [
    re.compile(r"(?i)\bsignatur\w*\b[^.\n]{0,60}?\b(?:is|are|was|were)\s+(?:valid|correct|verified)\b"),
    re.compile(r"(?i)\b(?:valid|correct|verified)\s+signatur\w*"),
    re.compile(r"(?i)\bsignatur\w*\s+(?:verified|verifies|matches)\b"),
    re.compile(r"(?i)\b(?:tanda tangan|signatur\w*)\b[^.\n]{0,40}?(?<!tidak )(?<!bukan )\b(?:valid|benar|sah)\b"),
]
_NEGATION = re.compile(r"(?i)\b(?:not|tidak|bukan|in)\s*valid|\binvalid\b")


def claims_valid_signature(text: str) -> bool:
    for p in _VALID_CLAIM:
        for m in p.finditer(text):
            if not _NEGATION.search(m.group(0)):
                return True
    return False


def check(composed: dict[str, Any], *, evidence: list[dict[str, Any]], results: list[dict[str, Any]],
          flags: dict[str, Any], code: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(final answer in the §5 shape, policy actions)."""
    out = copy.deepcopy(composed)
    actions: list[dict[str, Any]] = []
    retrieved = {c["citation_id"] for c in evidence}
    limitations = list(out.get("limitations", []))

    def act(rule: int, action: str, target: str) -> None:
        actions.append({"rule": rule, "action": action, "target": target})

    # Rule 1: citations must be retrieved in this run; validator refs must exist.
    for kind in ("findings", "next_steps"):
        for i, item in enumerate(out.get(kind, [])):
            keep = [c for c in item.get("citation_ids", []) if c in retrieved]
            if len(keep) != len(item.get("citation_ids", [])):
                act(1, "dropped citation not retrieved in this run", f"{kind}[{i}]")
            item["citation_ids"] = keep
            if kind == "findings":
                refs = [r for r in item.get("validator_refs", []) if isinstance(r, int) and 0 <= r < len(results)]
                if len(refs) != len(item.get("validator_refs", [])):
                    act(1, "dropped unknown validator reference", f"findings[{i}]")
                item["validator_refs"] = refs
                if not keep and not refs and item["status"] != "unverified":
                    item["status"] = "unverified"
                    act(1, "no support left: unverified", f"findings[{i}]")

    # Rule 2: confirmed needs a decisive validator result or a valid citation.
    for i, f in enumerate(out.get("findings", [])):
        if f["status"] == "confirmed" and not f["citation_ids"] and not any(answer.decisive(results[r]) for r in f["validator_refs"]):
            f["status"] = "unverified"
            act(2, "confirmed without decisive support: unverified", f"findings[{i}]")

    sig_valid = any(r["tool"] == "validate_test_signature" and r.get("ok") and r["data"].get("status") == "VALID"
                    for r in results)
    # Rule 3: no "valid signature" claim without a VALID result.
    if not sig_valid:
        kept = []
        for i, f in enumerate(out.get("findings", [])):
            if claims_valid_signature(f["text"]):
                act(3, "removed valid-signature claim without a VALID result", f"findings[{i}]")
            else:
                kept.append(f)
        out["findings"] = kept
        if claims_valid_signature(out.get("summary", "")):
            out["summary"] = "The signature has not been verified as valid by the deterministic validator."
            act(3, "replaced summary claiming a valid signature", "summary")
        steps = [s for s in out.get("next_steps", []) if not claims_valid_signature(s["text"])]
        if len(steps) != len(out.get("next_steps", [])):
            act(3, "removed next step claiming a valid signature", "next_steps")
        out["next_steps"] = steps
        if any(a["rule"] == 3 for a in actions):
            limitations.append("A statement that the signature is valid was removed: no validator returned VALID.")

    # Rule 4: an unknown or unavailable response code never gets a meaning.
    for i, r in enumerate(results):
        if r["tool"] == "lookup_response_code" and r.get("ok") and r["data"].get("status") in ("NOT_FOUND", "TABLE_UNAVAILABLE"):
            c = r["data"].get("code") or code or "the code"
            status = r["data"]["status"]
            out["summary"] = (f"Response code {c} was not found in the {r['data'].get('api_family')} "
                              f"{r['data'].get('spec_version')} table; no meaning is given." if status == "NOT_FOUND" else
                              f"The response-code table for {r['data'].get('api_family')} {r['data'].get('spec_version')} "
                              f"is not available here, so code {c} cannot be looked up; no meaning is given.")
            act(4, f"fixed summary for {status}", "summary")
            for j, f in enumerate(out.get("findings", [])):
                if (i in f["validator_refs"] or c in f["text"]) and f["status"] != "unverified":
                    f["status"] = "unverified"
                    act(4, "finding about an unresolved code: unverified", f"findings[{j}]")
            limitations.append(f"Response code {c}: {status}. Ask for the source API family and version.")

    # Rule 5: INCONCLUSIVE is never a verdict.
    for i, r in enumerate(results):
        if r["tool"] == "validate_test_signature" and r.get("ok") and r["data"].get("status") == "INCONCLUSIVE":
            for j, f in enumerate(out.get("findings", [])):
                if i in f["validator_refs"] and f["status"] != "unverified":
                    f["status"] = "unverified"
                    act(5, "finding on an INCONCLUSIVE result: unverified", f"findings[{j}]")
            limitations.append(f"The signature check was inconclusive ({r['data'].get('diagnostic_code')}); "
                               "it is neither valid nor invalid.")

    # Rule 6: conflicting versions are shown apart.
    if flags.get("version_conflict"):
        limitations.append("The evidence contains more than one version of the same document and they differ; "
                           "both are listed in the sources. Tell me which version you integrate against.")
        act(6, "version-conflict limitation added; both versions kept in sources", "limitations")

    # Rule 8 (before 7 so the abstention text is redacted too): nothing supported left -> abstain.
    if not out.get("findings"):
        reason = "No finding was supported by retrieved evidence or a validator result."
        abst = answer.abstention(reason)
        out["summary"], out["next_steps"] = (out["summary"] if any(a["rule"] == 4 for a in actions) else abst["summary"],
                                             abst["next_steps"])
        limitations.append(reason)
        act(8, "abstained: no supported finding", "answer")

    cited = {c for f in out.get("findings", []) for c in f["citation_ids"]} | \
            {c for s in out.get("next_steps", []) for c in s["citation_ids"]}
    final = {
        "summary": out.get("summary", ""),
        "findings": out.get("findings", []),
        "validation_results": answer.validation_results(results),
        "next_steps": out.get("next_steps", []),
        "sources": answer.sources(evidence, cited, bool(flags.get("version_conflict"))),
        "confidence": answer.confidence(flags, results, bool(flags.get("fallback"))),
        "limitations": list(dict.fromkeys(limitations)),
    }
    # Rule 7: redact every output string again.
    final, n = _redact(final)
    if n:
        act(7, f"redacted {n} secret-looking value(s) in the output", "answer")
    return final, actions


def _redact(obj: Any) -> tuple[Any, int]:
    if isinstance(obj, str):
        text, found = redact_text(obj)
        return text, int(bool(found))
    if isinstance(obj, list):
        pairs = [_redact(v) for v in obj]
        return [p[0] for p in pairs], sum(p[1] for p in pairs)
    if isinstance(obj, dict):
        # Source excerpts are verbatim corpus text (no user input); rewriting them would break the literal-excerpt
        # citation contract (p2), so rule 7 covers every other string.
        pairs = {k: (v, 0) if k == "quote_or_excerpt" else _redact(v) for k, v in obj.items()}
        return {k: v[0] for k, v in pairs.items()}, sum(v[1] for v in pairs.values())
    return obj, 0
