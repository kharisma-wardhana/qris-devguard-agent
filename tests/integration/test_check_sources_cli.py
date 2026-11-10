"""Run the manifest checker at its real entry point (spec AC #1, #2)."""

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_sources.py"
MANIFEST = ROOT / "knowledge_base" / "manifests" / "sources.yaml"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )


def test_committed_manifest_exits_zero():
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK: 5 sources, 5 approved")


def test_invalid_manifest_exits_one(tmp_path):
    bad = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    bad["sources"][0]["permission"]["evidence"] = None
    bad["sources"].append(dict(bad["sources"][1]))  # duplicate document_id
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")

    result = run(str(path))
    assert result.returncode == 1
    assert "duplicate document_id" in result.stdout
    assert "permission.evidence" in result.stdout


def test_committed_manifest_has_exactly_the_agreed_sources():
    sources = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["sources"]
    status = {s["document_id"]: s["approval_status"] for s in sources}
    assert status == {
        "snap-technical-security-standard": "approved",
        "snap-data-technical-spec": "approved",
        "synthetic-qris-mpm-service-spec": "approved",
        "synthetic-qris-troubleshooting-sop": "approved",
        "synthetic-test-fixtures": "approved",          # p4: fixture index
    }
    by_id = {s["document_id"]: s for s in sources}
    for doc in ("snap-technical-security-standard", "snap-data-technical-spec"):
        assert by_id[doc]["permission"]["basis"] == "reference_only"
        assert by_id[doc]["document_version"] == "1.0.2"
