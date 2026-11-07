"""Pod bootstrap script: safe failure, loopback bind, agreement with configs/model.yaml (spec AC #9, #10)."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "runpod" / "bootstrap_vllm.sh"
CONFIG = yaml.safe_load((ROOT / "configs" / "model.yaml").read_text(encoding="utf-8"))
SENTINEL_KEY = "sentinel-vllm-key-7f3a9c"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def script_var(name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"', SCRIPT.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} not set in bootstrap script"
    return match.group(1)


def make_stub_nvidia_smi(tmp_path: Path, driver: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "nvidia-smi"
    stub.write_text(f"#!/usr/bin/env bash\necho '{driver}'\n", encoding="utf-8", newline="\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bindir


def run(tmp_path, driver="580.95.05", key=SENTINEL_KEY, dry_run=True):
    env = {k: v for k, v in os.environ.items() if k != "VLLM_API_KEY"}
    env["PATH"] = str(make_stub_nvidia_smi(tmp_path, driver)) + os.pathsep + env["PATH"]
    if key is not None:
        env["VLLM_API_KEY"] = key
    if dry_run:
        env["BOOTSTRAP_DRY_RUN"] = "1"
    return subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8")


def test_script_parses():
    assert subprocess.run([BASH, "-n", str(SCRIPT)]).returncode == 0


@pytest.mark.parametrize(
    "var, expected",
    [
        ("MODEL_ID", CONFIG["model"]["id"]),
        ("MODEL_REVISION", CONFIG["model"]["revision"]),
        ("SERVED_NAME", CONFIG["model"]["served_name"]),
        ("VLLM_VERSION", CONFIG["vllm"]["version"]),
        ("HOST", CONFIG["vllm"]["host"]),
        ("PORT", str(CONFIG["vllm"]["port"])),
        ("MAX_MODEL_LEN", str(CONFIG["vllm"]["max_model_len"])),
        ("GPU_MEMORY_UTILIZATION", f'{CONFIG["vllm"]["gpu_memory_utilization"]:.2f}'),
        ("MAX_NUM_SEQS", str(CONFIG["vllm"]["max_num_seqs"])),
        ("REASONING_PARSER", CONFIG["vllm"]["reasoning_parser"]),
        ("TOOL_CALL_PARSER", CONFIG["vllm"]["tool_call_parser"]),
        ("MIN_DRIVER_MAJOR", str(CONFIG["vllm"]["min_driver_major"])),
    ],
)
def test_script_matches_model_config(var, expected):
    assert script_var(var) == expected


def test_binds_loopback_only():
    assert CONFIG["vllm"]["host"] == "127.0.0.1"
    assert "0.0.0.0" not in SCRIPT.read_text(encoding="utf-8")


def test_missing_api_key_fails_fast(tmp_path):
    result = run(tmp_path, key=None)
    assert result.returncode != 0
    assert "VLLM_API_KEY is not set" in result.stderr


def test_old_driver_fails_fast(tmp_path):
    result = run(tmp_path, driver="575.57.08")
    assert result.returncode != 0
    assert "older than 580" in result.stderr


def test_unreadable_driver_fails(tmp_path):
    result = run(tmp_path, driver="N/A")
    assert result.returncode != 0
    assert "could not read the NVIDIA driver version" in result.stderr


def test_dry_run_prints_pinned_command_without_key(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"vllm=={CONFIG['vllm']['version']}" in out
    assert f"--revision {CONFIG['model']['revision']}" in out
    assert "--host 127.0.0.1" in out
    assert "--enable-auto-tool-choice" in out
    assert SENTINEL_KEY not in out + result.stderr
    assert "--api-key" not in out


# --- scripts/tunnel.sh (plan step 5) ---------------------------------------

TUNNEL = ROOT / "scripts" / "tunnel.sh"


def test_tunnel_script_parses_and_targets_vllm_loopback():
    assert subprocess.run([BASH, "-n", str(TUNNEL)]).returncode == 0
    text = TUNNEL.read_text(encoding="utf-8")
    port = CONFIG["vllm"]["port"]
    assert f'VLLM_PORT="{port}"' in text
    assert '-L "127.0.0.1:${VLLM_PORT}:127.0.0.1:${VLLM_PORT}"' in text


def test_tunnel_refuses_placeholder_host(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("RUNPOD_SSH_HOST=set-me\nRUNPOD_SSH_PORT=set-me\nRUNPOD_SSH_KEY_PATH=~/.ssh/x\n", encoding="utf-8")
    result = subprocess.run([BASH, str(TUNNEL)], env={**os.environ, "ENV_FILE": str(env_file)},
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "RUNPOD_SSH_HOST is not set" in result.stderr


def test_no_secret_values_in_script():
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"(sk-|hf_)[A-Za-z0-9]{10,}", text)
    assert "VLLM_API_KEY=" not in text.replace('"${VLLM_API_KEY:-}"', "")


# --- step 11: fixes from the first live run ---------------------------------

def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def bash_path(path: Path) -> str:
    """Path as bash sees it; on Windows (Git Bash) 'C:/x' must be '/c/x' or it breaks PATH at the colon."""
    posix = path.as_posix()
    if os.name == "nt" and len(posix) > 1 and posix[1] == ":":
        return "/" + posix[0].lower() + posix[2:]
    return posix


def run_full(tmp_path, vllm_body: str):
    """Run the real (non-dry) script with stubbed nvidia-smi, uv, curl, and a stub venv."""
    bindir = make_stub_nvidia_smi(tmp_path, "595.91.07")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _stub(venv / "bin" / "python", "exit 0")
    _stub(venv / "bin" / "ninja", "exit 0")
    _stub(venv / "bin" / "vllm", vllm_body)
    _stub(bindir / "uv", "exit 0")
    _stub(bindir / "curl", f'[ -f "{venv.as_posix()}/healthy" ]')
    # ninja must be reachable ONLY through the venv, as on the pod.
    path = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if not (Path(p) / "ninja").exists()
                           and not (Path(p) / "ninja.exe").exists())
    env = {**os.environ, "PATH": str(bindir) + os.pathsep + path, "VLLM_API_KEY": SENTINEL_KEY,
           "VLLM_VENV": bash_path(venv), "VLLM_LOG": (tmp_path / "vllm.log").as_posix(),
           "VLLM_POLL_S": "1", "VLLM_READY_TIMEOUT_S": "20"}
    env.pop("BOOTSTRAP_DRY_RUN", None)
    return subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=60), venv


def test_vllm_finds_ninja_from_venv(tmp_path):
    """Regression: the live start died with FileNotFoundError 'ninja' (FlashInfer JIT)."""
    marker = (tmp_path / "venv" / "healthy").as_posix()
    body = ('command -v ninja >/dev/null || { echo "ERROR FileNotFoundError: ninja"; exit 1; }\n'
            f'touch "{marker}"; sleep 3')
    result, _ = run_full(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "vLLM ready" in result.stdout


def test_failed_start_prints_first_error_block(tmp_path):
    body = ('for i in 1 2 3; do echo "INFO loading $i"; done\n'
            'echo "ERROR root-cause-marker: something broke"\n'
            'for i in $(seq 1 60); do echo "noise line $i"; done\n'
            'exit 1')
    result, _ = run_full(tmp_path, body)
    assert result.returncode != 0
    assert "root-cause-marker" in result.stderr
    assert "vLLM exited during startup" in result.stderr
    assert SENTINEL_KEY not in result.stdout + result.stderr
