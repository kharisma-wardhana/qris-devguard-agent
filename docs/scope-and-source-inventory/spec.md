# Spec: Phase P0 scope and source inventory is signed off and the source list is confirmed safe to use publicly
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-06. Via /aspec:make-it-simple-v2.*
*Implements: docs/scope-and-source-inventory/intent.md*

## Behaviour

When this feature is done, a reader of the repo can see the following:

1. **Which sources the MVP may use**, and on what terms. The manifest holds exactly two kinds of approved source:
   - **Reference-only public documents** from the ASPI SNAP developer portal. They are used locally for retrieval, never committed, and quoted only in short cited excerpts.
   - **Synthetic, team-authored documents.** They are committed and clearly labelled as not official, so nobody mistakes them for an ASPI or Bank Indonesia publication.
2. **That every MVP endpoint has a source.**
   - `access-token-b2b` traces to the public SNAP Technical & Security standard.
   - `qr-mpm-generate` and `qr-mpm-query` trace to a synthetic QRIS MPM service spec. The ASPI portal has no QRIS MPM page, and the related CPM page could not be read (checked 2026-11-06).
3. **That the manifest checker enforces the labelling and storage rules.**
   - It rejects a synthetic document that isn't labelled as synthetic.
   - It rejects a team-authored document stored where git ignores it.
   - It rejects a reference-only document stored where git would commit it.
   - It rejects an approved entry missing its evidence.
4. **That P0 is closed.** Both exit criteria in `docs/scope.md` are ticked, and the sign-off table carries a name and date for each of the four roles. Kharisma Wardhana signs all four.
5. **That the two new risks are in the threat model:** redistributing documents that have no stated licence, and a synthetic document being mistaken for an official one.

## Architecture and the reason for it

This feature conforms to `docs/tech.md` and `docs/structure.md`:
- Python 3.11+, uv, pytest, jsonschema and PyYAML.
- Policy lives in `configs/`, and the manifest is the ingestion gate.
- No new dependencies.

**Manifest schema changes:**
- **New permission basis, `reference_only`.** None of the existing bases (`public_terms`, `written_permission`) is true for the ASPI documents, because the portal publishes no terms. Recording an accurate basis is the point of the manifest.
- **New optional field, `local_path`.** It records where the document file lives. That lets the checker verify storage rules:
  - A team-authored file must exist and be committed.
  - A reference-only file must sit in the git-ignored sources directory.
- **`effective_date` also accepts `YYYY-MM`.** The portal states "September 2024" with no day, and a guessed day would break the no-guessing constraint.

**New checker rules** (in the existing `scripts/check_sources.py`, which keeps one place for manifest policy):

| Rule | Why |
|---|---|
| A `document_id` starting `synthetic-` requires basis `team_authored`, and the reverse | Synthetic status can't be claimed for one but not the other |
| A `synthetic-` entry's title contains `(SYNTHETIC — not an official ASPI/BI document)` | The label travels with every citation |
| An approved `team_authored` entry needs a `local_path` that exists in the repo and is outside the ignored sources directory | Synthetic documents are reproducible from a clean clone |
| A `reference_only` entry's `local_path`, if set, is inside the ignored sources directory | Unlicensed public documents are never committed |
| An approved `reference_only` entry needs a non-placeholder `source_url` and permission evidence | Provenance can be checked |

**Where synthetic documents live:** a new committed knowledge-base directory, kept separate from the git-ignored sources directory. Mixing the two would make the git-ignore rules ambiguous.

**Reference-only excerpt limit:** stored as policy in `configs/security.yaml` and enforced by the citation layer from Phase P2. P0 only sets the number.

**What the synthetic documents contain:**
- **The MPM spec:**
  - It follows public SNAP conventions for transactional calls: `X-TIMESTAMP`, `X-SIGNATURE`, `X-PARTNER-ID`, `X-EXTERNAL-ID` and `CHANNEL-ID` headers, and an HMAC_SHA512 signature over a colon-joined string-to-sign that includes a SHA-256 of the minified body.
  - It defines request and response fields and response codes for Generate QR and Query Payment.
  - It states in its header that all values are synthetic.
- **The synthetic SOP** expands the procedure already in `skills/qris-integration-troubleshooting/SKILL.md` into a document that can be retrieved and cited.

## Data

- **Manifest** (committed): four approved entries and one candidate. It holds metadata only: IDs, titles, URLs, versions, dates, permission basis and evidence, and paths. Nothing sensitive.
- **Reference-only PDFs:** local only and git-ignored. They are never committed or shown in full. Retention: until superseded.
- **Synthetic documents** (committed): team-authored, all values fictional, no real merchant or partner identifiers. They are safe for public display.
- **Permission evidence strings:** each records who checked, when, which pages, and what was found. For example: "No published terms found on homepage, Info, Keamanan, API Services pages; checked 2026-11-06."

## Acceptance criteria

1. `uv run python scripts/check_sources.py` exits 0 on the committed manifest.
2. The committed manifest has exactly these `approved` entries:
   - `snap-technical-security-standard` (`reference_only`, version `1.0.2`)
   - `snap-data-technical-spec` (`reference_only`, version `1.0.2`, which includes the response codes)
   - `synthetic-qris-mpm-service-spec` (`team_authored`)
   - `synthetic-qris-troubleshooting-sop` (`team_authored`)

   `synthetic-test-fixtures` is present as `candidate`. No entries remain with the IDs `qris-national-standard`, `internal-qris-troubleshooting-sop`, `snap-qris-mpm-service-spec` or `snap-response-codes`.
3. The checker reports an error, each covered by its own unit test, for:
   - (a) a `synthetic-` ID whose basis isn't `team_authored`
   - (b) `team_authored` without the `synthetic-` prefix
   - (c) a `synthetic-` entry missing the title marker
   - (d) an approved `team_authored` entry whose `local_path` is missing or doesn't exist
   - (e) a `team_authored` `local_path` inside the git-ignored sources directory
   - (f) a `reference_only` `local_path` outside the git-ignored sources directory
   - (g) an approved `reference_only` entry without `source_url` or evidence
4. The schema accepts `effective_date` values `2024-09` and `2024-09-15`, and rejects `2024-9`, `09-2024` and `2024-09-15T00:00`. Unit tests cover each value.
5. Each synthetic document file exists at its manifest `local_path`, and its first line contains `(SYNTHETIC — not an official ASPI/BI document)`. A unit test checks this for every `synthetic-` entry.
6. The synthetic MPM spec has a section for each of Generate QR and Query Payment. Each section covers method and path, required headers, request fields, response fields, the signature profile with its string-to-sign definition, and the response codes used. A unit test checks that the required headings exist.
7. `docs/scope.md` meets all of the following:
   - §1.1 marks all three endpoints confirmed, each naming its manifest `document_id`.
   - Both exit criteria are ticked.
   - Every §5 row has a name and a date on or before 2026-11-10.
8. `docs/threat-model.md` contains entries for both of these, each with a control and a verifying test:
   - redistribution of documents with no stated licence
   - a synthetic document mistaken for an official one
9. `git ls-files knowledge_base/sources` lists only `.gitkeep`. A unit test checks this.
10. `configs/security.yaml` defines a maximum excerpt length for citations from `reference_only` sources.
11. All tests that existed before this feature still pass under `uv run pytest`.

## Flagged concerns

- **Legal / IP: no published terms on the ASPI portal.** The reference-only policy is a risk you are choosing to accept. It is not a licence, and the usage-guide PDF (*Pedoman Pengelolaan Developer Site*, Jan 2026) was never checked because it timed out. **Decides:** Kharisma Wardhana, by signing the KB maintainer row. Recommendation: open that PDF manually before signing.
- **Unverified version:** v1.0.2 comes from the portal's homepage and Info page, not from the PDF's own cover page. **Decides:** Kharisma Wardhana, by opening the PDF and confirming the version before marking it approved.
- **No separation of duties:** one person signs all four roles, so the KB-maintainer/approver separation in `configs/security.yaml` exists in config only. This is acceptable for a capstone. **Decides:** Kharisma Wardhana. It is recorded as a residual risk in the threat model.
- **Brand:** synthetic documents must not use ASPI or Bank Indonesia logos or layouts, or wording that suggests endorsement. **Decides:** Kharisma Wardhana, at review.
- **Public display (LinkedIn):** screenshots of answers may show only the short excerpt from a reference-only source, never a full page. **Decides:** Kharisma Wardhana, when capturing the report screenshots in P9.
- **Privacy and accessibility:** none found. These checks covered all data being synthetic or public metadata and no user-facing UI in this feature.

## Out of scope

- Ingesting, chunking or embedding any document. That is Phase P2.
- Payload or signature fixtures and validators. That is Phase P4, which is why `synthetic-test-fixtures` stays `candidate`.
- Enforcing the excerpt limit in code. That is Phase P2/P5.
- Automatically downloading or caching ASPI PDFs.
- Any other P1–P9 work, CI setup, and multimodal support.

## Resolved questions

- **Do the ASPI/SNAP terms allow public storage?** No terms are published on any page that loaded (checked 2026-11-06), and the usage-guide PDF wasn't readable. Decision: **reference only**, meaning local and git-ignored, short cited excerpts, and a new `reference_only` basis. *(user chose option a)*
- **Which documents are synthetic, and how are they marked?**
  - Synthetic: the QRIS MPM service spec (Generate QR, Query Payment), the troubleshooting SOP, and, later, the test fixtures.
  - Dropped: `qris-national-standard`.
  - Marking: a `synthetic-` ID prefix, the title marker, publisher "Capstone project team" and basis `team_authored`. *(user accepted)*
- **How is permission evidence recorded?**
  - Reference-only: who checked, the date, the pages checked and the finding.
  - Team-authored: the author and the date. *(follows from the two answers above)*
- **Are the three MVP endpoints right?** Yes. `access-token-b2b` is confirmed on the public Keamanan page (service code 73, v1.0). The two MPM endpoints come from the synthetic spec.
- **Deadlines:** P0 sign-off by 2026-11-10; P1–P9 complete by 2026-11-20; capstone submission 2026-12-05. *(confirmed by user)*
