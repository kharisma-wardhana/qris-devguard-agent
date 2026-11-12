# Plan: A local Streamlit demo UI drives chat, threads and approvals through /api/v1, and shows Langfuse alerts received by a loopback webhook
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-demo-ui-alerts/spec.md*

## Affected files

**New**
- `app/alerts/__init__.py`: package marker.
- `app/alerts/receiver.py`: stdlib-only webhook receiver (spec Behaviour §5). It is also the container's entry script.
- `app/alerts/store.py`: API-side reader. Re-validates each JSONL line (pydantic) and redacts (Behaviour §6).
- `app/ui/__init__.py`: package marker.
- `app/ui/client.py`: API client and token minting, with no Streamlit import (Architecture).
- `app/ui/render.py`: pure functions that turn API responses into plain-text sections in §5 order (Behaviour §3).
- `app/ui/streamlit_app.py`: the Streamlit pages (identity, threads, chat, approvals, alerts).
- `scripts/ui_server.py`: launcher. Loopback check, then Streamlit with usage stats off (Behaviour §7).
- `scripts/send_test_alert.py`: signs a synthetic `monitor-alert` and posts it, directly or from inside a compose container (AC #20).
- `tests/unit/test_alert_receiver.py`, `tests/unit/test_alert_store.py`, `tests/unit/test_ui_client.py`, `tests/unit/test_ui_render.py`, `tests/unit/test_ui_app.py` (AppTest with a fake client), `tests/unit/test_api_p9_routes.py`.
- `tests/integration/test_alert_receiver_cli.py`, `tests/integration/test_ui_server_cli.py`, `tests/integration/test_send_test_alert_cli.py`, `tests/integration/test_api_p9_server.py` (auth/authz against the real API subprocess), `tests/integration/test_ui_e2e.py` (AppTest against the real API and MCP subprocesses; thread and approval flows marked `db`).
- `docs/p9-demo-ui-alerts/runbook.md`: start order, demo script, controlled alert test, demo-only token note.
- `docs/p9-demo-ui-alerts/demo-report.md`: measured evidence for AC #20 and #21.

**Edited**
- `pyproject.toml`, `uv.lock`: new optional extra `ui` with a pinned `streamlit`.
- `configs/agent.yaml`: new `ui` and `alerts` sections (Architecture, config).
- `.gitignore`: `var/` (alert store).
- `.env.example`: `LANGFUSE_ALERT_WEBHOOK_SECRET=` (name only).
- `app/db/store.py`: `list_threads(owner, limit)`, `pending_approvals(limit)`.
- `app/agent/graph.py`: `Agent.history(thread_id)` from the checkpoint history.
- `app/api/app.py`: four GET routes: threads, thread messages, pending approvals, alerts.
- `docker-compose.yml`: `alert-receiver` service (profile `alerts`, no host ports, non-root, read-only script mount, `var/alerts` bind mount); `LANGFUSE_WEBHOOK_WHITELISTED_HOST: alert-receiver` in `x-langfuse-env`.
- `tests/unit/test_compose.py`: expected-service list and pins extended for `alert-receiver`. This is an intentional pin update; every existing assertion stays.
- `.github/workflows/ci.yml`: a new `ui-tests` job (`uv sync --locked --extra ui`, runs the UI tests). The existing `test` job is untouched; there, UI tests skip because Streamlit isn't installed.
- `.github/workflows/container-scan.yml`: one matrix entry for the receiver's pinned Python image.
- `docs/threat-model.md` (T20, plus boundary B7; the spec's "T18" was already taken by the GPU pod threat, so the new threat is numbered T20), `docs/tech.md`, `docs/structure.md`, `docs/index.md`.
- `CLAUDE.md`: append to `## Commands`, `## Test scope` and `## Common mistakes` only.

## Work sequence

1. **Dependency and config.** Files: `pyproject.toml`, `uv.lock`, `configs/agent.yaml`, `.gitignore`, `.env.example`. Pin the newest `streamlit` released at least two weeks ago. Spec AC: #19, #12 (config keys).
2. **Thread list and pending approvals.** Files: `app/db/store.py`, `app/api/app.py`. Spec AC: #1, #3, #5.
3. **Thread history.** Files: `app/agent/graph.py`, `app/api/app.py`. Spec AC: #2, #5.
4. **Alert receiver.** Files: `app/alerts/__init__.py`, `app/alerts/receiver.py`. Spec AC: #6–#12.
5. **Alert reader and route.** Files: `app/alerts/store.py`, `app/api/app.py`. Spec AC: #4, #5.
6. **Compose service and Langfuse whitelist.** Files: `docker-compose.yml`, `tests/unit/test_compose.py`, `.github/workflows/container-scan.yml`.
   - Pin the Python image to an exact patch tag that a local `podman pull` confirms exists.
   - Check that the non-root user can write the Windows bind mount. If it can't, stop and update this plan; don't switch to root silently.
   - Spec AC: #20 (wiring).
7. **UI client and rendering.** Files: `app/ui/__init__.py`, `app/ui/client.py`, `app/ui/render.py`. Spec AC: #14, #15, #17.
8. **Streamlit pages and launcher.** Files: `app/ui/streamlit_app.py`, `scripts/ui_server.py`. Spec AC: #13, #15, #16, #18, #19.
9. **CI job for UI tests.** File: `.github/workflows/ci.yml` (new job only). Spec AC: #13–#19 run in CI.
10. **Live local evidence.** Files: `scripts/send_test_alert.py`, `docs/p9-demo-ui-alerts/demo-report.md`. Spec AC: #20, #21.
    - The developer creates the webhook in the Langfuse UI and copies its secret into `.env`. The agent never reads it.
    - If Langfuse refuses `http://alert-receiver/...`, stop (spec Flagged concerns).
11. **Docs and agent configuration.** Files: `docs/p9-demo-ui-alerts/runbook.md`, `docs/threat-model.md`, `docs/tech.md`, `docs/structure.md`, `docs/index.md`, `CLAUDE.md`. Spec AC: #22.

Steps 2, 3 and 5 all edit `app/api/app.py`, and step 8 needs 2–5, so the only independent pair is 4 and 7. That's too small to be worth parallel worktrees. The steps run in order.

## Test coverage

Rows matched from `test-scope-by-stack`: web UI → end-to-end; service/script entry points → subprocess integration; roles/permissions → auth and authz integration; database → regression; existing project → regression. Every step also keeps the full existing suite passing unchanged (spec AC #5).

1. **Dependency and config:** unit test that the `ui` and `alerts` config keys load and `ui.host` is loopback.
2. **Thread list and pending approvals:**
   - Unit (`test_api_p9_routes.py`, fake store): route shapes, the `status` value check, ordering and limit.
   - **DB regression** (`-m db`, throwaway schema): `list_threads` returns only the owner's rows, newest first, capped. `pending_approvals` returns only `pending`.
   - **Auth/authz integration** against the real API subprocess (`test_api_p9_server.py`):
     - no token → 401 on both routes;
     - `developer` and `kb_maintainer` → 403 on pending approvals;
     - `support_engineer` and `admin` → 200;
     - another subject's threads never listed;
     - an `api_other` burst → 429 on a dedicated server, per the CLAUDE.md common mistake.
3. **Thread history:**
   - Unit: `Agent.history` on an in-memory checkpointer. Two runs give two entries in order. A planted secret in a message appears only redacted.
   - **DB regression** (`-m db`): the same on the PostgreSQL checkpointer.
   - **Authz integration:** another subject's thread and an unknown ID return 404 with the same body.
4. **Alert receiver:**
   - Unit:
     - signature (valid, missing, malformed, wrong, stale or future `t`);
     - 413 before reading the whole body;
     - 422 cases;
     - field allowlist and truncation;
     - permalink prefix rule;
     - dedupe by `id`;
     - the 500-record cap;
     - log lines carry no body, header or signature (caplog).
   - **Subprocess integration** (`test_alert_receiver_cli.py`): the real script on `127.0.0.1` with a random port. A signed alert is stored once. A bad signature gets 401 and nothing is stored. A missing secret exits non-zero and names the key, not its value.
5. **Alert reader and route:**
   - Unit: malformed and invalid lines are skipped and counted. A planted secret is redacted. A missing file gives an empty list. Newest first, at most 50.
   - **Authz integration** against the real API subprocess: `developer` and `kb_maintainer` → 403, approver roles → 200, no token → 401.
6. **Compose service and whitelist:**
   - **Regression** (`test_compose.py`): the service list and pins are extended. `alert-receiver` has no `ports`, a pinned image, a non-root user, a read-only script mount and its secret from `.env`. The whitelist names exactly `alert-receiver`. Every existing assertion still passes.
   - A local `podman compose --profile alerts up -d alert-receiver` smoke test, with output pasted.
7. **UI client and rendering:**
   - Unit with `httpx.MockTransport`:
     - minted token claims (subject, role, API audience, TTL);
     - re-mint before expiry;
     - the envelope is mapped to a UI error without submitted values;
     - 429 exposes `Retry-After`;
     - render output is plain-text sections in §5 order, and markdown links or HTML are passed through as literal text.
8. **Streamlit pages and launcher:**
   - UI unit (AppTest, fake client):
     - every page renders;
     - the token text appears in no element;
     - answer, source and alert text is rendered with text elements, not markdown;
     - the empty alerts state;
     - the error display.
   - **End-to-end (AppTest against the real API and MCP subprocesses, fake LLM)** (`test_ui_e2e.py`):
     - pick an identity, create a thread, send a message, see §5 sections;
     - `-m db`: the developer's message escalates, the approver approves and sees the ticket ID, a reject shows "nothing was created", the developer gets 403 on decide.

     AppTest is Streamlit's own test driver and stands in for Playwright. It doesn't run a browser, so the runbook adds one manual browser pass, recorded in the demo report.
   - **Subprocess integration** (`test_ui_server_cli.py`): the launcher serves `/_stcore/health` on `127.0.0.1`. Its command line carries `server.address=127.0.0.1` and usage stats off. A non-loopback `ui.host` is refused (unit with monkeypatch, like the API's existing test).
9. **CI job:** none beyond step 8's tests. They run in the new job. The agent-config eval job already exists (verified at Phase 4).
10. **Live local evidence:** subprocess integration for `send_test_alert.py` in direct mode against the receiver script. The live runs (AC #20, #21) are recorded in `demo-report.md` with run output pasted. They aren't in CI.
11. **Docs and agent configuration:** the repo hygiene tests (IPv4 and PEM checks) on the staged docs. Agent-config eval on the `CLAUDE.md` change.

## Agent configuration changes

This is a re-run (feature 10). It verifies and extends; nothing is re-derived.

- **`CLAUDE.md`** (its own diff and `approve write`, step 11): append only.
  - `## Commands`: `uv sync --extra ui`, `uv run python scripts/ui_server.py`, `podman compose --profile alerts up -d alert-receiver`, `uv run python scripts/send_test_alert.py ...`.
  - `## Test scope`: one row: "UI (Streamlit): AppTest end-to-end against the real API subprocess, plus one manual browser pass per release."
  - `## Common mistakes`: lessons this build actually hits, for example "a Langfuse container can't reach a host `127.0.0.1` listener; webhooks go to a compose service on port 80". No line is removed. The project-context pointer block stays the only one.
- **`.github/workflows/ci.yml`** (its own diff and `approve write`, step 9): a new `ui-tests` job. The existing `test` job isn't edited.
- **`.github/workflows/container-scan.yml`** (its own diff and `approve write`, step 6): one matrix entry. The job is otherwise unchanged.
- **Hooks:** no new hook. `deploy-gate.sh` and `eval.sh` are re-run and their output pasted at Phase 4.
- **Subagents:** none. No recurring task that the existing setup doesn't cover.

## Rollback

- Revert the feature's merge commit.
- Run `podman compose --profile alerts rm -sf alert-receiver`. Restart `langfuse-web` and `langfuse-worker` so the whitelist variable is gone.
- Delete the webhook automation in the Langfuse UI.
- Delete `var/alerts/`.

No database migration to undo.
