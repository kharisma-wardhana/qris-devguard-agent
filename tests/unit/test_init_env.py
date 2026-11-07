"""init_env.py creates a complete .env without leaking or overwriting (spec AC #5)."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init_env.py"
EXAMPLE = ROOT / ".env.example"


def run(out: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--example", str(EXAMPLE), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )


def parse(path: Path) -> dict[str, str]:
    pairs = (l.split("=", 1) for l in path.read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
    return {k: v for k, v in pairs}


def test_creates_env_with_every_placeholder_filled(tmp_path):
    out = tmp_path / ".env"
    result = run(out)
    assert result.returncode == 0, result.stderr
    env = parse(out)
    assert not [k for k, v in env.items() if "replace-me" in v]
    assert re.fullmatch(r"[0-9a-f]{64}", env["ENCRYPTION_KEY"])
    assert env["LANGFUSE_PUBLIC_KEY"].startswith("pk-lf-")
    assert env["LANGFUSE_SECRET_KEY"].startswith("sk-lf-")
    assert f"devguard:{env['POSTGRES_PASSWORD']}@127.0.0.1" in env["DATABASE_URL"]
    assert env["RUNPOD_SSH_HOST"] == "set-me"  # user-provided values are untouched
    generated = [v for k, v in env.items() if k in ("POSTGRES_PASSWORD", "REDIS_AUTH", "VLLM_API_KEY", "SALT")]
    assert len(set(generated)) == len(generated)  # independent secrets


def test_never_prints_generated_values(tmp_path):
    out = tmp_path / ".env"
    result = run(out)
    env = parse(out)
    secret_keys = [k for k, v in parse(EXAMPLE).items() if v == "replace-me"]
    for key in secret_keys:
        assert env[key] not in result.stdout + result.stderr


def test_refuses_to_overwrite(tmp_path):
    out = tmp_path / ".env"
    out.write_text("KEEP=me\n", encoding="utf-8")
    result = run(out)
    assert result.returncode == 1
    assert out.read_text(encoding="utf-8") == "KEEP=me\n"
