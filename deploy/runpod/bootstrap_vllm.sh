#!/usr/bin/env bash
# Pod-side bootstrap for vLLM. Runs ON the RunPod pod, as root, over SSH.
# See docs/runpod-deployment.md for the full session.
#
#   VLLM_API_KEY must be set (RunPod secret or `export`). It is read by vLLM
#   from the environment and never put on a command line.
#
#   BOOTSTRAP_DRY_RUN=1  print the checks and the serve command, then exit
#                        without installing or starting anything.
#
# Values below must match configs/model.yaml (tests/unit/test_bootstrap_vllm.py).
set -euo pipefail

MODEL_ID="Qwen/Qwen3-8B-FP8"
MODEL_REVISION="220b46e3b2180893580a4454f21f22d3ebb187d3"
SERVED_NAME="qwen3-8b"
VLLM_VERSION="0.32.0"
HOST="127.0.0.1"
PORT="8080"
MAX_MODEL_LEN="16384"
GPU_MEMORY_UTILIZATION="0.90"
MAX_NUM_SEQS="8"
REASONING_PARSER="qwen3"
TOOL_CALL_PARSER="hermes"
MIN_DRIVER_MAJOR="580"

VENV="${VLLM_VENV:-/root/vllm-venv}"
LOG="${VLLM_LOG:-/root/vllm.log}"
READY_TIMEOUT_S="${VLLM_READY_TIMEOUT_S:-900}"

die () { echo "bootstrap: ERROR: $*" >&2; exit 1; }
say () { echo "bootstrap: $*"; }

# 1. Preconditions: fail in seconds, before any download is paid for.
[ -n "${VLLM_API_KEY:-}" ] || die "VLLM_API_KEY is not set. Add it as a RunPod secret or export it, then rerun."
command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi not found. Is this a GPU pod?"

driver="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | tr -d '[:space:]')"
driver_major="${driver%%.*}"
case "$driver_major" in
  ''|*[!0-9]*) die "could not read the NVIDIA driver version (got '${driver}')." ;;
esac
say "NVIDIA driver ${driver}"
if [ "$driver_major" -lt "$MIN_DRIVER_MAJOR" ]; then
  die "driver ${driver} is older than ${MIN_DRIVER_MAJOR}, which vLLM ${VLLM_VERSION}'s default CUDA 13.0 build needs. Terminate this pod and pick another host, or follow the CUDA 12.9 fallback in docs/runpod-deployment.md."
fi

SERVE_ARGS=(
  serve "$MODEL_ID"
  --revision "$MODEL_REVISION"
  --served-model-name "$SERVED_NAME"
  --host "$HOST" --port "$PORT"
  --max-model-len "$MAX_MODEL_LEN"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --max-num-seqs "$MAX_NUM_SEQS"
  --reasoning-parser "$REASONING_PARSER"
  --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER"
)

if [ "${BOOTSTRAP_DRY_RUN:-0}" = "1" ]; then
  say "dry run: would install vllm==${VLLM_VERSION} into ${VENV}"
  say "dry run: would run: vllm ${SERVE_ARGS[*]}"
  exit 0
fi

# 2. Install pinned vLLM into its own venv (uv picks the matching torch build).
command -v uv >/dev/null 2>&1 || python3 -m pip install --quiet --upgrade uv
[ -x "${VENV}/bin/python" ] || uv venv --python 3.12 "$VENV"
say "installing vllm==${VLLM_VERSION} (a few minutes)"
VIRTUAL_ENV="$VENV" uv pip install --quiet "vllm==${VLLM_VERSION}" --torch-backend=auto

# 3. Serve. vLLM reads VLLM_API_KEY from the environment; weights download on first start.
say "starting vLLM on ${HOST}:${PORT}; log: ${LOG}"
nohup "${VENV}/bin/vllm" "${SERVE_ARGS[@]}" >"$LOG" 2>&1 &
pid=$!

# 4. Wait until healthy, or fail with the log tail.
waited=0
until curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; do
  kill -0 "$pid" 2>/dev/null || { tail -n 40 "$LOG" >&2; die "vLLM exited during startup."; }
  [ "$waited" -ge "$READY_TIMEOUT_S" ] && { tail -n 40 "$LOG" >&2; die "vLLM not healthy after ${READY_TIMEOUT_S}s."; }
  sleep 10; waited=$((waited + 10))
done
say "vLLM ready after ~${waited}s (pid ${pid})"
nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv
