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
# Document section numbers such as `section_id: "4.2.2.7"` (p2 retrieval set) look like IPv4
# addresses; a quoted section_id value is the one context excluded.
IPV4 = re.compile(r'(?<![\w:.])(?<!section_id: ")(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
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


def test_ip_pattern_skips_quoted_section_ids_but_still_catches_ips():
    """Regression (p2 step 12): a quoted section_id like 4.2.2.x is not an IP; real IPs still are.
    Literals are assembled at run time so this file itself contains no IP-shaped string."""
    ip = ".".join(["203", "0", "113", "7"])          # TEST-NET-3 documentation address
    sec = ".".join(["4", "2", "2", "7"])
    assert IPV4.findall(f'gold: [{{ document_id: snap-data-technical-spec, section_id: "{sec}" }}]') == []
    assert IPV4.findall(f"RUNPOD_SSH_HOST={ip}") == [ip]
    assert IPV4.findall(f'host: "{ip}"  section: {sec}') == [ip, sec]
