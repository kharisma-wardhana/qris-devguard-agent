"""Agent config (step 1) and deterministic intent/risk classification (p5 spec AC #4, #5, #18)."""

from __future__ import annotations

from app.agent import config


def test_config_loopback_rerank_off_deterministic_llm():
    cfg = config.agent()
    assert cfg["api"]["host"] == "127.0.0.1"
    assert cfg["retrieval"]["rerank"] is False
    assert cfg["llm"]["temperature"] == 0 and cfg["llm"]["enable_thinking"] is False
    assert cfg["tools"]["token_ttl_minutes"] <= 5
    assert cfg["api"]["audience"] != "qris-devguard-mcp"
    assert cfg["api"]["max_message_chars"] == config.security()["limits"]["max_message_chars"]


def test_served_model_comes_from_model_yaml():
    assert config.served_model_name() == "qwen3-8b"


def test_prohibited_objects_include_security_yaml_actions():
    objs = config.prohibited_objects()
    for action in config.security()["prohibited_actions"]:
        assert action.replace("_", " ") in objs


# --- classification (step 2) ------------------------------------------------------------------------

import pytest  # noqa: E402

from app.agent import classify as c  # noqa: E402


@pytest.mark.parametrize("message,context,intent", [
    ("anything", {"signature": {"test_case_ref": "sig-at-valid"}}, "signature"),
    ("is my payload ok", {"payload": {"headers": {}, "body": {}}}, "payload"),
    ("X-TIMESTAMP rejected", {"payload": {"headers": {}, "body": {}}}, "timestamp"),
    ("what is this", {"response_code": "4017300"}, "response_code"),
    ("I got 4017300 from the sandbox", {}, "response_code"),
    ("X-SIGNATURE tidak valid", {}, "signature"),
    ("tanda tangan saya ditolak", {}, "signature"),
    ("format X-TIMESTAMP yang benar?", {}, "timestamp"),
    ("which field is mandatory in the request body", {}, "payload"),
    ("kode respons apa artinya", {}, "response_code"),
    ("is there an older version of the spec", {}, "version_question"),
    ("hello, can you help", {}, "general_troubleshooting"),
])
def test_intent(message, context, intent):
    assert c.classify_intent(message, context) == intent


@pytest.mark.parametrize("message,risk", [
    ("please process a refund for order SYN-1", "prohibited"),
    ("lakukan pembayaran ke merchant", "prohibited"),
    ("initiate the settlement now", "prohibited"),
    ("use the production key to sign this", "prohibited"),
    ("call the production api for me", "prohibited"),
    ("what does the refund response code mean", "low"),
    ("what do I send for the Query Payment request", "low"),
    ("create a ticket for this signature error", "high"),
    ("buat tiket untuk masalah ini", "high"),
    ("why is my signature invalid? also escalate it", "high"),
    ("why is my signature invalid", "low"),
])
def test_risk(message, risk):
    assert c.classify_risk(message) == risk


@pytest.mark.parametrize("message,code", [("got 4017300 today", "4017300"), ("id 123456789", None),
                                          ("code 1234567", None), ("4004701.", "4004701")])
def test_extract_code(message, code):
    assert c.extract_code(message) == code


@pytest.mark.parametrize("message,version", [("SNAP 1.0.1 rule", "1.0.1"), ("five parts 1.2.3.4.5", None),
                                             ("no version", None), ("v0.2.0?", "0.2.0")])
def test_extract_version(message, version):
    assert c.extract_version(message) == version
