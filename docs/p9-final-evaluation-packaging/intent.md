# Intent: One release-gate run decides PASS/FAIL on every P0 gate, and a first-time cloner can set up, run, evaluate and tear down the system from the README and one setup script
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-15. Via /aspec:make-it-simple-v2.*

## Problem statement

P0–P9 (demo UI) are merged, but the capstone cannot be called done, and a newcomer cannot rebuild it:

- **No single run says PASS or FAIL.** `configs/eval_thresholds.yaml` defines 11 gates and 4 critical gates. Their evidence is spread across eight phase reports (`docs/p2-…/retrieval-report.md` to `docs/p9-…/demo-report.md`) in different formats, run dates and dataset versions. No command reads the thresholds and gives one verdict. PRD §28 asks for "Evaluation gate produces PASS/FAIL with metrics and dataset version".
- **Some gates were never measured.** `golden_answer_supported` and `citation_support_precision` have no eval at all. TTFT/TPOT under a documented baseline was deferred from P1 to P9 (P1 recorded only health-check latencies). `reproducible_deployment` has no deployment manifest and no rebuild evidence.
- **Some gates have open evidence.** `langfuse_alert_fired`: P9 AC #21 is not met (no real Langfuse alert delivered yet). P8 AC #23 (live part) and AC #25 are open.
- **Packaging is missing or stale.** The README's Status section still says P6 is in review and P7 in progress. There is no rebuild-from-scratch section covering TRD §26.3 (GPU SKU, images, model revision, teardown). There is no final report (at most 5 pages, architecture diagram, measured numbers; `docs/product.md`) and no Langfuse dashboard or alert screenshots.
- **A first-time cloner cannot get from `git clone` to a running system from the README alone.** The steps are spread across `README.md`, `docs/runpod-deployment.md`, the `CLAUDE.md` command list and the phase runbooks. They are run by hand, in an order the reader must work out, and no single script does the setup.

## Proposed outcome

- **One release-gate command** reads `configs/eval_thresholds.yaml` and the newest evidence for each gate. It writes one report with PASS/FAIL per gate, the measured value, the dataset version and the run date. A gate without evidence reads NOT RUN, never PASS.
- **The missing measurements exist:**
  - a golden-answer eval (`golden_answer_supported`, `citation_support_precision`)
  - a vLLM TTFT/TPOT benchmark with its hardware recorded
  - a deployment manifest for `reproducible_deployment`
  - a real Langfuse alert that fired, which closes P9 AC #21
- **The README is the first-clone guide.** It covers:
  - prerequisites
  - one **bash** setup script that runs the local sequence in order: prerequisite check, env, `uv sync` with extras, compose, migrations, test keys, models, response-code extract, ingest, source check, health check. It also opens the SSH tunnel when a pod address is set in `.env`. It never creates a pod. It is idempotent and stops with a plain message at the first failure.
  - how to run, evaluate, benchmark and tear down
- **A final report** (at most 5 pages) with the architecture diagram, the measured numbers and the screenshots.

## Affected users and systems

- **Kharisma Wardhana:** runs the pod session, takes the screenshots, pastes the real webhook secret, and submits the capstone.
- **Capstone reviewers and first-time cloners:** read the README and the report, and run the setup script. They are the audience of the packaging.
- **`scripts/`:** a new setup script, the release-gate command, a golden-answer eval and a benchmark driver. Existing eval scripts are reused as evidence producers.
- **`evals/`:** a golden-answer dataset and new dated reports.
- **`configs/`:** benchmark and gate settings (no threshold changes).
- **`README.md`, `docs/`:** README rewrite, final report, screenshots, deployment manifest; `docs/tech.md`, `docs/structure.md` and `docs/index.md` updated.
- **RunPod pod + vLLM (`docs/runpod-deployment.md`):** used for one live session that the developer starts and stops.
- **Local Langfuse and `alert-receiver`:** the real alert firing for P9 AC #21.

## Constraints

- P9 is done by 2026-11-20; the capstone is submitted on 2026-12-05.
- Live runs (golden-answer eval, TTFT/TPOT benchmark, live agent flows, the P8 live echo rate) happen in one pod session the developer starts and stops; scripts and their CI-mode tests are ready before it.
- The agent never starts or stops the pod, never reaches RunPod, and never reads or uses a credential.
- Screenshots, pasting the real Langfuse webhook secret, and anything that reaches RunPod are the developer's actions.
- Public repo and report: synthetic data only; no secrets, IPs, pod IDs or private hostnames in the README, report, manifest or screenshots, and screenshots are checked before they are committed.
- Targets are not results: a gate without evidence reads NOT RUN, a missed gate is reported as FAIL with its number, and thresholds are never edited to reach PASS.
- The setup script is bash (Git Bash on Windows, Linux, macOS) and replaces no existing script.
- P0–P9 behaviour and the existing test suite must not break.
- The report is Markdown in `docs/`, at most 5 printed pages; exporting it to PDF is the developer's step.

## Open questions

- Does a golden-answer dataset exist (for example from `evals/datasets/agent_flows.yaml`), or is a new one written? How many questions, and who labels the expected answers?
- How are "answer supported" and "citation supports the claim" judged: by deterministic checks, by the developer's labels, or by a model judge? A model judge would be the LLM grading the LLM.
- Where is TTFT/TPOT measured: `vllm bench serve` on the pod, or a client-side driver through the tunnel? Which GPU, given the A5000 FP8-fallback note in `docs/runpod-deployment.md`?
- What counts as PASS for `reproducible_deployment`: a manifest plus a green setup-script run on a fresh clone, or also a fresh pod rebuild?
- Does the release-gate command re-run every eval, or read the newest existing reports, and how old may evidence be?
- Are P8 AC #23 (live) and AC #25, and P9 AC #21, closed inside this feature or recorded as open in the final report?
- Where do screenshots live, and how is the architecture diagram drawn (for example Mermaid in Markdown)?
- Does the setup script run the heavy steps (embedding stack, model fetch, ingest) by default, or behind a flag?
- Does CI run the release gate (in a CI mode without the pod), or is it a local command only?
