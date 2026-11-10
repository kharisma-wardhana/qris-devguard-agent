"""Validator scripts at their real entry points (p4 spec AC #9, #12, #14)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script: str, *args: str, env: dict | None = None):
    import os
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, **(env or {})})


def test_gen_test_keys_writes_index_and_prints_no_key_material(tmp_path):
    res = run("gen_test_keys.py", "--out", str(tmp_path))
    assert res.returncode == 0, res.stderr
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index == {"syn-hmac-01": "hmac", "syn-hmac-02": "hmac", "syn-rsa-01": "rsa", "syn-rsa-02": "rsa"}
    secret = (tmp_path / "syn-hmac-01.hmac").read_text(encoding="ascii").strip()
    assert secret not in res.stdout + res.stderr
    assert "PRIVATE KEY" not in res.stdout + res.stderr
    again = run("gen_test_keys.py", "--out", str(tmp_path))
    assert again.returncode == 0 and "wrote 0" in again.stdout


def test_extract_response_codes_refuses_missing_pdf(tmp_path):
    res = run("extract_response_codes.py", "--root", str(tmp_path), "--out", str(tmp_path / "t.json"))
    assert res.returncode == 1
    assert res.stderr.strip().startswith("error: PDF not found") and "Traceback" not in res.stderr
    assert not (tmp_path / "t.json").exists()


def test_extract_response_codes_refuses_checksum_mismatch(tmp_path):
    fake = tmp_path / "knowledge_base" / "sources" / "snap-standar-data-spesifikasi-teknis-1.0.2.pdf"
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"%PDF-1.4\n% synthetic stand-in, not the real document\n")
    res = run("extract_response_codes.py", "--root", str(tmp_path), "--out", str(tmp_path / "t.json"))
    assert res.returncode == 1
    assert "checksum mismatch" in res.stderr and "Traceback" not in res.stderr
    assert not (tmp_path / "t.json").exists()
