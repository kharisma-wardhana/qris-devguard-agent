"""Knowledge-pipeline CLIs run at their real entry points (p2 spec AC #10; plan steps 5-8)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script: str, *args: str, env_extra: dict | None = None, cwd: Path = ROOT):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=300)


# --- fetch_models.py (step 5) ------------------------------------------------

def test_fetch_models_dry_run_prints_pinned_revision():
    result = run("fetch_models.py", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "BAAI/bge-m3" in result.stdout
    assert "5617a9f61b028005a4858fdac845db406aefb181" in result.stdout
    assert "nothing downloaded" in result.stdout


def test_fetch_models_without_embed_stack_fails_with_one_line(tmp_path):
    # Shadow the optional packages with modules that fail to import, whether or not the extra is installed.
    for name in ("truststore", "huggingface_hub"):
        (tmp_path / f"{name}.py").write_text("raise ImportError('shadowed for test')\n", encoding="utf-8")
    result = run("fetch_models.py", env_extra={"PYTHONPATH": str(tmp_path)})
    assert result.returncode == 2
    assert result.stderr.strip() == "error: embedding stack not installed: run `uv sync --extra embed`"
    assert "Traceback" not in result.stderr
