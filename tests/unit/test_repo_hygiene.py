"""Licensed sources stay out of git, and the excerpt limit exists (spec AC #9, #10)."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_only_gitkeep_is_tracked_under_sources():
    result = subprocess.run(
        ["git", "ls-files", "knowledge_base/sources"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    tracked = result.stdout.split()
    assert tracked == ["knowledge_base/sources/.gitkeep"], tracked


def _tracked_text_files():
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    for name in result.stdout.splitlines():
        path = ROOT / name
        if name == "uv.lock" or not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


# An IPv4 literal not preceded by ':' (image tags such as clickhouse-server:26.3.62.3) or a version 'v'.
IPV4 = re.compile(r"(?<![\w:.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0"}


def test_no_public_ip_or_runpod_ssh_target_tracked():
    """Pod IPs, ports and ssh.runpod.io users belong in .env only (p1 spec AC #5)."""
    offenders = []
    for name, text in _tracked_text_files():
        offenders += [f"{name}: {ip}" for ip in IPV4.findall(text) if ip not in ALLOWED_IPS]
        offenders += [f"{name}: runpod ssh user" for _ in re.findall(r"[\w-]+@ssh\.runpod\.io", text)]
    assert not offenders, offenders


def test_reference_only_excerpt_limit_defined():
    config = yaml.safe_load((ROOT / "configs" / "security.yaml").read_text(encoding="utf-8"))
    limit = config["citations"]["max_excerpt_chars"]["reference_only"]
    assert isinstance(limit, int) and 0 < limit <= 500
