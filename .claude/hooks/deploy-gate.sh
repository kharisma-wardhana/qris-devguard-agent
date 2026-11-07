#!/usr/bin/env bash
# deploy-gate: asks a human before Bash commands that look like they reach a
# real environment. Defence in depth only -- the production gate is the
# instruction that the agent does not deploy; a regex cannot be complete.
# Fail closed: any internal error emits "ask". Deliberately no `set -e`.
# Parses JSON with Python because jq is not available on this project's machines.
set -uo pipefail

fallback () {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}' "$1"
  exit 0
}

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import json, re, sys" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -z "$PY" ] && fallback "Guard hook cannot run: no working Python found. Approve manually."

out=$("$PY" -c '
import json, re, sys

DANGER = (r"kubectl|helm +|terraform +(-[^ ]+ +)*(apply|destroy)|aws +|gcloud +|az +"
          r"|flyctl +deploy|vercel +--prod|netlify +deploy|firebase +deploy|serverless +deploy"
          r"|docker +push|npm +publish|uv +publish|gh +release +create|alembic +upgrade"
          r"|prisma +migrate +deploy|ssh +|scp +|rsync +|make +deploy|runpodctl|psql +"
          r"|(^|[;&|] *)(bash +|sh +)?\S*(scripts/tunnel|bootstrap_vllm)\.sh")

def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
          "permissionDecision": decision, "permissionDecisionReason": reason}}, separators=(",", ":")), end="")
    sys.exit(0)

try:
    data = json.loads(sys.stdin.read())
    cmd = (data.get("tool_input") or {}).get("command") or ""
except Exception:
    emit("ask", "Guard hook received input it could not parse.")
if cmd and re.search(DANGER, cmd):
    emit("ask", "This command can reach a real environment. A human approves production actions.")
' 2>/dev/null)
[ $? -ne 0 ] && fallback "Guard hook failed internally. Approve manually."
printf '%s' "$out"
exit 0
