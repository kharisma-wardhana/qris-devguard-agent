"""Licensed sources stay out of git, and the excerpt limit exists (spec AC #9, #10)."""

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


def test_reference_only_excerpt_limit_defined():
    config = yaml.safe_load((ROOT / "configs" / "security.yaml").read_text(encoding="utf-8"))
    limit = config["citations"]["max_excerpt_chars"]["reference_only"]
    assert isinstance(limit, int) and 0 < limit <= 500
