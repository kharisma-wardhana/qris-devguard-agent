"""Deployment manifest: fill local fields, merge the fresh-clone setup report, check completeness (p9-final AC #27).

Usage:
  uv run python scripts/deploy_manifest.py --fill                          # model, vLLM version, uv.lock hash, image digests
  uv run python scripts/deploy_manifest.py --setup-report <setup-*.json>   # from the fresh clone's scripts/setup.sh run
  uv run python scripts/deploy_manifest.py --check                         # what is still missing (exit 0 when complete)

Pod-side values (gpu.sku/driver/cuda, vllm.image, fresh_pod.*) are typed into the YAML by the developer from the pod
shell. The file must never hold an IP, a pod address or a secret; --check refuses those.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "docs" / "p9-final-evaluation-packaging" / "deployment-manifest.yaml"


def main(argv: list[str] | None = None) -> int:
    from app.evaluation import manifest

    parser = argparse.ArgumentParser(description="Fill and check the deployment manifest.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--fill", action="store_true", help="write the locally readable fields")
    parser.add_argument("--setup-report", type=Path, default=None, help="merge a setup-<ts>.json from a fresh clone")
    parser.add_argument("--check", action="store_true", help="report missing fields and identifier problems")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if not (args.fill or args.setup_report or args.check):
        parser.print_usage(sys.stderr)
        print("error: choose --fill, --setup-report or --check", file=sys.stderr)
        return 2

    try:
        data = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) if args.manifest.is_file() else manifest.template()
    except yaml.YAMLError:
        print("error: manifest is not valid YAML", file=sys.stderr)
        return 1
    changed = False
    if args.fill:
        data = manifest.fill_local(data, manifest.local_fields())
        missing = [n for n, v in data["images"].items() if not v.get("digest")]
        print(f"filled model, vLLM version, uv.lock hash and {len(data['images'])} images"
              + (f"; no local digest for: {', '.join(missing)} (pull or start them, then --fill again)" if missing else ""))
        changed = True
    if args.setup_report:
        try:
            raw = args.setup_report.read_bytes()
            data = manifest.merge_setup_report(data, json.loads(raw), raw)
        except (OSError, ValueError):
            print("error: setup report unreadable", file=sys.stderr)
            return 1
        print(f"merged fresh-clone run from {args.setup_report.name}")
        changed = True
    if changed:
        data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(manifest.dump(data), encoding="utf-8")
    if args.check:
        found = manifest.problems(data, datetime.now(timezone.utc))
        for p in found:
            print(f"missing/problem: {p}")
        print("manifest complete" if not found else f"manifest incomplete: {len(found)} problem(s)")
        return 0 if not found else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
