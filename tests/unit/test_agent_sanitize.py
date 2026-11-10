"""sanitize_input redaction (p5 spec AC #3)."""

from __future__ import annotations

import json

import pytest

from app.agent import config
from app.agent.sanitize import REDACTED, sanitize

PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nSYNTHETICNOTAREALKEY\n-----END PRIVATE KEY-----"
TOKEN = "eyJhbGciOiJIUzI1NiJ9SYNTHETICTOKENVALUE0123"


def test_pem_block_in_message_is_redacted_whole():
    msg, _, found = sanitize(f"my key is\n{PEM}\nwhy invalid?", {})
    assert "MIIE" not in msg and "SYNTHETICNOTAREALKEY" not in msg and "BEGIN PRIVATE KEY" not in msg
    assert "private_key" in found and msg.endswith("why invalid?")


def test_bearer_token_in_message():
    msg, _, found = sanitize(f"Authorization: Bearer {TOKEN} fails", {})
    assert TOKEN not in msg and "bearer_token" in found


@pytest.mark.parametrize("field", config.security()["redact_fields"])
def test_every_redact_field_as_text_pair(field):
    msg, _, found = sanitize(f'{field}: "S3cr3tValueSYN" and more', {})
    assert "S3cr3tValueSYN" not in msg and found


@pytest.mark.parametrize("field", config.security()["redact_fields"])
def test_every_redact_field_in_nested_context(field):
    ctx = {"payload": {"headers": {field.upper(): "S3cr3tValueSYN"}, "body": {"additionalInfo": {field: "S3cr3tValueSYN"}}}}
    _, out, found = sanitize("q", ctx)
    assert "S3cr3tValueSYN" not in json.dumps(out) and found


def test_authorization_keeps_scheme_and_signature_block_survives():
    ctx = {"payload": {"headers": {"Authorization": f"Bearer {TOKEN}", "X-SIGNATURE": "c2lnbmF0dXJl"}, "body": {}},
           "signature": {"algorithm": "HMAC_SHA512", "fixture_id": "syn-hmac-01", "test_case_ref": "sig-gen-valid"}}
    _, out, _ = sanitize("q", ctx)
    assert out["payload"]["headers"]["Authorization"] == f"Bearer {REDACTED}"
    assert out["payload"]["headers"]["X-SIGNATURE"] == REDACTED
    assert out["signature"] == ctx["signature"]          # the block name `signature` is not a secret field


def test_input_not_mutated_and_clean_input_untouched():
    ctx = {"payload": {"headers": {"Authorization": "Bearer abc"}, "body": {"merchantId": "SYN-MERCHANT-01"}}}
    before = json.dumps(ctx)
    msg, out, found = sanitize("why does X-TIMESTAMP fail?", ctx)
    assert json.dumps(ctx) == before
    assert msg == "why does X-TIMESTAMP fail?" and out["payload"]["body"] == {"merchantId": "SYN-MERCHANT-01"}
