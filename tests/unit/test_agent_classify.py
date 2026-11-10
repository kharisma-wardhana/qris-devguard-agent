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
