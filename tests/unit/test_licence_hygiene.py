"""No tracked file carries 300+ consecutive characters of ASPI text (p2 spec AC #14; threat T16).

Runs only where the reference-only PDFs are present (the developer machine); in CI it is
skipped with a reason. Text is compared after lowercasing and collapsing whitespace, using
64-character fingerprints: a run of overlapping matching windows spanning >= 300 characters
fails the test. Extracted PDF text is cached next to the PDFs, inside the git-ignored
knowledge_base/sources/, so it never leaves that folder.
"""

import re
import subprocess
from pathlib import Path

import pytest

from app.rag import config, manifest

ROOT = Path(__file__).resolve().parents[2]
WINDOW = 64
LIMIT = config.excerpt_limit("reference_only")            # 300
ENTRIES = [e for e in manifest.load_manifest(ROOT / "knowledge_base" / "manifests" / "sources.yaml")
           if (e.get("permission") or {}).get("basis") == "reference_only" and e.get("local_path")]
PDFS = [ROOT / e["local_path"] for e in ENTRIES if (ROOT / e["local_path"]).is_file()]

pytestmark = pytest.mark.skipif(not PDFS, reason="reference-only PDFs not present (they are never in CI or git)")


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def pdf_text(path: Path) -> str:
    cache = path.with_name(f".{path.stem}.{manifest.sha256_file(path)[:16]}.txt")
    if cache.is_file():
        return cache.read_text(encoding="utf-8")
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        text = normalise(" ".join(page.extract_text() or "" for page in pdf.pages))
    cache.write_text(text, encoding="utf-8")
    return text


def longest_shared_run(text: str, fingerprints: set[int]) -> int:
    longest, run_start, prev_hit = 0, None, False
    for i in range(0, max(len(text) - WINDOW + 1, 0)):
        hit = hash(text[i:i + WINDOW]) in fingerprints
        if hit and not prev_hit:
            run_start = i
        if hit:
            longest = max(longest, i - run_start + WINDOW)
        prev_hit = hit
    return longest


def tracked_text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    for rel in filter(None, out.decode("utf-8").split("\0")):
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() in {".pdf", ".png", ".jpg", ".lock"}:
            continue
        try:
            yield rel, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def test_no_tracked_file_contains_aspi_text():
    source = " ".join(pdf_text(p) for p in PDFS)
    assert len(source) > 10_000, "PDF text extraction returned too little to compare"
    fingerprints = {hash(source[i:i + WINDOW]) for i in range(len(source) - WINDOW + 1)}
    offenders = []
    for rel, text in tracked_text_files():
        run = longest_shared_run(normalise(text), fingerprints)
        if run >= LIMIT:
            offenders.append(f"{rel} ({run} chars)")
    assert not offenders, "ASPI text in tracked files: " + ", ".join(offenders)


def test_detector_catches_a_planted_copy():
    """The check must be able to fail: a 320-char slice of the PDF text is detected."""
    source = " ".join(pdf_text(p) for p in PDFS)
    fingerprints = {hash(source[i:i + WINDOW]) for i in range(len(source) - WINDOW + 1)}
    planted = "notes before. " + source[5000:5320] + " notes after."
    assert longest_shared_run(normalise(planted), fingerprints) >= LIMIT
    assert longest_shared_run(normalise("X-SIGNATURE " * 40), fingerprints) < LIMIT
