#!/usr/bin/env bash
# Forward the pod's vLLM (127.0.0.1:8080 on the pod) to localhost:8080 over SSH.
# Reads RUNPOD_SSH_HOST, RUNPOD_SSH_PORT and RUNPOD_SSH_KEY_PATH from .env (never sourced).
# Leave it running in its own terminal; Ctrl+C closes the tunnel.
# The port must match configs/model.yaml (tests/unit/test_bootstrap_vllm.py).
set -euo pipefail

VLLM_PORT="8080"
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.env}"

die () { echo "tunnel: ERROR: $*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || die "no .env found. Run: uv run python scripts/init_env.py"

get () {  # get KEY -> value from .env without executing anything in it
  grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

host="$(get RUNPOD_SSH_HOST)"; port="$(get RUNPOD_SSH_PORT)"; key="$(get RUNPOD_SSH_KEY_PATH)"
key="${key/#\~/$HOME}"

for pair in "RUNPOD_SSH_HOST:$host" "RUNPOD_SSH_PORT:$port" "RUNPOD_SSH_KEY_PATH:$key"; do
  name="${pair%%:*}"; value="${pair#*:}"
  { [ -n "$value" ] && [ "$value" != "set-me" ]; } || die "$name is not set in .env (use the pod's 'SSH over exposed TCP' details)."
done
[ -f "$key" ] || die "SSH key not found at RUNPOD_SSH_KEY_PATH."

echo "tunnel: forwarding localhost:${VLLM_PORT} -> pod 127.0.0.1:${VLLM_PORT} (Ctrl+C to stop)"
exec ssh -N \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
  -p "$port" -i "$key" "root@${host}"
