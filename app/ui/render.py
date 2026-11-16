"""Plain-text views of API responses for the demo UI (docs/p9-demo-ui-alerts/spec.md Behaviour §3, §4, §6; AC #15).

Everything here returns strings that the pages show with text elements only (never markdown or HTML), so a link
or markup planted in a question, an answer, a retrieved excerpt or an alert stays literal text. Headings are this
module's own fixed words. No Streamlit import.
"""

from __future__ import annotations

from typing import Any

FINDING_ORDER = ("confirmed", "likely", "unverified")
SEVERITY_LABEL = {"ALERT": "ALERT (firing)", "WARNING": "WARNING", "OK": "OK (recovered)", "NO_DATA": "NO DATA",
                  "UNKNOWN": "UNKNOWN", "PAUSED": "PAUSED"}


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _cites(ids: Any) -> str:
    return f" [{', '.join(_s(i) for i in ids)}]" if ids else ""


def answer_sections(answer: dict[str, Any] | None) -> list[tuple[str, list[str]]]:
    """The §5 response contract, in order: Summary, Findings, Validation results, Next steps, Sources,
    Confidence and limitations. Then the action outcome or draft, when present."""
    a = answer or {}
    out: list[tuple[str, list[str]]] = [("Summary", [_s(a.get("summary")) or "(no summary)"])]

    findings = [f for f in a.get("findings") or [] if isinstance(f, dict)]
    lines: list[str] = []
    for status in FINDING_ORDER:
        for f in findings:
            if f.get("status") == status:
                lines.append(f"[{status}] {_s(f.get('text'))}{_cites(f.get('citation_ids'))}")
    lines += [f"[{_s(f.get('status')) or 'unlabelled'}] {_s(f.get('text'))}" for f in findings
              if f.get("status") not in FINDING_ORDER]
    out.append(("Findings", lines or ["(none)"]))

    lines = []
    for v in a.get("validation_results") or []:
        ref = v.get("reference") or {}
        ref_text = ", ".join(f"{k}={_s(x)}" for k, x in ref.items() if x is not None)
        lines.append(f"{_s(v.get('tool'))} -> {_s(v.get('result'))}" + (f" ({ref_text})" if ref_text else ""))
        lines += [f"    detail: {_s(d)}" for d in v.get("details") or [] if d]
        lines += [f"    limitation: {_s(d)}" for d in v.get("limitations") or [] if d]
    out.append(("Validation results", lines or ["(no validator was run)"]))

    out.append(("Next steps", [f"{n}. {_s(s.get('text'))}{_cites(s.get('citation_ids'))}"
                               for n, s in enumerate(a.get("next_steps") or [], 1)] or ["(none)"]))

    lines = []
    for s in a.get("sources") or []:
        where = ", ".join(x for x in (f"section {_s(s.get('section'))}" if s.get("section") else "",
                                      f"page {_s(s.get('page'))}" if s.get("page") else "") if x)
        tag = " (SYNTHETIC)" if s.get("synthetic") else ""
        lines.append(f"[{_s(s.get('citation_id'))}] {_s(s.get('title'))}{tag} - {_s(s.get('document_id'))} "
                     f"v{_s(s.get('document_version'))}" + (f", {where}" if where else ""))
        if s.get("quote_or_excerpt"):
            lines.append(f'    "{_s(s.get("quote_or_excerpt"))}"')
        if s.get("source_url"):
            lines.append(f"    {_s(s.get('source_url'))}")
    out.append(("Sources", lines or ["(no sources cited)"]))

    out.append(("Confidence and limitations", [f"Confidence: {_s(a.get('confidence')) or 'unknown'}",
                                               *[f"- {_s(x)}" for x in a.get("limitations") or []]]))
    if a.get("action"):
        act = a["action"]
        out.append(("Action", [f"{_s(act.get('type'))} on {_s(act.get('target'))}: {_s(act.get('status'))}",
                               f"ticket: {_s(act.get('ticket_id')) or '(none: nothing was created)'}"]))
    if a.get("draft"):
        d = a["draft"]
        out.append(("Draft (not executed)", [f"{_s(d.get('action'))}: {_s(d.get('status'))}", _s(d.get("summary"))]))
    return out


def approval_lines(view: dict[str, Any]) -> list[str]:
    act = view.get("action") or {}
    payload = act.get("payload") or {}
    lines = [f"approval: {_s(view.get('approval_id'))}  status: {_s(view.get('status'))}",
             f"action: {_s(act.get('type'))} on {_s(act.get('target'))}",
             f"requester: {_s(view.get('requester_id'))}  expires: {_s(view.get('expires_at'))}",
             f"expected side effect: {_s(view.get('expected_side_effect'))}",
             f"evidence: {', '.join(_s(e) for e in view.get('evidence_ids') or []) or '(none)'}"]
    for key in ("title", "priority", "body"):                # the sanitized ticket draft (Agent._ticket_payload)
        if payload.get(key):
            lines.append(f"{key}: {_s(payload[key])}")
    if view.get("approver_id"):
        lines.append(f"decided by: {_s(view.get('approver_id'))} at {_s(view.get('decided_at'))}")
    lines.append(f"ticket: {_s(view.get('ticket_id')) or '(none)'}")
    return lines


def decision_outcome(result: dict[str, Any]) -> str:
    view = result.get("approval") or {}
    status, ticket = view.get("status"), view.get("ticket_id")
    if ticket:
        return f"Approved and executed: sandbox ticket {ticket} was created."
    if status == "rejected":
        return "Rejected: nothing was created."
    return f"Approval is now {_s(status)}; no ticket was created."


def alert_lines(alert: dict[str, Any]) -> tuple[str, list[str]]:
    """(header, detail lines) for one received alert."""
    sev = _s(alert.get("severity"))
    header = f"{SEVERITY_LABEL.get(sev, sev)} - {_s(alert.get('title'))}"
    lines = [_s(alert.get("body")),
             f"window: {_s(alert.get('window')) or '-'}  from: {_s(alert.get('from_timestamp')) or '-'}  "
             f"to: {_s(alert.get('to_timestamp')) or '-'}",
             f"received: {_s(alert.get('received_at'))}  alert id: {_s(alert.get('id'))}  monitor: {_s(alert.get('monitor_id'))}"]
    return header, lines


def short_id(value: str, n: int = 10) -> str:
    return value if len(value) <= n else f"{value[:n]}..."


# --- p9 ui-refresh (docs/p9-ui-refresh/spec.md Behaviour §2, §3, §6, §7) ------------------------------------------

def overview_counts(threads: list[dict[str, Any]] | None, pending: list[dict[str, Any]] | None,
                    checks: dict[str, Any] | None) -> dict[str, int | None]:
    """The Overview stat values. None means "no figure" (the read failed or the identity may not read it); the page
    shows "—" for it and never a placeholder number."""
    return {"threads": None if threads is None else len(threads),
            "awaiting": None if threads is None else sum(1 for t in threads if t.get("status") == "awaiting_approval"),
            "pending": None if pending is None else len(pending),
            "checks_up": None if not checks else sum(1 for v in checks.values() if v == "up"),
            "checks_total": None if not checks else len(checks)}


def _num(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.4g}"
    return _s(v)


def gate_value(value: Any) -> str:
    """A measured gate value as text: 0.4356, 3, true, or "passed=3, of=4" for an object. None is "(no value)"."""
    if value is None:
        return "(no value)"
    if isinstance(value, dict):
        return ", ".join(f"{_s(k)}={_num(v)}" for k, v in value.items()) or "(no value)"
    return _num(value)


def gate_threshold(threshold: Any) -> str:
    """{"min": 0.9} -> ">= 0.9"; {"max": 0} -> "<= 0"; {"must_pass": true} -> "must pass"; {"min": 4, "of": 4} ->
    ">= 4 of 4". Anything else is listed as key=value."""
    if not isinstance(threshold, dict) or not threshold:
        return "(no threshold)"
    t = dict(threshold)
    parts = []
    if "min" in t:
        parts.append(f">= {_num(t.pop('min'))}" + (f" of {_num(t.pop('of'))}" if "of" in t else ""))
    if "max" in t:
        parts.append(f"<= {_num(t.pop('max'))}")
    if t.pop("must_pass", None) is True:
        parts.append("must pass")
    parts += [f"{_s(k)}={_num(v)}" for k, v in t.items()]
    return ", ".join(parts)


def source_lines(source: dict[str, Any]) -> list[str]:
    """One Knowledge base or pinned-reference entry as plain text."""
    meta = ", ".join(x for x in (f"version {_s(source.get('document_version'))}" if source.get("document_version") else "",
                                 f"effective {_s(source.get('effective_date'))}" if source.get("effective_date") else "",
                                 _s(source.get("kind"))) if x)
    return [_s(source.get("title")) or "(untitled)",
            f"{_s(source.get('document_id'))}" + (f" · {_s(source.get('publisher'))}" if source.get("publisher") else ""),
            meta or "(no version or date)"]
