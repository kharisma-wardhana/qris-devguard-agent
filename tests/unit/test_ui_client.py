"""p9 UI client and config (docs/p9-demo-ui-alerts/spec.md Behaviour §1, §7; AC #12, #14, #17, #19)."""

from __future__ import annotations

from app.agent import config


def test_ui_config_is_loopback_and_complete():
    ui = config.agent()["ui"]
    assert ui["host"] == "127.0.0.1"
    assert ui["api_url"].startswith("http://127.0.0.1:")
    assert 1 <= ui["token_ttl_minutes"] <= 60                 # configs/mcp.yaml caps tokens at 60 min
    roles = {i["role"] for i in ui["identities"]}
    assert "developer" in roles
    assert roles & set(config.security()["approver_roles"])   # the demo needs an approver
    assert all(i["sub"].startswith("demo-") for i in ui["identities"])   # synthetic subjects only


def test_alerts_config_keys():
    a = config.agent()["alerts"]
    assert a["store_path"].startswith("var/")                 # git-ignored
    assert a["secret_env"] == "LANGFUSE_ALERT_WEBHOOK_SECRET"
    assert a["max_body_bytes"] == 65536 and a["max_records"] == 500 and a["max_clock_skew_seconds"] == 600
    assert a["title_max_chars"] == 200 and a["body_max_chars"] == 1000 and a["list_limit"] == 50
    assert a["langfuse_base_url"].endswith("/")
