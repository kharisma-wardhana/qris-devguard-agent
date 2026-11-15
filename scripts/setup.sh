#!/usr/bin/env bash
# QRIS DevGuard AI - first-clone setup (docs/p9-final-evaluation-packaging/spec.md Behaviour §1).
#
#   bash scripts/setup.sh                    # everything, in order; safe to re-run
#   bash scripts/setup.sh --quick            # skip the embedding extra, the model fetch and ingest (tests + API only)
#   bash scripts/setup.sh --synthetic-only   # no reference PDFs: synthetic documents only (does NOT reproduce
#                                            # the measured results, which cite the SNAP documents)
#   bash scripts/setup.sh --help
#
# It only chains the repo's own scripts. It stops at the first failed step with the reason and the next action.
# It never creates, starts or stops a RunPod pod, never downloads the reference PDFs, and never prints a value from
# .env. The SSH tunnel is opened only when RUNPOD_SSH_HOST, RUNPOD_SSH_PORT and RUNPOD_SSH_KEY_PATH are set in .env.
# Writes evals/reports/setup-<timestamp>.json (git-ignored) for scripts/deploy_manifest.py --setup-report.
#
# Exit codes: 0 done, 1 a step failed, 2 usage error.
# Deliberately no `set -e`: every step is checked explicitly so each failure gets its own message.
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
# Paths handed to Python: Windows Python misreads Git Bash paths such as /c/Users/..., so pass the native form.
ROOT_PY="$(cygpath -m "$ROOT" 2>/dev/null || printf '%s' "$ROOT")"

QUICK=0
SYNTH=0
TOTAL=13
STEP_NAMES=("prerequisites" ".env" "uv sync" "compose services" "database migrations" "test keys" "reference PDFs"
            "response-code extract" "embedding and reranker models" "ingest" "source manifest check" "SSH tunnel"
            "health check")

usage() {
  cat <<'EOF'
Usage: bash scripts/setup.sh [--quick] [--synthetic-only] [--help]

Steps (each one is skipped when its result is already in place):
   1 prerequisites            git, uv, podman + podman compose, Python >= 3.11 (through uv)
   2 .env                     scripts/init_env.py (new) or --add-missing (existing); values are never printed
   3 uv sync                  extras embed + ui (--quick: ui only)
   4 compose services         podman compose up -d, then wait for PostgreSQL and Qdrant
   5 database migrations      scripts/db_migrate.py
   6 test keys                scripts/gen_test_keys.py (existing keys are kept)
   7 reference PDFs           present at their manifest local_path with the manifest checksum; never downloaded
   8 response-code extract    scripts/extract_response_codes.py, skipped when the derived file is newer than the PDF
   9 models                   scripts/fetch_models.py (BGE-M3 embedder and reranker into the HF cache)
  10 ingest                   scripts/ingest.py (unchanged sources are skipped by checksum and config)
  11 source manifest check    scripts/check_sources.py
  12 SSH tunnel               scripts/tunnel.sh in the background, only when RUNPOD_SSH_* are set in .env
  13 health check             scripts/healthcheck.py (vLLM reads FAIL until a pod and the tunnel are up)

Flags:
  --quick            skip steps 9 and 10 and the embed extra (enough for the test suite and the API)
  --synthetic-only   skip steps 7 and 8; ingest only team_authored sources (does not reproduce measured results)
  --help             this text
EOF
}

for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --synthetic-only) SYNTH=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

MODE="full"
[ "$QUICK" = 1 ] && MODE="quick"
[ "$SYNTH" = 1 ] && MODE="synthetic-only"
[ "$QUICK" = 1 ] && [ "$SYNTH" = 1 ] && MODE="quick+synthetic-only"

STARTED="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
STAMP="$(date +%Y-%m-%dT%H%M%S)"
REPORT_DIR="$ROOT/evals/reports"
FRESH_CLONE=false
[ -f "$ROOT/.env" ] || FRESH_CLONE=true
TUNNEL=false
STEPS_JSON=""
HC_ALL=null
HC_PASSED=null
HC_TOTAL=null

json_escape() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "${s//$'\n'/ }"; }

record() {  # record N STATUS DETAIL
  local entry
  entry="{\"n\": $1, \"name\": \"$(json_escape "${STEP_NAMES[$(( $1 - 1 ))]}")\", \"status\": \"$2\", \"detail\": \"$(json_escape "$3")\"}"
  if [ -z "$STEPS_JSON" ]; then STEPS_JSON="$entry"; else STEPS_JSON="$STEPS_JSON, $entry"; fi
}

write_report() {  # write_report RESULT
  local commit
  commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  mkdir -p "$REPORT_DIR" || return 0
  cat > "$REPORT_DIR/setup-$STAMP.json" <<EOF
{"result": "$1", "mode": "$MODE", "fresh_clone": $FRESH_CLONE, "commit": "$(json_escape "$commit")",
 "started_at": "$STARTED", "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)", "tunnel": $TUNNEL,
 "healthcheck": {"all_pass": $HC_ALL, "checks_passed": $HC_PASSED, "checks_total": $HC_TOTAL},
 "steps": [$STEPS_JSON]}
EOF
}

say()  { printf '[%d/%d] %s ... %s\n' "$1" "$TOTAL" "${STEP_NAMES[$(( $1 - 1 ))]}" "$2"; }
ok()   { say "$1" "ok${2:+ ($2)}"; record "$1" ok "${2:-}"; }
skip() { say "$1" "skipped ($2)"; record "$1" skipped "$2"; }
fail() {  # fail N REASON NEXT_ACTION
  say "$1" "FAILED"
  echo "  reason: $2" >&2
  echo "  next:   $3" >&2
  record "$1" failed "$2"
  write_report failed
  exit 1
}

PY=(uv run python)

# 1 prerequisites ---------------------------------------------------------------------------------------------
missing=""
for tool in git uv podman; do command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"; done
[ -z "$missing" ] || fail 1 "not installed:$missing" "install them (README: Prerequisites), then re-run"
podman compose version >/dev/null 2>&1 || fail 1 "podman compose is not available" \
  "install a compose provider for podman (README: Prerequisites), then re-run"
"${PY[@]}" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1 \
  || fail 1 "uv could not provide Python >= 3.11" "run 'uv python install 3.12', then re-run"
ok 1

# 2 .env --------------------------------------------------------------------------------------------------------
if [ -f .env ]; then
  "${PY[@]}" scripts/init_env.py --add-missing >/dev/null 2>&1 \
    || fail 2 "init_env.py --add-missing failed" "run 'uv run python scripts/init_env.py --add-missing' to see why"
  ok 2 "existing .env kept; missing keys added"
else
  "${PY[@]}" scripts/init_env.py >/dev/null 2>&1 \
    || fail 2 "init_env.py failed" "run 'uv run python scripts/init_env.py' to see why"
  ok 2 "created with random secrets"
fi

# 3 uv sync -----------------------------------------------------------------------------------------------------
if [ "$QUICK" = 1 ]; then
  uv sync --extra ui || fail 3 "uv sync failed" "check the network and 'uv sync --extra ui' output"
  ok 3 "extras: ui"
else
  uv sync --extra embed --extra ui || fail 3 "uv sync failed" "check the network and 'uv sync --extra embed --extra ui' output"
  ok 3 "extras: embed, ui"
fi

# 4 compose services --------------------------------------------------------------------------------------------
podman compose up -d >/dev/null 2>&1 || fail 4 "podman compose up -d failed" \
  "run 'podman compose up -d' to see the error (is the podman machine running? is .env complete?)"
"${PY[@]}" -m app.evaluation.setup_checks wait --timeout "${SETUP_WAIT_SECONDS:-120}" \
  || fail 4 "PostgreSQL or Qdrant did not start in time" "run 'podman compose ps' and 'podman compose logs postgres qdrant'"
ok 4 "PostgreSQL and Qdrant accept connections"

# 5 database migrations -----------------------------------------------------------------------------------------
"${PY[@]}" scripts/db_migrate.py >/dev/null 2>&1 || fail 5 "db_migrate.py failed" \
  "run 'uv run python scripts/db_migrate.py' to see why"
ok 5

# 6 test keys ---------------------------------------------------------------------------------------------------
"${PY[@]}" scripts/gen_test_keys.py >/dev/null 2>&1 || fail 6 "gen_test_keys.py failed" \
  "run 'uv run python scripts/gen_test_keys.py' to see why"
ok 6 "existing keys kept"

# 7 reference PDFs ----------------------------------------------------------------------------------------------
if [ "$SYNTH" = 1 ]; then
  skip 7 "--synthetic-only"
else
  if ! pdf_out="$("${PY[@]}" -m app.evaluation.setup_checks pdfs --root "$ROOT_PY" 2>&1)"; then
    [ -n "$pdf_out" ] && printf '  %s\n' "$pdf_out" >&2
    fail 7 "reference PDF missing or a different version" \
      "place the file(s) as shown above, then re-run (or use --synthetic-only, which does not reproduce the results)"
  fi
  ok 7 "present, checksums match"
fi

# 8 response-code extract ---------------------------------------------------------------------------------------
DERIVED="knowledge_base/sources/derived/snap-response-codes-1.0.2.json"
PDF="knowledge_base/sources/snap-standar-data-spesifikasi-teknis-1.0.2.pdf"
if [ "$SYNTH" = 1 ]; then
  skip 8 "--synthetic-only"
elif [ -f "$DERIVED" ] && [ "$DERIVED" -nt "$PDF" ]; then
  skip 8 "derived table is current"
else
  "${PY[@]}" scripts/extract_response_codes.py >/dev/null 2>&1 || fail 8 "extract_response_codes.py failed" \
    "run 'uv run python scripts/extract_response_codes.py' to see why"
  ok 8
fi

# 9 models ------------------------------------------------------------------------------------------------------
if [ "$QUICK" = 1 ]; then
  skip 9 "--quick"
else
  "${PY[@]}" scripts/fetch_models.py || fail 9 "fetch_models.py failed" \
    "check the network; run 'uv run python scripts/fetch_models.py --dry-run' to see what it fetches"
  ok 9 "pinned revisions in the HF cache"
fi

# 10 ingest -----------------------------------------------------------------------------------------------------
if [ "$QUICK" = 1 ]; then
  skip 10 "--quick"
elif [ "$SYNTH" = 1 ]; then
  SYN_MANIFEST="$ROOT_PY/var/setup/synthetic-sources.yaml"
  "${PY[@]}" -m app.evaluation.setup_checks synthetic-manifest --root "$ROOT_PY" --out "$SYN_MANIFEST" >/dev/null 2>&1 \
    || fail 10 "could not write the synthetic-only manifest" "re-run with a writable var/ directory"
  "${PY[@]}" scripts/ingest.py --manifest "$SYN_MANIFEST" || fail 10 "ingest.py failed" \
    "run 'uv run python scripts/ingest.py' and read its per-document reasons"
  ok 10 "team_authored sources only"
else
  "${PY[@]}" scripts/ingest.py || fail 10 "ingest.py failed" \
    "run 'uv run python scripts/ingest.py' and read its per-document reasons"
  ok 10
fi

# 11 source manifest check --------------------------------------------------------------------------------------
"${PY[@]}" scripts/check_sources.py >/dev/null 2>&1 || fail 11 "check_sources.py failed" \
  "run 'uv run python scripts/check_sources.py' to see which entry"
ok 11

# 12 SSH tunnel -------------------------------------------------------------------------------------------------
env_has() {  # env_has KEY: set, non-empty and not the init_env placeholder; the value is never printed
  local line value
  line="$(grep -E "^$1=" .env 2>/dev/null | tail -n 1)"
  value="${line#*=}"
  [ -n "$line" ] && [ -n "$value" ] && [ "$value" != "set-me" ]
}
if env_has RUNPOD_SSH_HOST && env_has RUNPOD_SSH_PORT && env_has RUNPOD_SSH_KEY_PATH; then
  mkdir -p var
  nohup bash scripts/tunnel.sh > var/tunnel.log 2>&1 &
  echo "$!" > var/tunnel.pid
  "${PY[@]}" -m app.evaluation.setup_checks wait --port 8080 --timeout "${SETUP_TUNNEL_SECONDS:-30}" \
    || fail 12 "the tunnel did not open localhost:8080" "read var/tunnel.log; check the pod is running and its SSH details"
  TUNNEL=true
  ok 12 "running in the background (pid in var/tunnel.pid)"
else
  skip 12 "no pod configured"
fi

# 13 health check -----------------------------------------------------------------------------------------------
"${PY[@]}" scripts/healthcheck.py
hc_exit=$?
HC_LINE="$(tail -n 1 "$REPORT_DIR/healthcheck.jsonl" 2>/dev/null)"
count() { printf '%s' "$HC_LINE" | grep -o "\"status\": \"$1\"" | wc -l | tr -d ' '; }
if [ -n "$HC_LINE" ]; then
  HC_PASSED="$(count PASS)"
  HC_TOTAL=$(( HC_PASSED + $(count FAIL) ))
  if printf '%s' "$HC_LINE" | grep -q '"all_pass": true'; then HC_ALL=true; else HC_ALL=false; fi
fi
non_vllm_fail="$(printf '%s' "$HC_LINE" | grep -o '"check": "[a-z_]*", "status": "FAIL"' | grep -v '"vllm_' | wc -l | tr -d ' ')"
if [ "$hc_exit" = 0 ]; then
  ok 13 "all checks PASS"
elif [ "$TUNNEL" = false ] && [ -n "$HC_LINE" ] && [ "$non_vllm_fail" = 0 ]; then
  ok 13 "vLLM checks FAIL as expected: no pod configured (create one: README, RunPod), then re-run"
else
  fail 13 "health check failed" "read the FAIL lines above (README: Expected health-check output)"
fi

write_report done
echo "setup done ($MODE). Report: evals/reports/setup-$STAMP.json"
exit 0
