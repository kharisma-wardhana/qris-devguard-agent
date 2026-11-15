"""Deployment manifest for `reproducible_deployment` (p9-final spec Behaviour §5; AC #27).

The manifest holds what TRD §26.2 step 15 asks for: GPU SKU, driver and CUDA, image digests, model and revision,
quantization, context length, vLLM version and the dependency lockfile hash. It also records the two rebuild runs:
a fresh clone set up with scripts/setup.sh (health check 7/7) and a fresh pod created only from the README.

The gate passes only when every required field is filled, the fresh-clone run is within the freshness window, the
fresh pod followed the README, no unlisted step was needed, and the file carries no IP, pod address or secret.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from app.evaluation import ROOT, settings

REQUIRED = {
    "gpu": ("sku", "driver", "cuda"),
    "model": ("id", "revision", "quantization", "max_model_len"),
    "vllm": ("version", "image"),
    "lockfile": ("file", "sha256"),
    "fresh_clone": ("date", "commit", "setup_report_sha256", "healthcheck_all_pass", "checks_passed"),
    "fresh_pod": ("date", "followed_readme"),
}
IPV4 = re.compile(r"(?<![\w.:])" + r"\.".join([r"\d{1,3}"] * 4) + r"(?![\w.])")
POD_ADDRESS = re.compile(r"(?i)(?:[\w-]+@)?[\w-]+\.(?:runpod\.(?:io|net)|proxy\.runpod\.net)|ssh\.runpod")


def template() -> dict[str, Any]:
    return {"manifest_version": 1, "updated_at": None,
            "gpu": {"sku": None, "driver": None, "cuda": None, "data_center": None},
            "model": {"id": None, "revision": None, "served_name": None, "quantization": None, "max_model_len": None},
            "vllm": {"version": None, "image": None},
            "images": {},
            "lockfile": {"file": "uv.lock", "sha256": None},
            "fresh_clone": {"date": None, "commit": None, "setup_report_sha256": None, "healthcheck_all_pass": None,
                            "checks_passed": None},
            "fresh_pod": {"date": None, "followed_readme": None},
            "unlisted_steps": []}


def local_fields(root: Path = ROOT) -> dict[str, Any]:
    """Everything readable on this machine without a credential: model identity, vLLM version, lockfile hash and
    the compose images (tag, and the local digest when podman has the image)."""
    m = yaml.safe_load((root / "configs" / "model.yaml").read_text(encoding="utf-8"))
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    images = {}
    for name, svc in sorted((compose.get("services") or {}).items()):
        if isinstance(svc, dict) and svc.get("image"):
            images[name] = {"image": svc["image"], "digest": image_digest(svc["image"])}
    return {"model": {"id": m["model"]["id"], "revision": m["model"]["revision"], "served_name": m["model"]["served_name"],
                      "quantization": m["model"]["quantization"], "max_model_len": m["vllm"]["max_model_len"]},
            "vllm_version": m["vllm"]["version"],
            "lockfile_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(), "images": images}


def image_digest(image: str) -> str | None:
    if "@sha256:" in image:
        return image.split("@", 1)[1]
    try:
        out = subprocess.run(["podman", "image", "inspect", "--format", "{{.Digest}}", image], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    digest = out.stdout.strip()
    return digest if out.returncode == 0 and digest.startswith("sha256:") else None


def fill_local(data: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    data = {**template(), **(data or {})}
    data["model"] = {**data["model"], **local["model"]}
    data["vllm"] = {**data["vllm"], "version": local["vllm_version"]}
    data["lockfile"] = {"file": "uv.lock", "sha256": local["lockfile_sha256"]}
    data["images"] = local["images"]
    return data


def merge_setup_report(data: dict[str, Any], report: dict[str, Any], raw: bytes) -> dict[str, Any]:
    """Fresh-clone fields from scripts/setup.sh's setup report."""
    hc = report.get("healthcheck") or {}
    data = {**template(), **(data or {})}
    data["fresh_clone"] = {"date": report.get("finished_at"), "commit": report.get("commit"),
                           "setup_report_sha256": hashlib.sha256(raw).hexdigest(),
                           "healthcheck_all_pass": hc.get("all_pass"), "checks_passed": hc.get("checks_passed"),
                           "fresh_clone": report.get("fresh_clone"), "mode": report.get("mode")}
    return data


def identifier_problems(data: dict[str, Any]) -> list[str]:
    from app.agent.sanitize import redact_text

    text = yaml.safe_dump(data, allow_unicode=True)
    problems = []
    if [ip for ip in IPV4.findall(text) if ip not in ("127.0.0.1", "0.0.0.0")]:
        problems.append("contains an IPv4 address")
    if POD_ADDRESS.search(text):
        problems.append("contains a pod address or RunPod host")
    if redact_text(text)[1]:
        problems.append("contains a secret-shaped value")
    return problems


def problems(data: dict[str, Any], now: datetime) -> list[str]:
    out = []
    for section, keys in REQUIRED.items():
        block = data.get(section) or {}
        out += [f"{section}.{k} is empty" for k in keys if block.get(k) in (None, "")]
    images = data.get("images") or {}
    if not images:
        out.append("images is empty")
    out += [f"images.{n} has no digest" for n, v in images.items() if not (v or {}).get("digest")]
    fc, fp = data.get("fresh_clone") or {}, data.get("fresh_pod") or {}
    if fc.get("healthcheck_all_pass") is False or (fc.get("checks_passed") not in (None, "") and int(fc["checks_passed"]) < 7):
        out.append("fresh-clone health check was not 7/7 PASS")
    if fc.get("fresh_clone") is False:
        out.append("setup report is not from a fresh clone")
    if fc.get("mode") == "synthetic-only":
        out.append("setup ran --synthetic-only, which does not reproduce the measured results")
    if fp.get("followed_readme") is False:
        out.append("fresh pod needed steps outside the README")
    if data.get("unlisted_steps"):
        out.append(f"{len(data['unlisted_steps'])} unlisted step(s) were needed; fix the README")
    out += identifier_problems(data)
    if not out and fc.get("date"):
        try:
            when = datetime.fromisoformat(str(fc["date"]).replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if now - when > timedelta(days=settings()["freshness_days"]):
                out.append("stale")
        except ValueError:
            out.append("fresh_clone.date is not an ISO date")
    return out


def evaluate(path: Path, now: datetime) -> tuple[bool, list[str], str | None]:
    """(complete, problems, fresh-clone date) for the release gate. problems == ["stale"] means STALE."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False, ["manifest unreadable"], None
    found = problems(data, now)
    date = (data.get("fresh_clone") or {}).get("date")
    return not found, found, None if date is None else str(date)


def dump(data: dict[str, Any]) -> str:
    header = ("# Deployment manifest (docs/p9-final-evaluation-packaging/spec.md Behaviour §5; TRD §26.2 step 15).\n"
              "# Local fields: `uv run python scripts/deploy_manifest.py --fill`. Pod fields (gpu.*, vllm.image,\n"
              "# fresh_pod.*) are pasted by the developer from the pod shell. Versions, digests and hashes only:\n"
              "# never an IP, pod ID, hostname or secret (checked by `--check` and the release gate).\n")
    return header + yaml.safe_dump(json.loads(json.dumps(data)), sort_keys=False, allow_unicode=True)
