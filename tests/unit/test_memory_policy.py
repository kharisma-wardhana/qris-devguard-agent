"""Memory policy without a database (p8 spec Behaviour §1, AC #2, #3, #7)."""

from __future__ import annotations

import pytest

from app.memory import policy


@pytest.mark.parametrize("message,hit", [
    ("remember: answer in Indonesian", ("language", "id")),
    ("Please REMEMBER: always answer in bahasa indonesia. Then: what does 4017300 mean?", ("language", "id")),
    ("ingat: jawab dalam bahasa Indonesia", ("language", "id")),
    ("remember: reply in English", ("language", "en")),
    ("ingat: jawab pakai bahasa Inggris", ("language", "en")),
    ("remember: keep it brief", ("detail", "brief")),
    ("ingat: jawab singkat", ("detail", "brief")),
    ("remember: detailed answers", ("detail", "detailed")),
    ("ingat: jawab lengkap", ("detail", "detailed")),
])
def test_configured_phrases_map_to_one_allowed_value(message, hit):
    assert policy.match_phrase(message) == hit
    assert policy.is_allowed(*hit)


@pytest.mark.parametrize("message", [
    "remember: my merchant ID is 12345",
    "ingat: kunci saya ada di folder keys",
    "remember: answer in French",
])
def test_remember_prefix_without_a_configured_phrase_is_unrecognised(message):
    assert policy.match_phrase(message) == "unrecognised"


@pytest.mark.parametrize("message", [
    "What does response code 4017300 mean?",
    "I want to remember how the signature works",          # no "remember:" prefix
    "answer in Indonesian please",                          # a request for this answer, not a save
])
def test_no_phrase_is_none(message):
    assert policy.match_phrase(message) is None


def test_decide_reasons():
    assert policy.decide("what is X?", route="read", secret_detected=False) == (policy.NOT_DEFAULT, None)
    assert policy.decide("remember: answer in English", route="read", secret_detected=False) == (
        policy.SAVED, ("language", "en"))
    # a secret anywhere in the run's input blocks every save, even a matching phrase
    assert policy.decide("remember: answer in English", route="read", secret_detected=True) == (policy.NOT_SECRET, None)
    assert policy.decide("remember: my id is 1", route="read", secret_detected=False) == (policy.NOT_UNRECOGNISED, None)
    for route in ("refuse", "clarify", "draft"):
        assert policy.decide("remember: answer in English", route=route, secret_detected=False) == (policy.NOT_ROUTE, None)


@pytest.mark.parametrize("key,value,ok", [
    ("language", "id", True), ("language", "en", True), ("detail", "brief", True), ("detail", "detailed", True),
    ("language", "fr", False), ("detail", "", False), ("tone", "formal", False), ("language", "ID", False),
])
def test_only_configured_keys_and_values(key, value, ok):
    assert policy.is_allowed(key, value) is ok


def test_note_lines_are_fixed_text_in_key_order():
    assert policy.note_lines({"language": "id", "detail": "brief"}) == ["Keep the answer brief.", "Answer in Indonesian."]
    assert policy.note_lines({"language": "xx"}) == []


def test_hint_lists_allowed_values_only():
    hint = policy.saveable_hint()
    assert "language: id, en" in hint and "detail: brief, detailed" in hint


def _pem() -> str:
    return "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize("text,kind", [
    ("Rotate the client_secret=abcd1234efgh before testing", "secret_field"),
    ("Use Authorization: Bearer " + "x" * 24, "bearer_token"),
    ("key: " + _pem(), "private_key"),
    ("Contact " + "dev" + "@" + "example.com" + " for help", "email"),
    ("Call " + "0812" + "34567890", "phone"),
    ("Trx " + "1" * 12 + " failed", "long_digit_run"),
])
def test_pattern_text_refusal_kinds(text, kind):
    assert kind in policy.refusal_kinds(text)


@pytest.mark.parametrize("text", [
    "Signature fails when the timestamp lacks a timezone offset; use +07:00 in X-TIMESTAMP.",
    "validate_test_signature -> INVALID was caused by a trailing newline in the minified body.",
    "Response code 4017300 means the token expired; request a new access token.",   # 7 digits: a SNAP code
])
def test_ordinary_troubleshooting_text_is_accepted(text):
    assert policy.refusal_kinds(text) == []
