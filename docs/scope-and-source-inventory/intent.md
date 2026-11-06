# Intent: Phase P0 scope and source inventory is signed off and the source list is confirmed safe to use publicly
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*

## Problem statement

The P0 deliverables have been drafted but not finished:
- the scope document (`docs/scope.md`)
- the threat model (`docs/threat-model.md`)
- the SOP skill outline
- the source manifest

Every entry in the source manifest is still a `candidate`. None has a confirmed version, effective date or permission, and the sign-off table in `docs/scope.md` §5 is empty.

The PRD's P0 exit criterion, "data owners/source permissions identified", is therefore not met. Without approved sources, Phase P2 ingestion cannot start.

Because the project will be shown publicly on LinkedIn, an unconfirmed source is more than a gap in the paperwork. If its licence doesn't allow public use, putting it in the repo or in a screenshot is a real exposure risk.

## Proposed outcome

- Every source the MVP needs is marked `approved` in `knowledge_base/manifests/sources.yaml`. Each approved entry has a real URL (or a team-authored marker), a version, an effective date and recorded permission evidence.
- Sources are a mix: public ASPI/SNAP documents where their terms allow public use, and synthetic team-written documents for everything else.
- The three MVP endpoints in `docs/scope.md` §1.1 are each confirmed against an approved source.
- The sign-off table in `docs/scope.md` §5 is complete and both P0 exit criteria are ticked.
- `scripts/check_sources.py` passes with the approved entries in place.

## Affected users and systems

- Kharisma Wardhana, the capstone author, who signs all four P0 roles.
- The internal agentic-AI team and the public LinkedIn audience, who will see whatever is committed and shown.
- `knowledge_base/manifests/sources.yaml` and `source.schema.json`
- `knowledge_base/sources/`, which stores the source documents
- `docs/scope.md`, `docs/threat-model.md` and `skills/qris-integration-troubleshooting/SKILL.md`
- Phase P2 ingestion and Phase P4 validators downstream, which will consume only approved sources.

## Constraints

- The capstone must be submitted by **2026-12-05**. P0 has to close early enough to leave time for phases P1–P9.
- Nothing confidential may appear in the repo, traces, report or screenshots: no internal documents, real credentials or customer or merchant data.
- A public document is approved only if its terms permit the public use this project makes of it. Otherwise it is replaced by a synthetic equivalent.
- Versions, dates and URLs are never guessed. Any field that can't be confirmed stays `null` and the entry stays `candidate`.
- One person signs all four P0 roles. The separation of duties between the KB maintainer and the approver exists in config only.
- No production connectivity, and no multimodal (P9) work.

## Open questions

- For each public ASPI/SNAP candidate, do the terms of use allow storing it in a public repo, or only referencing and excerpting it?
- Which documents will be synthetic, and how will each be marked so readers don't mistake it for an official specification?
- How is permission evidence recorded for a public document versus a team-authored one?
- Are the three MVP endpoints (`access-token-b2b`, `qr-mpm-generate`, `qr-mpm-query`) the right set, or should they change once the sources are confirmed?
- Is 2026-12-05 the capstone submission deadline, and is there a separate earlier target date for P0 sign-off?
