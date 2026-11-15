"""scripts/setup.sh run for real under bash, with a fake toolchain on PATH (p9-final spec AC #1-#6).

The script is copied into a temp "clone" with a minimal manifest. Fake `uv`, `podman` and `git` log every call
and fail on request (FAKE_FAIL); only `uv run python -m app.evaluation.setup_checks pdfs|synthetic-manifest` is
passed through to the real Python, so the PDF check runs for real. Nothing touches the developer's repo, .env,
containers or network. Skipped where no Git-for-Windows/Linux bash exists (never WSL's bash.exe).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or "system32" in (BASH or "").lower(),
                                reason="needs a Git-for-Windows or Linux bash")
PDF_BYTES = b"%PDF-1.7 synthetic test file\n"
SENTINEL = "SENTINEL-" + "do-not-print-0123456789"
HEALTH_OK_EXCEPT_VLLM = {"ts": "t", "all_pass": False, "checks": [
    *({"check": c, "status": "FAIL", "ms": 1} for c in ("vllm_health", "vllm_models", "vllm_chat")),
    *({"check": c, "status": "PASS", "ms": 1} for c in ("postgres", "qdrant", "langfuse", "mcp"))]}
HEALTH_ALL = {"ts": "t", "all_pass": True, "checks": [{"check": c, "status": "PASS", "ms": 1} for c in
                                                      ("vllm_health", "vllm_models", "vllm_chat", "postgres", "qdrant",
                                                       "langfuse", "mcp")]}

FAKE = r"""#!/usr/bin/env bash
name="$(basename "$0")"
echo "$name $*" >> "$FAKE_LOG"
if [ -n "${FAKE_FAIL:-}" ] && [[ "$name $*" == *"$FAKE_FAIL"* ]]; then exit 1; fi
if [ "$name" = git ] && [ "${1:-}" = rev-parse ]; then echo abc1234; exit 0; fi
all="$*"
if [ "$name" = uv ] && [[ "$all" == "run python -m app.evaluation.setup_checks "* ]]; then
  sub="${5:-}"; shift 2
  if [ "$sub" = pdfs ] || [ "$sub" = synthetic-manifest ]; then
    PYTHONPATH="$REAL_REPO" exec "$REAL_PYTHON" "$@"
  fi
  exit 0
fi
if [ "$name" = uv ] && [ "$*" = "run python scripts/healthcheck.py" ]; then
  mkdir -p evals/reports; echo "$FAKE_HEALTH" >> evals/reports/healthcheck.jsonl; exit "${FAKE_HEALTH_EXIT:-1}"
fi
exit 0
"""


@pytest.fixture
def clone(tmp_path):
    repo = tmp_path / "clone"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "setup.sh", repo / "scripts" / "setup.sh")
    (repo / "scripts" / "tunnel.sh").write_text('echo "tunnel.sh" >> "$FAKE_LOG"\n', encoding="utf-8", newline="\n")
    (repo / "knowledge_base" / "manifests").mkdir(parents=True)
    (repo / "knowledge_base" / "sources").mkdir(parents=True)
    (repo / "knowledge_base" / "sources" / "snap-test-1.0.2.pdf").write_bytes(PDF_BYTES)
    manifest = {"sources": [
        {"document_id": "snap-test", "version": "1.0.2", "approval_status": "approved",
         "permission": {"basis": "reference_only"}, "source_url": "https://example.org/docs/snap-test",
         "local_path": "knowledge_base/sources/snap-test-1.0.2.pdf",
         "checksum_sha256": hashlib.sha256(PDF_BYTES).hexdigest()},
        {"document_id": "synthetic-doc", "approval_status": "approved", "permission": {"basis": "team_authored"},
         "local_path": "knowledge_base/synthetic/synthetic-doc.md"}]}
    (repo / "knowledge_base" / "manifests" / "sources.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    fakes = tmp_path / "fakebin"
    fakes.mkdir()
    for tool in ("uv", "podman", "git"):
        (fakes / tool).write_text(FAKE, encoding="utf-8", newline="\n")
        (fakes / tool).chmod(0o755)
    return repo, fakes, tmp_path / "calls.log"


def run(clone, *args, health=HEALTH_OK_EXCEPT_VLLM, health_exit=1, fail=None, env_text=None):
    repo, fakes, log = clone
    if env_text is not None:
        (repo / ".env").write_text(env_text, encoding="utf-8")
    env = {**os.environ, "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log),
           "REAL_PYTHON": sys.executable, "REAL_REPO": str(ROOT), "FAKE_HEALTH": json.dumps(health),
           "FAKE_HEALTH_EXIT": str(health_exit)}
    if fail:
        env["FAKE_FAIL"] = fail
    # bash sees the fake dir first only if the path is in its own format; pass it through a login-free -c wrapper
    cmd = [BASH, "-c", 'export PATH="$(cygpath -u "$FAKEBIN" 2>/dev/null || echo "$FAKEBIN"):$PATH"; '
                       'exec bash scripts/setup.sh "$@"', "setup", *args]
    env["FAKEBIN"] = str(fakes)
    r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return r, calls


def steps(stdout):
    return [ln for ln in stdout.splitlines() if ln.startswith("[")]


def report(repo):
    (path,) = sorted((repo / "evals" / "reports").glob("setup-*.json"))[-1:]
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_run_order_and_report(clone):
    r, calls = run(clone)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = steps(r.stdout)
    assert [ln.split("]")[0] + "]" for ln in lines] == [f"[{n}/13]" for n in range(1, 14)]
    assert "[12/13] SSH tunnel ... skipped (no pod configured)" in lines
    assert "vLLM checks FAIL as expected" in lines[-1]
    order = [c for c in calls if c.startswith("uv run python scripts/") or c.startswith("podman compose up")]
    assert order == ["uv run python scripts/init_env.py", "podman compose up -d", "uv run python scripts/db_migrate.py",
                     "uv run python scripts/gen_test_keys.py", "uv run python scripts/extract_response_codes.py",
                     "uv run python scripts/fetch_models.py", "uv run python scripts/ingest.py",
                     "uv run python scripts/check_sources.py", "uv run python scripts/healthcheck.py"]
    assert "uv sync --extra embed --extra ui" in calls
    rep = report(clone[0])
    assert rep["result"] == "done" and rep["mode"] == "full" and rep["fresh_clone"] is True
    assert rep["healthcheck"] == {"all_pass": False, "checks_passed": 4, "checks_total": 7}
    assert not any("runpodctl" in c or "tunnel.sh" in c for c in calls)


def test_second_run_only_reports_ok_or_skipped(clone):
    run(clone, env_text="EXAMPLE=1\n")
    r, calls = run(clone, env_text="EXAMPLE=1\n")
    assert r.returncode == 0
    for ln in steps(r.stdout):
        assert "... ok" in ln or "... skipped" in ln, ln
    assert "uv run python scripts/init_env.py --add-missing" in calls and "uv run python scripts/init_env.py" not in calls
    assert report(clone[0])["fresh_clone"] is False


@pytest.mark.parametrize("n,pattern", [(1, "podman compose version"), (2, "scripts/init_env.py"), (3, "uv sync"),
                                       (4, "podman compose up"), (5, "scripts/db_migrate.py"),
                                       (6, "scripts/gen_test_keys.py"), (8, "scripts/extract_response_codes.py"),
                                       (9, "scripts/fetch_models.py"), (10, "scripts/ingest.py"),
                                       (11, "scripts/check_sources.py")])
def test_failure_stops_at_that_step(clone, n, pattern):
    r, calls = run(clone, fail=pattern)
    assert r.returncode == 1
    lines = steps(r.stdout)
    assert lines[-1].startswith(f"[{n}/13]") and lines[-1].endswith("FAILED")
    assert "next:" in r.stderr
    later = ["scripts/healthcheck.py"] if n < 13 else []
    assert not any(any(x in c for x in later) for c in calls)
    assert report(clone[0])["result"] == "failed"


def test_health_check_failure_beyond_vllm_stops(clone):
    bad = {**HEALTH_OK_EXCEPT_VLLM, "checks": [dict(c, status="FAIL") if c["check"] == "postgres" else c
                                               for c in HEALTH_OK_EXCEPT_VLLM["checks"]]}
    r, _ = run(clone, health=bad)
    assert r.returncode == 1 and steps(r.stdout)[-1] == "[13/13] health check ... FAILED"


def test_missing_pdf_stops_with_instructions_and_no_download(clone):
    (clone[0] / "knowledge_base" / "sources" / "snap-test-1.0.2.pdf").unlink()
    r, calls = run(clone)
    assert r.returncode == 1 and steps(r.stdout)[-1] == "[7/13] reference PDFs ... FAILED"
    assert "https://example.org/docs/snap-test" in r.stderr and "knowledge_base/sources/snap-test-1.0.2.pdf" in r.stderr
    assert not any(tool in " ".join(calls) for tool in ("curl", "wget", "Invoke-WebRequest"))


def test_wrong_checksum_stops_and_names_the_entry(clone):
    (clone[0] / "knowledge_base" / "sources" / "snap-test-1.0.2.pdf").write_bytes(b"%PDF-1.7 another version\n")
    r, _ = run(clone)
    assert r.returncode == 1 and "CHECKSUM snap-test" in r.stderr


def test_quick_skips_embed_models_and_ingest(clone):
    r, calls = run(clone, "--quick")
    assert r.returncode == 0
    out = r.stdout
    assert "[9/13] embedding and reranker models ... skipped (--quick)" in out
    assert "[10/13] ingest ... skipped (--quick)" in out
    assert "uv sync --extra ui" in calls and not any("--extra embed" in c for c in calls)
    assert report(clone[0])["mode"] == "quick"


def test_synthetic_only_skips_pdfs_and_ingests_team_authored_only(clone):
    (clone[0] / "knowledge_base" / "sources" / "snap-test-1.0.2.pdf").unlink()
    r, calls = run(clone, "--synthetic-only")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[7/13] reference PDFs ... skipped (--synthetic-only)" in r.stdout
    assert "[8/13] response-code extract ... skipped (--synthetic-only)" in r.stdout
    assert "does not reproduce" in r.stdout.lower() or "team_authored sources only" in r.stdout
    ingest = [c for c in calls if "scripts/ingest.py" in c]
    assert ingest and "--manifest" in ingest[0]
    syn = yaml.safe_load((clone[0] / "var" / "setup" / "synthetic-sources.yaml").read_text(encoding="utf-8"))
    assert [e["document_id"] for e in syn["sources"]] == ["synthetic-doc"]
    assert report(clone[0])["mode"] == "synthetic-only"


def test_help_lists_every_step_and_flag(clone):
    r, calls = run(clone, "--help")
    assert r.returncode == 0 and calls == []
    for word in ("prerequisites", "reference PDFs", "SSH tunnel", "health check", "--quick", "--synthetic-only"):
        assert word in r.stdout
    r, _ = run(clone, "--bogus")
    assert r.returncode == 2


def test_tunnel_runs_only_with_all_three_values(clone):
    partial = "RUNPOD_SSH_HOST=x\nRUNPOD_SSH_PORT=set-me\nRUNPOD_SSH_KEY_PATH=k\n"
    r, calls = run(clone, env_text=partial)
    assert "[12/13] SSH tunnel ... skipped (no pod configured)" in r.stdout and not any("tunnel.sh" in c for c in calls)
    full = f"RUNPOD_SSH_HOST={SENTINEL}\nRUNPOD_SSH_PORT=40022\nRUNPOD_SSH_KEY_PATH=k\n"
    r, calls = run(clone, env_text=full, health=HEALTH_ALL, health_exit=0)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[12/13] SSH tunnel ... ok" in r.stdout and "[13/13] health check ... ok (all checks PASS)" in r.stdout
    assert report(clone[0])["tunnel"] is True


def test_env_values_are_never_printed(clone):
    env_text = f"MCP_AUTH_SECRET={SENTINEL}\nRUNPOD_SSH_HOST={SENTINEL}\nRUNPOD_SSH_PORT=1\nRUNPOD_SSH_KEY_PATH=k\n"
    r, _ = run(clone, env_text=env_text)
    assert SENTINEL not in r.stdout and SENTINEL not in r.stderr
    assert SENTINEL not in (clone[0] / "evals" / "reports").joinpath(
        sorted(p.name for p in (clone[0] / "evals" / "reports").glob("setup-*.json"))[-1]).read_text(encoding="utf-8")


def test_script_never_names_pod_creation():
    text = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "runpodctl" not in text and "api.runpod" not in text.lower()
    assert "set -e" not in text.replace("set -e`", "")
