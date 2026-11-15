"""Judge golden answers with Claude Opus 5.5 (docs/p9-final-evaluation-packaging/spec.md Behaviour §3; AC #9-#13).

Usage (the developer runs this, with their own Anthropic credential in the environment):
  uv sync --extra judge                                               # once
  uv run python scripts/judge_answers.py evals/reports/answers-live-<ts>.json
  uv run python scripts/judge_answers.py <answers file> --batch-id <id>   # collect an existing batch (no new submit)

It submits one Message Batches request per answer, prints the batch ID straight away (so an interrupted run can be
resumed with --batch-id), waits for the batch to end, and writes evals/reports/judge-<ts>.json (git-ignored): the
metrics, the calibration agreement, the model ID, effort, prompt version and batch ID, and per-item verdicts. It never
prints or stores the credential. --fake-client is for tests only and is refused unless DEVGUARD_TEST_BACKENDS=1.

Exit 0 when the batch was collected and the report written; 1 on any error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_labels(path: Path) -> tuple[dict[str, bool | None], str | None]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(i["id"]): i.get("developer_supported") for i in data.get("items", [])}, data.get("graded_by")


def main(argv: list[str] | None = None) -> int:
    from app.evaluation import golden, judge, settings

    s = settings()
    parser = argparse.ArgumentParser(description="Judge golden answers with the configured Claude model.")
    parser.add_argument("answers", type=Path, help="answers-<mode>-<ts>.json from scripts/eval_answers.py")
    parser.add_argument("--batch-id", default=None, help="collect this existing batch instead of submitting")
    parser.add_argument("--dataset", type=Path, default=ROOT / s["golden"]["dataset"])
    parser.add_argument("--calibration", type=Path, default=ROOT / s["golden"]["calibration"])
    parser.add_argument("--report-dir", type=Path, default=ROOT / s["reports_dir"])
    parser.add_argument("--write-payloads", type=Path, default=None,
                        help="write the per-item judge payloads (JSONL) for subagent judges, and the rubric beside it; "
                             "nothing is judged")
    parser.add_argument("--verdicts-file", type=Path, default=None,
                        help="subagent backend: read verdicts (JSONL: {id, verdict}) instead of calling the API")
    parser.add_argument("--fake-client", type=Path, nargs="?", const=Path(), default=None,
                        help="tests only (DEVGUARD_TEST_BACKENDS=1): fixed entries from a JSON file, else all supported")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        raw = args.answers.read_bytes()
        answers = json.loads(raw)
        data = golden.load_dataset(args.dataset)
    except Exception as exc:  # noqa: BLE001
        print(f"error: input unreadable: {type(exc).__name__}", file=sys.stderr)
        return 1
    if answers.get("dataset_version") != data.get("dataset_version"):
        print(f"error: answers file is for dataset {answers.get('dataset_version')}, the dataset is "
              f"{data.get('dataset_version')}", file=sys.stderr)
        return 1
    items = {i["id"]: i for i in data["items"]}
    records = answers.get("records", [])
    unknown = [r["id"] for r in records if r["id"] not in items]
    if not records or unknown:
        print(f"error: no records, or records not in the dataset: {unknown[:5]}", file=sys.stderr)
        return 1

    if args.write_payloads:
        lines = judge.payload_lines(items, records)
        args.write_payloads.parent.mkdir(parents=True, exist_ok=True)
        args.write_payloads.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rubric = args.write_payloads.with_suffix(".rubric.txt")
        rubric.write_text(f"{judge.SYSTEM}\n\nVerdict JSON schema:\n{json.dumps(judge.VERDICT_SCHEMA, indent=1)}\n",
                          encoding="utf-8")
        print(f"wrote {len(lines)} payloads to {args.write_payloads} and the rubric to {rubric}")
        return 0

    if args.verdicts_file is not None:
        try:
            client: Any = judge.FileVerdicts(args.verdicts_file)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: verdicts file unreadable: {type(exc).__name__}", file=sys.stderr)
            return 1
    elif args.fake_client is not None:
        if os.environ.get("DEVGUARD_TEST_BACKENDS") != "1":
            print("error: --fake-client is for tests only (set DEVGUARD_TEST_BACKENDS=1)", file=sys.stderr)
            return 1
        entries = json.loads(args.fake_client.read_text(encoding="utf-8")) if args.fake_client.is_file() else {}
        client = judge.FakeBatches(entries)
    else:
        try:
            client = judge.AnthropicBatches()
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    requests = [{"custom_id": r["id"], "params": judge.request_params(items[r["id"]], r)} for r in records]
    try:
        if args.batch_id and not isinstance(client, judge.FileVerdicts):
            batch_id = args.batch_id
            if isinstance(client, judge.FakeBatches):
                client.prime(requests)                 # the fake has no server-side batch to read back
            print(f"collecting batch {batch_id} (no new submit)", flush=True)
        else:
            batch_id = client.submit(requests)
            if isinstance(client, judge.FileVerdicts):
                print(f"reading {len(client.entries)} verdicts from {args.verdicts_file.name}"
                      + (f" ({client.ignored} line(s) ignored)" if client.ignored else ""), flush=True)
            else:
                print(f"submitted {len(requests)} requests as batch {batch_id}; if interrupted, re-run with "
                      f"--batch-id {batch_id}", flush=True)
        while not client.ended(batch_id):
            time.sleep(s["judge"]["poll_seconds"])
        entries = {e["custom_id"]: e for e in client.results(batch_id)}
    except KeyboardInterrupt:
        print("interrupted; nothing written. Resume with --batch-id.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - SDK errors: class name only, never the message (may echo request data)
        print(f"error: batch call failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    verdicts = {r["id"]: judge.parse_entry(r["id"], entries.get(r["id"]),
                                           [c["citation_id"] for c in r.get("citations", [])]) for r in records}
    metrics = judge.metrics(records, verdicts)
    try:
        labels, graded_by = load_labels(args.calibration)
        cal = judge.calibration(labels, records, verdicts, graded_by)
    except Exception as exc:  # noqa: BLE001
        cal = {"status": "unreadable", "error": type(exc).__name__}
    usage = {k: sum(v.usage.get(k, 0) for v in verdicts.values()) for k in ("input_tokens", "output_tokens")}
    j = s["judge"]
    report = {"mode": answers.get("mode"), "dataset_version": data.get("dataset_version"),
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "answers_file": args.answers.name, "answers_sha256": hashlib.sha256(raw).hexdigest(),
              "model": j["model"], "effort": j["effort"], "prompt_version": j["prompt_version"], "batch_id": batch_id,
              "fake_client": isinstance(client, judge.FakeBatches),
              "backend": ("fake" if isinstance(client, judge.FakeBatches) else
                          judge.FileVerdicts.BACKEND if isinstance(client, judge.FileVerdicts) else "anthropic-batches"),
              **({"verdicts_file": args.verdicts_file.name, "verdicts_sha256": hashlib.sha256(client.raw).hexdigest(),
                  "verdict_lines_ignored": client.ignored} if isinstance(client, judge.FileVerdicts) else {}),
              "usage": usage, "metrics": metrics,
              "calibration": cal, "items": [verdicts[r["id"]].as_dict() for r in records]}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"judge-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{metrics['items']} items judged by {j['model']} ({answers.get('mode')} answers): "
          f"golden_answer_supported {metrics['golden_answer_supported']}, citation_support_precision "
          f"{metrics['citation_support_precision']}, not judged {metrics['not_judged']}; calibration {cal['status']}"
          + (f" (agreement {cal['percent_agreement']}, kappa {cal['cohens_kappa']}"
             + ("" if cal.get("human_graded") else "; MODEL-GRADED, not an independent check") + ")"
             if cal["status"] == "measured" else "")
          + f"  report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
