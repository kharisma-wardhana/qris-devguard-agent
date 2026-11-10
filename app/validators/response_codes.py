"""Exact response-code lookup (TRD §14.3; p4 spec AC #11, #12).

A code is 7 digits: HTTP status (3) + service code (2) + case code (2).
- FOUND             an entry for this HTTP status and case code, for this service code or for `any`
- NOT_FOUND         no such entry; never a guessed meaning. Up to N same-HTTP-status entries may
                    be listed, each labelled as a suggestion, not a match
- TABLE_UNAVAILABLE the table for this family/version is not on this machine (the real SNAP table
                    is extracted locally and git-ignored, so CI never has it)

`parse_pdf_rows` turns pdfplumber table rows from the SNAP data spec, part 6, into entries.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CODE = re.compile(r"^(\d{3})(\d{2})(\d{2})$")
FOUND, NOT_FOUND, TABLE_UNAVAILABLE = "FOUND", "NOT_FOUND", "TABLE_UNAVAILABLE"
_WS = re.compile(r"\s+")


@dataclass
class LookupResult:
    status: str
    code: str
    api_family: str
    spec_version: str
    http_status: str
    service_code: str
    case_code: str
    match: dict[str, Any] | None = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    citation: dict[str, Any] | None = None
    synthetic: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def load_table(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    table = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(table, dict) or not isinstance(table.get("entries"), list):
        return None
    return table


def _clip(text: str | None, cap: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _view(entry: dict[str, Any], cap: int) -> dict[str, Any]:
    return {
        "http_status": entry["http_status"], "service_code": entry["service_code"], "case_code": entry["case_code"],
        "category": entry.get("category"), "message": _clip(entry.get("message"), cap),
        "description": _clip(entry.get("description"), cap), "page": entry.get("page"),
    }


def lookup(code: str, api_family: str, spec_version: str, *, table: dict[str, Any] | None,
           table_meta: dict[str, Any], excerpt_cap: int, max_suggestions: int = 3) -> LookupResult:
    m = CODE.match(code)
    if not m:
        raise ValueError("code must be 7 digits")
    http, svc, case = m.groups()
    res = LookupResult(TABLE_UNAVAILABLE, code, api_family, spec_version, http, svc, case,
                       synthetic=bool(table_meta.get("synthetic")))
    if table is None:
        return res
    res.citation = {"document_id": table_meta["document_id"], "document_version": table.get("document_version", spec_version),
                    "section": table_meta.get("section"), "synthetic": res.synthetic}
    entries = table["entries"]
    exact = [e for e in entries if e["http_status"] == http and e["case_code"] == case and e["service_code"] == svc]
    generic = [e for e in entries if e["http_status"] == http and e["case_code"] == case and e["service_code"] == "any"]
    hit = (exact or generic or [None])[0]
    if hit is not None:
        res.status = FOUND
        res.match = {**_view(hit, excerpt_cap), "match_type": "exact" if exact else "generic_service_code"}
        if hit.get("page") is not None:
            res.citation["page"] = hit["page"]
        return res
    res.status = NOT_FOUND
    same_http = [e for e in entries if e["http_status"] == http and e["service_code"] in (svc, "any")]
    res.suggestions = [{**_view(e, excerpt_cap), "label": "suggestion, not a match"} for e in same_http[:max_suggestions]]
    return res


# --- extraction from the SNAP PDF (local only) -----------------------------------------------

def _clean(cell: Any) -> str:
    return _WS.sub(" ", str(cell or "")).strip()


def parse_pdf_rows(rows: list[tuple[int, list[Any]]]) -> list[dict[str, Any]]:
    """Rows are (page, cells) from pdfplumber tables. Merged cells come back as None and are dropped;
    a data row then has 6 cells: category, HTTP status, service code, case code, message, description.
    A row whose first four cells are empty continues the previous entry (a cell split across pages)."""
    entries: list[dict[str, Any]] = []
    for page, row in rows:
        cells = [_clean(c) for c in row if c is not None]
        if len(cells) != 6:
            continue
        cat, http, svc, case, msg, desc = cells
        if re.fullmatch(r"\d{3}", http) and re.fullmatch(r"\d{2}", case) and (svc == "any" or re.fullmatch(r"\d{2}", svc)):
            entries.append({"category": cat or None, "http_status": http, "service_code": svc, "case_code": case,
                            "message": msg, "description": desc, "page": page})
        elif not any((cat, http, svc, case)) and (msg or desc) and entries:
            prev = entries[-1]
            if msg:
                prev["message"] = f"{prev['message']} {msg}".strip()
            if desc:
                prev["description"] = f"{prev['description']} {desc}".strip()
    return entries
