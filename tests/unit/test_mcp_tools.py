"""MCP config and tools (p4 spec AC #2-#5, #13, #17)."""

from __future__ import annotations

from app.mcp_server import config


def test_config_loads_with_loopback_and_rerank_off():
    cfg = config.mcp()
    assert cfg["server"]["host"] == "127.0.0.1"
    assert cfg["search"]["rerank"] is False
    assert cfg["auth"]["algorithm"] == "HS256"
    assert cfg["auth"]["max_ttl_minutes"] <= 60


def test_role_matrix_comes_from_security_yaml():
    assert config.roles() == {"developer", "support_engineer", "kb_maintainer", "admin"}
    assert "kb_maintainer" not in config.tool_roles("validate_test_signature")
    assert "kb_maintainer" in config.tool_roles("search_qris_spec")
    assert config.tool_roles("create_support_ticket")          # exists in the matrix, but no P4 tool
    assert config.tool_roles("no_such_tool") == frozenset()


def test_every_p4_tool_is_in_the_matrix():
    for tool in config.TOOL_NAMES:
        assert config.tool_roles(tool), tool


def test_endpoints_match_scope():
    assert set(config.mcp()["validators"]["endpoints"]) == {"access-token-b2b", "qr-mpm-generate", "qr-mpm-query"}
