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


# --- p7: --add-missing --------------------------------------------------------------------------------

def run_add(out: Path):
    return subprocess.run([sys.executable, str(SCRIPT), "--example", str(EXAMPLE), "--out", str(out), "--add-missing"],
                          capture_output=True, text=True, encoding="utf-8")


ROLE_KEYS = ("DEVGUARD_APP_DB_PASSWORD", "DEVGUARD_TICKET_DB_PASSWORD", "DEVGUARD_AUDIT_DB_PASSWORD")


def test_add_missing_appends_only_absent_keys_and_keeps_bytes(tmp_path):
    out = tmp_path / ".env"
    assert run(out).returncode == 0
    lines = out.read_bytes().decode("utf-8").splitlines(keepends=True)
    old = [l for l in lines if l.split("=", 1)[0] not in ROLE_KEYS]      # a pre-P7 .env
    old[0] = old[0].rstrip("\n") + "\r\n"                                # odd line ending must survive
    before = "".join(old).encode("utf-8")
    out.write_bytes(before)
    r = run_add(out)
    assert r.returncode == 0, r.stderr
    after = out.read_bytes()
    assert after.startswith(before)                                       # every existing byte unchanged
    env = parse(out)
    for key in ROLE_KEYS:
        assert re.fullmatch(r"[0-9a-f]{48}", env[key].strip())
        assert env[key].strip() not in r.stdout + r.stderr               # names printed, never values
        assert key in r.stdout
        assert after.decode("utf-8").count(f"{key}=") == 1


def test_add_missing_twice_adds_nothing(tmp_path):
    out = tmp_path / ".env"
    run(out)
    before = out.read_bytes()
    r = run_add(out)
    assert r.returncode == 0 and "nothing added" in r.stdout and out.read_bytes() == before


def test_add_missing_never_changes_an_existing_value(tmp_path):
    out = tmp_path / ".env"
    out.write_text("DEVGUARD_APP_DB_PASSWORD=mine\nMCP_AUTH_SECRET=keep\n", encoding="utf-8")
    run_add(out)
    env = parse(out)
    assert env["DEVGUARD_APP_DB_PASSWORD"] == "mine" and env["MCP_AUTH_SECRET"] == "keep"


def test_without_flag_still_refuses(tmp_path):
    out = tmp_path / ".env"
    out.write_text("KEEP=me\n", encoding="utf-8")
    assert run(out).returncode == 1 and out.read_text(encoding="utf-8") == "KEEP=me\n"
