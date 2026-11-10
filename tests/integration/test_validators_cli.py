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
