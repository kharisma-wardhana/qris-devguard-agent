#!/usr/bin/env bash
# Agent-configuration eval. Asserts each installed hook decides as
# documented, in both directions. Deliberately no `set -e`.
# Run from the repo root: bash .claude/hooks/eval.sh
set -uo pipefail
fail=0
ran=0

# acts <label> <script> <expected-substring> <input-json>
#   the hook must return a decision containing <expected-substring>
acts () {
  [ -f "$2" ] || { echo "skip - $1 (no $2)"; return; }
  ran=$((ran + 1))
  out=$(printf '%s' "$4" | bash "$2" 2>&1)
  if printf '%s' "$out" | grep -q "$3"; then
    echo "ok   - $1"
  else
    echo "FAIL - $1 (expected '$3', got '${out:-<empty>}')"; fail=1
  fi
}

# quiet <label> <script> <input-json>
#   the hook must say nothing at all -- the case it must NOT act on
quiet () {
  [ -f "$2" ] || { echo "skip - $1 (no $2)"; return; }
  ran=$((ran + 1))
  out=$(printf '%s' "$3" | bash "$2" 2>&1)
  if [ -z "$out" ]; then
    echo "ok   - $1"
  else
    echo "FAIL - $1 (expected no output, got '$out')"; fail=1
  fi
}

# One PAIR per installed hook: a case it must act on, and one it must not.
# Adding a hook and adding its pair are the same change.

acts  "deploy-gate asks on terraform"   .claude/hooks/deploy-gate.sh \
      '"permissionDecision":"ask"' \
      '{"tool_input":{"command":"terraform apply -auto-approve"}}'
acts  "deploy-gate asks on runpodctl"   .claude/hooks/deploy-gate.sh \
      '"permissionDecision":"ask"' \
      '{"tool_input":{"command":"runpodctl create pod"}}'
acts  "deploy-gate asks on bad input"   .claude/hooks/deploy-gate.sh \
      '"permissionDecision":"ask"' \
      'not json'
quiet "deploy-gate allows tests"        .claude/hooks/deploy-gate.sh \
      '{"tool_input":{"command":"uv run pytest"}}'
acts  "deploy-gate asks on tunnel.sh"   .claude/hooks/deploy-gate.sh \
      '"permissionDecision":"ask"' \
      '{"tool_input":{"command":"bash scripts/tunnel.sh"}}'
quiet "deploy-gate allows reading tunnel.sh" .claude/hooks/deploy-gate.sh \
      '{"tool_input":{"command":"cat scripts/tunnel.sh"}}'

if [ -f .claude/monitoring/control_band.py ]; then
  ran=$((ran + 1))
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import statistics" >/dev/null 2>&1; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then echo "FAIL - control band: no working Python found"; fail=1
  else "$PY" .claude/monitoring/control_band.py --self-check || fail=1; fi
fi

if [ "$ran" -eq 0 ]; then
  echo "FAIL - nothing to evaluate: no hooks and no monitoring script found."
  fail=1
fi

exit $fail
