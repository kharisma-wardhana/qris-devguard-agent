# REVIEW.md
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Amended: 2026-11-07 for p1-infrastructure.*

Claude reviews pull requests on this repo against this file. Read `CLAUDE.md` and the feature's `docs/<feature-name>/spec.md` first. The review checks the diff against the spec's acceptance criteria.

## Passes

### Bugs
- Every field added to `knowledge_base/manifests/source.schema.json` must also have a matching rule in `scripts/check_sources.py`, where one is needed. Each new rule must have one passing and one failing unit test in `tests/unit/test_check_sources.py`.
- **Dates in `sources.yaml` must be quoted or month-only.** PyYAML turns an unquoted full date (for example `2024-09-15`) into a `date` object, and the schema then rejects it. `2024-09` is safe because PyYAML reads it as a string.
- Tests that read repo files (`tests/unit/test_synthetic_docs.py`, `test_repo_hygiene.py`, `tests/integration/`) must locate files relative to the repo root, never relative to the working directory.
- A ticked box in `docs/<feature-name>/tasks.md` must have a matching commit that contains the step's work and its tests. A step added to `plan.md` must appear in `tasks.md` in the same commit.
- `docs/index.md` must link every `docs/<feature-name>/` folder.
- **P1:** `configs/model.yaml`, `deploy/runpod/bootstrap_vllm.sh` and `scripts/tunnel.sh` (port) change together. A value changed in only one fails the config-agreement test in `tests/unit/test_bootstrap_vllm.py`.
- **P1:** local URLs in `.env.example` use `127.0.0.1`, never `localhost` (on Windows, `localhost` tries IPv6 first and every connection stalls).
- **P1:** `docs/runpod-deployment.md` fetches the bootstrap script only by commit SHA, never by branch name.

### Security
- Nothing under `knowledge_base/sources/` may be tracked except `.gitkeep`. No `.env*` file other than `.env.example`, and no `*.pem`, `*.key` or `*.p12` file, may be added anywhere.
- `.env.example` must contain placeholders only (`replace-me` style). No real-looking keys, tokens or hosts.
- Synthetic documents and fixtures must contain no real merchant, partner, customer or credential values.
- `configs/security.yaml` must not get narrower without a matching `docs/threat-model.md` change. In particular, check that nothing was removed from `redact_fields`, `prohibited_actions` or `egress_allowlist`.
- Workflows in `.github/workflows/` keep `permissions: contents: read`, use no `pull_request_target`, reference no secrets, and run no deploy step.
- `.claude/hooks/*.sh` keep the fail-closed contract: no `set -e`, every error path emits `ask`, exit code is always 0. `.claude/settings.json` may only gain entries. A removed hook or `deny` rule is a blocker unless the PR states why.
- **P1:** in `docker-compose.yml`, every port stays on `127.0.0.1`, every image stays pinned, no secret appears on a `command:` or `healthcheck:` line, and no `mem_limit` is used (rootless Podman can't apply it).
- **P1:** vLLM binds to `127.0.0.1` with no `--api-key` on its command line, and the runbook's pod table exposes no HTTP port. No IP address, pod ID or `ssh.runpod.io` user string appears in a tracked file.
- **P1:** `scripts/healthcheck.py` never prints response bodies or secrets. Its sentinel tests stay.
- **P1:** changes to the tunnel, the bootstrap or the runbook keep T13 and T18 in `docs/threat-model.md` consistent.

### Compliance
- Public ASPI/SNAP documents are `reference_only`: they are never committed, and any quoted excerpt is at most `citations.max_excerpt_chars.reference_only` (300) characters.
- Every `synthetic-` document keeps `(SYNTHETIC — not an official ASPI/BI document)` on its first line and in its manifest title. It uses no ASPI or Bank Indonesia logos, layout, or wording that suggests endorsement.
- Manifest permission evidence records who checked, when, and what was found. Versions, dates and URLs are never guessed; unknown values stay `null`.
- Nothing is added that connects to production systems or performs payment, refund, reversal, settlement or merchant-activation actions (PRD §1.4).
- **P1:** Langfuse stays self-hosted with telemetry off, and traces carry only the fixed synthetic probe. Adding a Langfuse Cloud host to `egress_allowlist` needs a threat-model change.
- **P1:** values in the runbook's measured-values table are labelled as measured, and a deviation from the plan (such as the RTX A5000 instead of the RTX PRO 4000) is recorded as found, not presented as planned (CLAUDE.md: targets are never presented as results).

## Severity levels

| Level | Examples | Action |
|---|---|---|
| **Blocker** | A licensed source, secret or key committed; approval or redaction weakened; a hook or deny rule removed; CI red | Blocks merge |
| **Major** | A new checker rule without tests; a synthetic document missing its label; an unquoted full date; `docs/index.md` or `tasks.md` out of step | Must be fixed before merge, or the PR states why it is deferred |
| **Minor** | Unclear wording, a weak test name, a missing docstring on a non-obvious rule | Comment; author's choice |
| **Info** | Suggestions and alternatives | Comment only |

## Exclusions

- `uv.lock` contents (only check that it changed together with `pyproject.toml`)
- The wording of `docs/QRIS_DevGuard_AI_PRD_TRD.md` (it is the source requirements document, not reviewed here)
- `.gitkeep` files and anything under `evals/reports/`
- Git LF/CRLF line-ending warnings
- Markdown table alignment and formatting-only changes
- Debt already accepted: tests import `scripts/` via `sys.path` (`docs/structure.md` § Deviations)
- **P1:** the numbers in `docs/runpod-deployment.md` § Measured values. The user reports them from live runs; check only that each is labelled with its source.
- **P1:** environment-variable names in `docker-compose.yml` copied from Langfuse's official compose file. Check only pins, ports and secrets.

## What a human must decide

- Whether to keep accepting the reference-only risk for ASPI documents (no published licence), and whether to re-review if terms appear.
- Changing the reference-only excerpt limit, or adding or removing a source.
- Whether a synthetic value could still be mistaken for an official one, for example a path or service code that matches the real SNAP one.
- When to make `agent-config-eval` blocking (`continue-on-error: false`), and when to enable branch protection on `main`.
- **Organisation-level controls this repo cannot configure:** OS-level sandboxing for agent sessions, outbound domain allowlisting, and credential isolation (where RunPod, Langfuse and GitHub credentials live). These are managed settings, owned outside this repo.
