"""scripts/scan_markers.py as a subprocess (p8 spec AC #24): counts per document, never chunk text."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/scan_markers.py"
PLANTED = ROOT / "tests/fixtures/injection/planted-sections.md"
ROW = re.compile(r"^[a-z0-9-]+ [0-9A-Za-z.\-]+ chunks=\d+ matched_chunks=\d+ sentences=\d+ "
                 r"categories=(?:-|(?:[a-z_]+:\d+,?)+)$")
TOTAL = re.compile(r"^total documents=\d+ chunks=\d+ matched_chunks=\d+ sentences=\d+$")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=300,
                          cwd=ROOT)


def rows(stdout):
    lines = stdout.strip().splitlines()
    assert lines[0].startswith("injection-marker scan over the harness corpus")
    assert TOTAL.match(lines[-1]), lines[-1]
    body = lines[1:-1]
    assert body and all(ROW.match(x) for x in body), body          # IDs, versions and counts only
    return {tuple(x.split()[:2]): x for x in body}


def total_sentences(stdout):
    return int(stdout.strip().splitlines()[-1].rsplit("sentences=", 1)[1])


def test_harness_corpus_counts_per_document_and_no_text():
    r = run("--harness")
    assert r.returncode == 0, r.stderr
    got = rows(r.stdout)
    assert ("synthetic-qris-mpm-service-spec", "0.1.0") in got
    # Measured on the committed corpus (p8 step 8): one SOP line telling the reader to run a validator matches
    # tool_invocation, a known false positive reported in the observability report. The MPM spec is clean.
    assert "matched_chunks=0" in got[("synthetic-qris-mpm-service-spec", "0.1.0")]
    assert "tool_invocation:1" in got[("synthetic-qris-troubleshooting-sop", "0.1.0")]


def test_planted_attack_text_is_counted_and_never_printed():
    clean = total_sentences(run("--harness").stdout)
    r = run("--harness", "--plant", str(PLANTED))
    assert r.returncode == 0, r.stderr
    got = rows(r.stdout)
    mpm = got[("synthetic-qris-mpm-service-spec", "0.1.0")]
    assert "matched_chunks=0" not in mpm and "override:" in mpm and "approval_claim:" in mpm
    assert total_sentences(r.stdout) > clean
    for text in ("INJ-CANARY", "run_sql", "pre-granted", "ignore its"):
        assert text not in r.stdout + r.stderr


def test_plant_without_harness_is_refused():
    r = run("--plant", str(PLANTED))
    assert r.returncode == 2 and "--plant needs --harness" in r.stderr
