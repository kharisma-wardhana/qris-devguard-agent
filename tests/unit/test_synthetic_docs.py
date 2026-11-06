"""Synthetic documents are labelled and the MPM spec is complete (spec AC #5, #6)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIR = ROOT / "knowledge_base" / "synthetic"
MANIFEST = ROOT / "knowledge_base" / "manifests" / "sources.yaml"
MARKER = "(SYNTHETIC — not an official ASPI/BI document)"
MPM_SPEC = SYNTHETIC_DIR / "synthetic-qris-mpm-service-spec.md"

ENDPOINT_SECTIONS = ["Generate QR", "Query Payment"]
REQUIRED_SUBSECTIONS = [
    "Method and path",
    "Required headers",
    "Request fields",
    "Response fields",
    "Signature profile",
    "Response codes",
]


def synthetic_files() -> list[Path]:
    files = set(SYNTHETIC_DIR.glob("*.md"))
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for s in manifest["sources"]:
        if s["document_id"].startswith("synthetic-") and s.get("local_path"):
            files.add(ROOT / s["local_path"])
    return sorted(files)


def test_synthetic_documents_exist():
    assert synthetic_files(), "no synthetic documents found"


@pytest.mark.parametrize("path", synthetic_files(), ids=lambda p: p.name)
def test_first_line_carries_marker(path):
    assert path.is_file(), f"{path} listed in manifest but missing"
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert MARKER in first_line


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## \d+\. {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"section '{title}' missing"
    return match.group(1)


@pytest.mark.parametrize("endpoint", ENDPOINT_SECTIONS)
def test_mpm_spec_endpoint_has_required_subsections(endpoint):
    body = _section(MPM_SPEC.read_text(encoding="utf-8"), endpoint)
    headings = set(re.findall(r"^### (.+?)\s*$", body, re.M))
    missing = [h for h in REQUIRED_SUBSECTIONS if h not in headings]
    assert not missing, f"{endpoint} missing: {missing}"


def test_mpm_spec_defines_string_to_sign():
    text = MPM_SPEC.read_text(encoding="utf-8")
    assert "stringToSign" in text and "HMAC_SHA512" in text
