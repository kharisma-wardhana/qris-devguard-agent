# QRIS DevGuard AI — Phase P0: Scope and Source Inventory

Status: draft, pending team sign-off · Source: `docs/QRIS_DevGuard_AI_PRD_TRD.md` §27 (Phase P0)

Phase P0 exit criteria (TRD §27):

- [x] **No production connectivity.** See §3 and `docs/threat-model.md` §2.
- [ ] **Data owners and source permissions identified.** See §4 and `knowledge_base/manifests/sources.yaml`.

The criteria are met when every row in §5 has an owner sign-off and `uv run python scripts/check_sources.py` passes.

## 1. Approved use case

There is one bounded workflow (PRD §1): **diagnosing sandbox QRIS/SNAP API integration issues involving authentication/signatures, request formatting, timestamp construction and response codes.** The diagnosis is backed by cited documentation and deterministic validators.

| Symptom class | In MVP | Deterministic check | Notes |
|---|---|---|---|
| Access-token signature (SHA256withRSA) | Yes | `validate_test_signature` | Dummy RSA key pairs only |
| Transactional signature (HMAC_SHA512) | Yes | `validate_test_signature` | Test HMAC secrets only |
| Timestamp / ISO-8601 format | Yes | `validate_request_payload` (format rules) | — |
| Request payload / headers | Yes | `validate_request_payload` | Versioned JSON Schemas |
| Response-code meaning | Yes | `lookup_response_code` | Unknown codes return `NOT_FOUND` |
| API sequence / state errors | Retrieval only | none | Answer from cited docs; escalate if unclear |

### 1.1 Endpoint coverage (MVP)

Validators and schemas will initially cover only the endpoints below. Each one must be confirmed against an approved source before Phase P4.

| Endpoint ID (internal) | Purpose | Signature profile | Confirmed in source |
|---|---|---|---|
| `access-token-b2b` | Obtain B2B access token | SHA256withRSA | [ ] |
| `qr-mpm-generate` | Generate QRIS MPM | HMAC_SHA512 | [ ] |
| `qr-mpm-query` | Query payment status | HMAC_SHA512 | [ ] |

The endpoint IDs are internal identifiers. Exact paths, field lists and canonical string formats come from the approved sources, not from this document.

## 2. Users and roles

| Role | Can do | Cannot do |
|---|---|---|
| `developer` | Create threads, submit queries, run read-only tools, request ticket drafts | Approve actions, publish documents |
| `support_engineer` | Everything `developer` can, plus approve or reject sandbox ticket creation for their team's threads | Publish documents |
| `kb_maintainer` | Upload, validate and publish approved document versions | Approve tickets (separation of duties) |
| `admin` | Manage users and roles, delete memory, view audit logs | Bypass approval gates |

## 3. Boundaries

**In scope:** sandbox environment only, synthetic merchant and transaction references, dummy RSA keys, test HMAC secrets, and a local sandbox ticket table.

**Prohibited.** These tools must not exist, or must hard-deny (TRD §15.1):
- Payment, refund, reversal and settlement actions
- Merchant activation
- Any production API call
- Production credential operations
- Arbitrary URL fetch, SQL, shell or filesystem tools exposed to the model

**Inputs that are never accepted:** production private keys, live HMAC secrets, access tokens, merchant credentials, customer personal data and production logs. Inputs that contain these are rejected or redacted at `sanitize_input` before persistence or tracing.

**Network:** the only outbound targets allowed are the configured vLLM, Qdrant, PostgreSQL and Langfuse hosts (`configs/security.yaml`). No bank, ASPI or merchant production hosts are configured anywhere.

## 4. Data owners

| Data asset | Owner (role) | Storage | Retention |
|---|---|---|---|
| Approved specification sources | Knowledge base maintainer | `knowledge_base/sources/` (access-controlled, not committed if licence forbids) | Until superseded |
| Source manifest | Knowledge base maintainer | `knowledge_base/manifests/sources.yaml` (committed) | Versioned in git |
| Internal SOP excerpts | Capstone team lead | `skills/qris-integration-troubleshooting/references/` | Versioned in git |
| Synthetic payload fixtures | Capstone team (validator owner) | `knowledge_base/fixtures/payloads/` (committed) | Versioned in git |
| Test key material (RSA/HMAC) | Capstone team (validator owner) | Outside git (dev secrets or a local fixture dir listed in `.gitignore`) | For the life of the project |
| Graph checkpoints | Platform owner | PostgreSQL | 30 days |
| Audit events | Platform owner | PostgreSQL | 90 days |
| Long-term memory | Platform owner, with per-user deletion rights | PostgreSQL | 90-day expiry |
| Langfuse traces | Platform owner | Langfuse (synthetic data only) | Per Langfuse project setting |

The named people for each role are recorded in §5 during sign-off.

## 5. Sign-off

| Item | Role | Name | Date |
|---|---|---|---|
| Use case and boundaries (§1, §3) | Team lead | | |
| Source inventory and permissions | KB maintainer | | |
| Threat model | Security reviewer | | |
| SOP skill outline | Support engineer | | |
