# QRIS MPM Service Specification (SYNTHETIC — not an official ASPI/BI document)

| Field | Value |
|---|---|
| Document ID | `synthetic-qris-mpm-service-spec` |
| Version | 0.1.0 |
| Effective date | 2026-11 |
| Author | Kharisma Wardhana, QRIS DevGuard AI capstone team |
| Status | Synthetic test material |

> **Read this first.** This document was written by the capstone team to test QRIS DevGuard AI. It is **not** issued, reviewed or endorsed by ASPI or Bank Indonesia. Its conventions are *modelled on* the public SNAP technical and security standard. Every path, service code, field limit, response code and example value in it is **synthetic** and may differ from the official specification. Do not use it to build or certify a real integration.

## 1. Common rules

### 1.1 Environment

These services exist only in the DevGuard sandbox. There is no production endpoint.

### 1.2 Prerequisite: access token

Each call needs a B2B access token. The token is obtained from the access-token service described in the public SNAP Technical & Security standard (manifest `snap-technical-security-standard`). This document does not redefine that service.

### 1.3 Timestamp format

`X-TIMESTAMP` uses ISO-8601 with milliseconds and a numeric offset: `yyyy-MM-ddTHH:mm:ss.SSS±HH:MM`, for example `2026-11-06T10:15:30.000+07:00`. Timestamps more than 5 minutes from server time are rejected (case code `01`, invalid field format).

### 1.4 Canonicalisation profile `synthetic-snap-symmetric-v1`

This profile is used by both services. The validator implements it as a named, versioned function.

1. **Minify the request body.** Parse the JSON, then re-serialise it with no insignificant whitespace, keeping key order exactly as received.
2. **Hash the body.** `bodyHash = lowercase(hex(SHA-256(minifiedBody)))`
3. **Build the string-to-sign:**
   ```
   stringToSign = HTTPMethod + ":" + EndpointUrl + ":" + AccessToken + ":" + bodyHash + ":" + X-TIMESTAMP
   ```
   `HTTPMethod` is upper case. `EndpointUrl` is the relative path, starting at `/` and without host or query string.
4. **Sign.** `X-SIGNATURE = base64(HMAC_SHA512(key = clientSecret, message = stringToSign))`

The client secret is test-only. It is held by the validator fixture store and never appears in requests to DevGuard, in logs or in answers.

### 1.5 Response code structure

A response code has 7 characters: `HTTP status (3) + service code (2) + case code (2)`. Example: `2004700` means HTTP 200, service 47, case 00.

## 2. Generate QR

### Method and path

`POST /v1.0/qr/qr-mpm-generate`. Synthetic service code **47**.

### Required headers

| Header | Required | Rule |
|---|---|---|
| `Content-Type` | Yes | `application/json` |
| `Authorization` | Yes | `Bearer <access token>` |
| `X-TIMESTAMP` | Yes | See §1.3 |
| `X-SIGNATURE` | Yes | See §1.4 |
| `X-PARTNER-ID` | Yes | Max 36 characters |
| `X-EXTERNAL-ID` | Yes | Numeric string, max 36 characters, unique per partner per day |
| `CHANNEL-ID` | Yes | 5 digits |

### Request fields

| Field | Type | Required | Rule |
|---|---|---|---|
| `partnerReferenceNo` | string | Yes | Max 64 characters, unique per partner |
| `amount.value` | string | Yes | Decimal with exactly 2 fractional digits, e.g. `"10000.00"` |
| `amount.currency` | string | Yes | `"IDR"` only |
| `merchantId` | string | Yes | Max 64 characters |
| `terminalId` | string | No | Max 16 characters |
| `validityPeriod` | string | No | ISO-8601 timestamp after `X-TIMESTAMP` |
| `additionalInfo` | object | No | Free-form; must not contain personal data |

Example (synthetic):

```json
{"partnerReferenceNo":"SYN-REF-0001","amount":{"value":"10000.00","currency":"IDR"},"merchantId":"SYN-MERCHANT-01","terminalId":"T01"}
```

### Response fields

| Field | Type | Always present | Rule |
|---|---|---|---|
| `responseCode` | string | Yes | 7 characters, see §1.5 |
| `responseMessage` | string | Yes | Human-readable |
| `referenceNo` | string | On success | Assigned by the provider |
| `partnerReferenceNo` | string | On success | Echo of the request value |
| `qrContent` | string | On success | QR payload string (synthetic) |

### Signature profile

`HMAC_SHA512` using canonicalisation profile `synthetic-snap-symmetric-v1` (§1.4). Example string-to-sign, with the body hash shortened:

```
POST:/v1.0/qr/qr-mpm-generate:<access token>:9f2c…e1:2026-11-06T10:15:30.000+07:00
```

### Response codes

| Code | Meaning |
|---|---|
| `2004700` | Successful |
| `4004701` | Invalid field format `{field name}` |
| `4004702` | Invalid mandatory field `{field name}` |
| `4014700` | Unauthorized: signature invalid |
| `4014701` | Invalid token (B2B) |
| `4094700` | Conflict: duplicate `partnerReferenceNo` |
| `5004700` | General error |

## 3. Query Payment

### Method and path

`POST /v1.0/qr/qr-mpm-query`. Synthetic service code **51**.

### Required headers

These are the same as Generate QR (§2): `Content-Type`, `Authorization`, `X-TIMESTAMP`, `X-SIGNATURE`, `X-PARTNER-ID`, `X-EXTERNAL-ID` and `CHANNEL-ID`, with the same rules.

### Request fields

| Field | Type | Required | Rule |
|---|---|---|---|
| `originalReferenceNo` | string | One of these two | `referenceNo` from Generate QR |
| `originalPartnerReferenceNo` | string | One of these two | `partnerReferenceNo` from Generate QR |
| `serviceCode` | string | Yes | Must be `"47"`, the service that created the QR |
| `merchantId` | string | Yes | Max 64 characters |

### Response fields

| Field | Type | Always present | Rule |
|---|---|---|---|
| `responseCode` | string | Yes | 7 characters, see §1.5 |
| `responseMessage` | string | Yes | Human-readable |
| `originalReferenceNo` | string | On success | — |
| `originalPartnerReferenceNo` | string | On success | — |
| `serviceCode` | string | On success | `"47"` |
| `latestTransactionStatus` | string | On success | `00` paid, `03` pending, `05` cancelled, `06` failed |
| `transactionStatusDesc` | string | No | Human-readable status |
| `paidTime` | string | When status is `00` | ISO-8601 |
| `amount` | object | On success | Same shape as the request in §2 |

### Signature profile

`HMAC_SHA512` using canonicalisation profile `synthetic-snap-symmetric-v1` (§1.4), with `EndpointUrl` = `/v1.0/qr/qr-mpm-query`.

### Response codes

| Code | Meaning |
|---|---|
| `2005100` | Successful |
| `4005101` | Invalid field format `{field name}` |
| `4005102` | Invalid mandatory field `{field name}` |
| `4015100` | Unauthorized: signature invalid |
| `4015101` | Invalid token (B2B) |
| `4045101` | Transaction not found |
| `5005100` | General error |
