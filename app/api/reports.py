"""Read-only views for the demo UI's Knowledge base and Evaluations pages (docs/p9-ui-refresh/spec.md Behaviour §8).

- `sources()`: the approved, retrievable manifest entries, as a field allowlist (no paths, checksums, permission
  evidence, notes or URLs).
- `release_gate()`: the newest release-gate report, as a field allowlist (no evidence paths or SHA-256 values).

This module does not import app.evaluation (eval-time only) and does not read configs/evaluation.yaml; its paths come
from configs/agent.yaml `api`. Both functions raise ReportError, never an exception carrying file text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent import config
from app.rag import manifest

ROOT = config.ROOT
SOURCE_FIELDS = ("document_id", "title", "publisher", "kind", "document_version")
GATE_REQUIRED = ("generated_at", "verdict", "gates")


class ReportError(Exception):
    """`missing` True: no usable report (404). False: a report or the manifest exists but is unusable (500)."""

    def __init__(self, missing: bool):
        super().__init__("missing" if missing else "unusable")
        self.missing = missing


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def sources(manifest_path: Path | None = None) -> list[dict[str, Any]]:
    path = manifest_path or ROOT / config.agent()["api"]["manifest_path"]
    try:
        entries = manifest.load_manifest(path)
        keep = manifest.retrievable_document_ids(entries)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ReportError(missing=False) from None
    out = []
    for e in entries:
        if e.get("document_id") not in keep:
            continue
        item: dict[str, Any] = {k: _s(e.get(k)) for k in SOURCE_FIELDS}
        item["effective_date"] = _s(e.get("effective_date"))       # YAML may parse YYYY-MM-DD as a date
        item["synthetic"] = (e.get("permission") or {}).get("basis") == "team_authored"
        out.append(item)
    return out


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _names(v: Any) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _gate(g: Any) -> dict[str, Any]:
    if not isinstance(g, dict) or "name" not in g or "status" not in g:
        raise ReportError(missing=False)
    dates = [str(e["date"]) for e in g.get("evidence") or [] if isinstance(e, dict) and e.get("date")]
    return {"name": str(g["name"]), "kind": _s(g.get("kind")), "status": str(g["status"]),
            "value": g.get("value"), "threshold": g.get("threshold"),
            "evidence_date": max(dates) if dates else None, "note": str(g.get("note") or "")}


def release_gate(reports_dir: Path | None = None) -> dict[str, Any]:
    api = config.agent()["api"]
    directory = reports_dir or ROOT / api["reports_dir"]
    found = [(str(d.get("generated_at") or ""), p.name, d) for p in directory.glob(api["release_gate_glob"])
             if (d := _load(p)) is not None]
    if not found:
        raise ReportError(missing=True)
    _, _, data = max(found, key=lambda f: (f[0], f[1]))
    if any(k not in data for k in GATE_REQUIRED) or not isinstance(data["gates"], list):
        raise ReportError(missing=False)
    return {"generated_at": str(data["generated_at"]), "verdict": str(data["verdict"]),
            "not_passing": _names(data.get("not_passing")),
            "critical_not_passing": _names(data.get("critical_not_passing")),
            "gates": [_gate(g) for g in data["gates"]]}
