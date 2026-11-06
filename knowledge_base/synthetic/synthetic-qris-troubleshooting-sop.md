# QRIS Integration Troubleshooting SOP (SYNTHETIC — not an official ASPI/BI document)

| Field | Value |
|---|---|
| Document ID | `synthetic-qris-troubleshooting-sop` |
| Version | 0.1.0 |
| Effective date | 2026-11 |
| Author | Kharisma Wardhana, QRIS DevGuard AI capstone team |
| Status | Synthetic test material |

> **Read this first.** This SOP was written by the capstone team for QRIS DevGuard AI. It does not reproduce any organisation's internal procedure. It is an operating procedure only. Protocol rules (fields, formats, codes) must always be cited from an approved specification, never from this SOP.

## 1. Purpose

This SOP gives developers and support engineers one repeatable way to diagnose sandbox QRIS integration failures. It separates confirmed findings from guesses, and it escalates instead of improvising when evidence runs out.

## 2. Diagnostic sequence

1. Record the API family, endpoint, environment and specification version. Stop if the environment is not `sandbox`.
2. Remove secrets from the material provided. Never ask for private keys, client secrets, access tokens or merchant credentials.
3. Classify the symptom as one of: signature, timestamp, payload, response code, sequence/state, or general.
4. Retrieve the relevant specification sections and confirm their version. If sources disagree, show both and ask which version applies.
5. Run the matching deterministic check with a named test fixture (§3).
6. Report findings in three groups: confirmed, likely and unverified.
7. Give remediation steps, each tied to a citation or a check result.
8. Escalate when §4 applies. Creating a ticket always needs human approval.

## 3. Symptom playbooks

### 3.1 Signature

- Confirm which profile applies:
  - asymmetric access-token signature (SHA256withRSA), or
  - symmetric transactional signature (HMAC_SHA512, profile `synthetic-snap-symmetric-v1`).
- Run `validate_test_signature` with the fixture ID. Report `VALID`, `INVALID` or `INCONCLUSIVE` exactly as returned.
- Common causes to check against the cited spec:
  - the body was not minified, or was re-ordered, before hashing
  - the wrong `EndpointUrl` (host or query string included)
  - an upper-case hex body hash
  - a timestamp different from the `X-TIMESTAMP` header
  - the wrong key

### 3.2 Timestamp

- Check the format and offset against the cited specification, and check clock skew against the allowed window.
- Run `validate_request_payload`, which applies the timestamp format rules.

### 3.3 Payload

- Run `validate_request_payload` for the endpoint and spec version.
- Report each error with its JSON pointer and the cited rule.
- Do not correct the payload silently. Show the user what to change.

### 3.4 Response code

- Run `lookup_response_code` for an exact match on the API family and version.
- If it returns `NOT_FOUND`, say so and ask for the source or version. Similar codes may be offered only when labelled as suggestions.

### 3.5 Sequence or state

- Answer from cited documentation only. No deterministic check exists in the MVP, so escalate if the documents don't settle it.

## 4. Escalation criteria

Escalate when any of these is true:
- a required source is missing or contradictory
- a check returns `INCONCLUSIVE` or cannot reproduce the issue
- the issue looks environment-specific
- production-only evidence would be needed
- the request would need a production action

## 5. Evidence requirements

Every final answer contains:
- a summary
- findings (confirmed / likely / unverified)
- validation results (tool, fixture, result, limitations)
- recommended next steps
- sources (title, version, section, URL or document ID, and citation ID)
- confidence and limitations

A citation of a synthetic document keeps its "SYNTHETIC" label wherever it is shown.
