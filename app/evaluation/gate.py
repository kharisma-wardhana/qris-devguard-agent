"""Release gate (p9-final spec Behaviour §4; AC #16-#22).

Reads configs/eval_thresholds.yaml (targets, never edited here) and configs/evaluation.yaml (where each gate's
evidence lives), picks the newest accepted report for each gate in evals/reports/, and grades it:

- PASS / FAIL against the threshold;
- NOT RUN when there is no accepted evidence (none at all, only an unaccepted mode such as CI evidence for a
  live-only gate, a fake judge, or a producer that itself reported NOT RUN);
- STALE when the evidence is older than `freshness_days` or names a dataset version the dataset file no longer has.

The overall verdict is PASS only if every gate and every critical gate is PASS; a critical gate that is not PASS is
an automatic FAIL. It runs no eval. Latency (TTFT/TPOT) is shown separately as reported, not gated.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from app.evaluation import ROOT, settings, thresholds

PASS, FAIL, NOT_RUN, STALE = "PASS", "FAIL", "NOT RUN", "STALE"
_FILE_TS = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})")
_CRYPTO_FLOW_FAILURES = {"no_valid_signature_claim", "no_confirmed_about_code"}


class GateError(RuntimeError):
    """Configuration or threshold file unreadable: exit 2, no verdict."""


@dataclass
class Evidence:
    path: str
    sha256: str
    date: str | None
    dataset_version: str | None
    mode: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "date": self.date, "dataset_version": self.dataset_version,
                "mode": self.mode}


@dataclass
class GateResult:
    name: str
    kind: str                      # "gate" | "critical"
    status: str
    value: Any = None
    threshold: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "status": self.status, "value": self.value,
                "threshold": self.threshold, "evidence": [e.as_dict() for e in self.evidence], "note": self.note}


# ---------------------------------------------------------------- reading reports

def report_date(path: Path, data: dict[str, Any]) -> datetime | None:
    for key in ("generated_at", "measured_at", "ts"):
        raw = data.get(key)
        if isinstance(raw, str):
            for candidate in (raw, raw.replace("Z", "+00:00")):
                try:
                    dt = datetime.fromisoformat(candidate)
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            m = _FILE_TS.search(raw)
            if m:
                return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}+00:00")
    m = _FILE_TS.search(path.name)
    if m:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}+00:00")
    return None


def report_mode(data: dict[str, Any]) -> str | None:
    if data.get("fake_client"):
        return "fake"                 # a fake judge is never evidence
    return data.get("mode")


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def pick(reports_dir: Path, glob: str, modes: list[Any]) -> tuple[Path, dict[str, Any]] | str:
    """(path, data) of the newest report matching `glob` whose mode is accepted, or a NOT RUN reason."""
    found = []
    for path in reports_dir.glob(glob):
        data = _load(path)
        if data is not None:
            found.append((report_date(path, data) or datetime.min.replace(tzinfo=timezone.utc), path, data))
    if not found:
        return f"no evidence matching {glob}"
    accepted = [f for f in found if report_mode(f[2]) in modes]
    if not accepted:
        seen = sorted({str(report_mode(f[2])) for f in found})
        return f"only evidence in mode(s) {', '.join(seen)}; accepted: {', '.join(str(m) for m in modes)}"
    _, path, data = max(accepted, key=lambda f: (f[0], f[1].name))
    return path, data


def dotted(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _security_cases(data: dict[str, Any], cases: list[str]) -> dict[str, str] | None:
    by_id = {r.get("id"): r.get("status") for r in data.get("results", [])}
    if any(c not in by_id for c in cases):
        return None
    return {c: str(by_id[c]) for c in cases}


def extract(kind: str, data: dict[str, Any], spec: dict[str, Any]) -> tuple[Any, str]:
    """(value, note). value None means the report doesn't carry the needed data (NOT RUN)."""
    if kind == "min_accuracy":
        accs = [v.get("accuracy") for v in (data.get("validators") or {}).values()]
        return (min(accs), "") if accs and None not in accs else (None, "no validator accuracies")
    if kind == "flows_passed":
        ran = [f for f in data.get("flows", []) if f.get("passed") is not None]
        return {"passed": sum(bool(f["passed"]) for f in ran), "ran": len(ran)}, ""
    if kind == "flows_crypto_failures":
        return sum(bool(_CRYPTO_FLOW_FAILURES & set(f.get("failures") or [])) for f in data.get("flows", [])), ""
    if kind == "status_pass":
        status = data.get("status")
        if status == NOT_RUN or status is None:
            return None, f"producer reported {status}"
        return status == PASS, f"producer reported {status}"
    if kind in ("case_pass_fraction", "case_fail_count"):
        cases = _security_cases(data, spec["cases"])
        if cases is None:
            return None, f"cases missing from report: {', '.join(spec['cases'])}"
        ran = {c: s for c, s in cases.items() if s != NOT_RUN}
        if len(ran) < len(cases):
            return None, "a mapped case was NOT RUN: " + ", ".join(c for c in cases if c not in ran)
        not_pass = sum(s != PASS for s in ran.values())
        if kind == "case_pass_fraction":
            return round((len(ran) - not_pass) / len(ran), 4), ", ".join(f"{c} {s}" for c, s in cases.items())
        return not_pass, ", ".join(f"{c} {s}" for c, s in cases.items())
    if kind == "real_alert_fired":
        return bool(data.get("real_alert_fired")), f"{len(data.get('real', []))} real deliveries"
    return dotted(data, kind), ""


def compare(value: Any, threshold: dict[str, Any]) -> bool:
    if "must_pass" in threshold:
        return value is True
    if "of" in threshold:
        return value["ran"] == threshold["of"] and value["passed"] >= threshold["min"]
    if "min" in threshold:
        return value >= threshold["min"]
    if "max" in threshold:
        return value <= threshold["max"]
    raise GateError(f"unknown threshold shape {threshold}")


def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _dataset_version(spec: dict[str, Any] | None) -> str | None:
    if not spec:
        return None
    data = yaml.safe_load((ROOT / spec["file"]).read_text(encoding="utf-8"))
    return str(data.get(spec["key"]))


# ---------------------------------------------------------------- grading

def grade_source(name: str, kind: str, spec: dict[str, Any], threshold: dict[str, Any], reports_dir: Path,
                 now: datetime) -> GateResult:
    picked = pick(reports_dir, spec["glob"], spec["modes"])
    if isinstance(picked, str):
        return GateResult(name, kind, NOT_RUN, threshold=threshold, note=picked)
    path, data = picked
    date = report_date(path, data)
    report_version = data.get(spec["dataset"]["report_key"]) if spec.get("dataset") else None
    ev = Evidence(_shown(path), hashlib.sha256(path.read_bytes()).hexdigest(),
                  date.isoformat() if date else None, None if report_version is None else str(report_version),
                  report_mode(data))
    value, note = extract(spec["value"], data, spec)
    rescored = data.get("rescored_from")
    if rescored and spec["value"].startswith("summary."):          # both numbers stay visible (decision 2026-11-15)
        key = spec["value"].split(".", 1)[1]
        was = (rescored.get("summary") or {}).get(key)
        note = "; ".join(x for x in (note, f"rescored from {rescored.get('file')} (checks v{rescored.get('checks_version')}: "
                                           f"{key} was {was})") if x)
    if value is None:
        return GateResult(name, kind, NOT_RUN, threshold=threshold, evidence=[ev], note=note or "value not in report")
    expected_version = _dataset_version(spec.get("dataset"))
    if expected_version is not None and str(report_version) != expected_version:
        return GateResult(name, kind, STALE, value, threshold, [ev],
                          f"dataset version {report_version} != current {expected_version}")
    if date is None or now - date > timedelta(days=settings()["freshness_days"]):
        return GateResult(name, kind, STALE, value, threshold, [ev],
                          f"evidence older than {settings()['freshness_days']} days" if date else "evidence has no date")
    return GateResult(name, kind, PASS if compare(value, threshold) else FAIL, value, threshold, [ev], note)


def grade_manifest(name: str, spec: dict[str, Any], threshold: dict[str, Any], now: datetime,
                   manifest_path: Path | None) -> GateResult:
    path = manifest_path or ROOT / spec["manifest"]
    try:
        from app.evaluation import manifest
    except ImportError:
        return GateResult(name, "gate", NOT_RUN, threshold=threshold, note="manifest checker not installed")
    if not path.is_file():
        return GateResult(name, "gate", NOT_RUN, threshold=threshold, note=f"no manifest at {spec['manifest']}")
    ok, problems, date = manifest.evaluate(path, now)
    ev = Evidence(_shown(path), hashlib.sha256(path.read_bytes()).hexdigest(),
                  date, None, None)
    status = PASS if ok else (STALE if problems == ["stale"] else FAIL)
    return GateResult(name, "gate", status, ok, threshold, [ev], "; ".join(problems))


def run(reports_dir: Path | None = None, now: datetime | None = None, manifest_path: Path | None = None) -> dict[str, Any]:
    try:
        s, t = settings(), thresholds()
    except Exception as exc:  # noqa: BLE001
        raise GateError(f"config unreadable: {type(exc).__name__}") from exc
    reports_dir = reports_dir or ROOT / s["reports_dir"]
    now = now or datetime.now(timezone.utc)
    results: list[GateResult] = []
    for name, threshold in t["gates"].items():
        spec = s["gates"].get(name)
        if spec is None:
            raise GateError(f"no evidence mapping for gate {name}")
        if "manifest" in spec:
            results.append(grade_manifest(name, spec, threshold, now, manifest_path))
        else:
            results.append(grade_source(name, "gate", spec, threshold, reports_dir, now))
    for name, threshold in t["critical"].items():
        spec = s["critical"].get(name)
        if spec is None:
            raise GateError(f"no evidence mapping for critical gate {name}")
        parts = [grade_source(name, "critical", src, threshold, reports_dir, now) for src in spec["sources"]]
        evidence = [e for p in parts for e in p.evidence]
        notes = "; ".join(p.note for p in parts if p.note)
        bad = [p for p in parts if p.status in (NOT_RUN, STALE)]
        if bad:
            results.append(GateResult(name, "critical", bad[0].status, None, threshold, evidence, notes))
        else:
            total = sum(p.value for p in parts)
            results.append(GateResult(name, "critical", PASS if compare(total, threshold) else FAIL, total, threshold,
                                      evidence, notes))
    not_pass = [r.name for r in results if r.status != PASS]
    critical_bad = [r.name for r in results if r.kind == "critical" and r.status != PASS]
    verdict = PASS if not not_pass else FAIL
    return {"generated_at": now.isoformat(timespec="seconds"), "verdict": verdict, "not_passing": not_pass,
            "critical_not_passing": critical_bad, "freshness_days": s["freshness_days"],
            "gates": [r.as_dict() for r in results], "reported_not_gated": reported(reports_dir)}


def reported(reports_dir: Path) -> dict[str, Any]:
    """Latency and judge calibration: shown next to the gates, never graded."""
    out: dict[str, Any] = {}
    bench = pick(reports_dir, "bench-report-*.json", ["live"])
    if not isinstance(bench, str):
        path, data = bench
        out["latency"] = {"evidence": path.name, "hardware": data.get("hardware"), "rows": data.get("rows"),
                          "tunnel_ttft": data.get("tunnel_ttft"), "notes": data.get("notes")}
    judge = pick(reports_dir, "judge-*.json", ["live"])
    if not isinstance(judge, str):
        path, data = judge
        out["judge"] = {"evidence": path.name, "model": data.get("model"), "prompt_version": data.get("prompt_version"),
                        "calibration": data.get("calibration"), "not_judged": (data.get("metrics") or {}).get("not_judged")}
    return out


# ---------------------------------------------------------------- rendering

BEGIN, END = "<!-- release-gate:begin -->", "<!-- release-gate:end -->"


def _fmt(value: Any) -> str:
    if isinstance(value, dict) and "passed" in value:
        return f"{value['passed']}/{value['ran']}"
    if isinstance(value, float):
        return f"{value:.4g}"
    return "—" if value is None else str(value)


def _thr(threshold: dict[str, Any]) -> str:
    if "must_pass" in threshold:
        return "must pass"
    if "of" in threshold:
        return f"≥ {threshold['min']} of {threshold['of']}"
    return f"≥ {threshold['min']}" if "min" in threshold else f"≤ {threshold['max']}"


def markdown(result: dict[str, Any]) -> str:
    lines = [BEGIN, f"*Generated by `scripts/release_gate.py` at {result['generated_at']}. Do not edit by hand.*", "",
             f"**Verdict: {result['verdict']}**"
             + (f" — not passing: {', '.join(result['not_passing'])}" if result["not_passing"] else ""), "",
             "| Gate | Kind | Status | Value | Threshold | Evidence (SHA-256, first 12) | Date | Dataset | Mode | Note |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for g in result["gates"]:
        ev = g["evidence"]
        cell = lambda k: "<br>".join(str(e[k]) if e[k] is not None else "—" for e in ev) or "—"  # noqa: E731
        sha = "<br>".join(f"`{Path(e['path']).name}` {e['sha256'][:12]}" for e in ev) or "—"
        lines.append(f"| {g['name']} | {g['kind']} | {g['status']} | {_fmt(g['value'])} | {_thr(g['threshold'])} | "
                     f"{sha} | {cell('date')} | {cell('dataset_version')} | {cell('mode')} | {g['note'] or ''} |")
    rep = result.get("reported_not_gated", {})
    lines += ["", "### Reported, not gated", ""]
    lat = rep.get("latency")
    if lat:
        hw = lat.get("hardware") or {}
        lines.append(f"Latency (`{lat['evidence']}`; {hw.get('gpu_sku')}, driver {hw.get('driver')}, CUDA {hw.get('cuda')}), "
                     "against the PRD §22 targets (p95 TTFT ≤ 5000 ms, p95 TPOT ≤ 120 ms at concurrency 1):")
        lines += ["", "| Concurrency | TTFT p50 / p95 (ms) | TPOT p50 / p95 (ms) | Targets met |", "|---|---|---|---|"]
        for r in lat.get("rows") or []:
            met = r.get("against_targets")
            met_s = ("yes" if all(v["met"] for v in met.values()) else "no") if met else "informational"
            lines.append(f"| {r['max_concurrency']} | {r['ttft_ms']['p50']:.1f} / {r['ttft_ms']['p95']:.1f} | "
                         f"{r['tpot_ms']['p50']:.1f} / {r['tpot_ms']['p95']:.1f} | {met_s} |")
        for n in lat.get("notes") or []:
            lines.append(f"\n> {n}")
    else:
        lines.append("Latency: no live benchmark report yet.")
    j = rep.get("judge")
    if j:
        cal = j.get("calibration") or {}
        lines.append(f"\nJudge: {j.get('model')} ({j.get('prompt_version')}), not judged {j.get('not_judged')}; "
                     f"calibration {cal.get('status')}"
                     + (f": agreement {cal.get('percent_agreement')}, Cohen's kappa {cal.get('cohens_kappa')}"
                        if cal.get("status") == "measured" else "")
                     + ("" if cal.get("human_graded") else
                        f". **Model-graded calibration** (graded by: {cal.get('graded_by') or 'nobody'}): the judge "
                        "metrics have no independent human check."))
    lines.append(END)
    return "\n".join(lines)


def write_markdown(path: Path, block: str) -> None:
    """Replace the generated block (or create the file with it); text outside the markers is kept."""
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if BEGIN in text and END in text:
            head, rest = text.split(BEGIN, 1)
            tail = rest.split(END, 1)[1]
            path.write_text(head + block + tail, encoding="utf-8")
            return
        path.write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Release gate report\n\n" + block + "\n", encoding="utf-8")
