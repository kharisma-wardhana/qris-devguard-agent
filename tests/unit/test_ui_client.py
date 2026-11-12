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


# --- client (AC #14, #17) ----------------------------------------------------------------------------------------

import json  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402

from app.ui import client as uic  # noqa: E402

SECRET = "x" * 48
DEV = uic.Identity("demo-dev", "developer", "Developer")


def make(handler, clock=lambda: 1_800_000_000.0, ident=DEV):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)
    c = uic.Client(ident, secret=SECRET, api_url="http://127.0.0.1:9", transport=httpx.MockTransport(wrapped),
                   clock=clock)
    return c, seen


def ok(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def test_identities_from_config_and_approver_flag():
    ids = uic.identities()
    assert [i.sub for i in ids] == ["demo-dev", "demo-approver"]
    assert [uic.is_approver(i) for i in ids] == [False, True]


def test_token_claims_and_no_token_in_repr():
    c, seen = make(ok({"threads": []}))
    c.list_threads()
    tok = seen[0].headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(tok, SECRET, algorithms=["HS256"], audience=config.agent()["api"]["audience"],
                        options={"verify_exp": False, "verify_iat": False})     # fixed fake clock
    assert claims["sub"] == "demo-dev" and claims["role"] == "developer"
    assert claims["exp"] - claims["iat"] == config.agent()["ui"]["token_ttl_minutes"] * 60
    assert tok not in repr(c) and SECRET not in repr(c)
    assert tok not in str(seen[0].url)


def test_token_reused_then_reminted_before_expiry():
    now = [1_800_000_000.0]
    c, seen = make(ok({"threads": []}), clock=lambda: now[0])
    c.list_threads()
    now[0] += 60
    c.list_threads()
    now[0] += config.agent()["ui"]["token_ttl_minutes"] * 60 - 100     # inside the re-mint margin
    c.list_threads()
    toks = [r.headers["Authorization"] for r in seen]
    assert toks[0] == toks[1] and toks[2] != toks[1]


def test_routes_and_bodies():
    c, seen = make(lambda r: httpx.Response(200, json={"thread_id": "t", "runs": [], "approvals": [], "threads": [],
                                                       "alerts": [], "skipped": 0}))
    c.create_thread()
    c.send("t" * 20, "hello", {"api_family": "snap"})
    c.pending_approvals()
    c.decide("a-1", "reject", "not needed")
    c.alerts()
    c.history("t" * 20)
    calls = [(r.method, r.url.path, r.url.query.decode()) for r in seen]
    assert calls == [("POST", "/api/v1/threads", ""), ("POST", f"/api/v1/threads/{'t' * 20}/messages", ""),
                     ("GET", "/api/v1/approvals", "status=pending"), ("POST", "/api/v1/approvals/a-1/decision", ""),
                     ("GET", "/api/v1/alerts", ""), ("GET", f"/api/v1/threads/{'t' * 20}/messages", "")]
    assert json.loads(seen[1].content) == {"message": "hello", "context": {"api_family": "snap"}}
    assert json.loads(seen[3].content) == {"decision": "reject", "reason": "not needed"}


def test_envelope_becomes_ui_error_without_values():
    env = {"code": "INVALID_ARGUMENT", "message": "one or more fields are invalid", "correlation_id": "abc123",
           "field_errors": [{"field": "context.response_code", "reason": "string_pattern_mismatch", "input": "S3CRET"}]}
    c, _ = make(ok(env, 422))
    with pytest.raises(uic.ApiError) as e:
        c.send("t" * 20, "q", {"response_code": "S3CRET"})
    err = e.value
    assert (err.status, err.code, err.correlation_id) == (422, "INVALID_ARGUMENT", "abc123")
    assert err.field_errors == [{"field": "context.response_code", "reason": "string_pattern_mismatch"}]
    assert "S3CRET" not in str(vars(err))


def test_rate_limit_exposes_retry_after():
    c, _ = make(lambda r: httpx.Response(429, headers={"Retry-After": "7"},
                                         json={"code": "RATE_LIMITED", "message": "m", "correlation_id": "c"}))
    with pytest.raises(uic.ApiError) as e:
        c.list_threads()
    assert e.value.code == "RATE_LIMITED" and e.value.retry_after == "7"


def test_non_envelope_and_unreachable():
    c, _ = make(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(uic.ApiError) as e:
        c.list_threads()
    assert e.value.code == "HTTP_502" and "html" not in e.value.message

    def boom(request):
        raise httpx.ConnectError("refused")
    c, _ = make(boom)
    with pytest.raises(uic.ApiError) as e:
        c.list_threads()
    assert e.value.code == "API_UNREACHABLE"


def test_ready_needs_no_token_and_accepts_503():
    c, seen = make(ok({"status": "not_ready", "checks": {"mcp": "up", "llm": "down"}}, 503))
    assert c.ready()["checks"]["llm"] == "down" and "Authorization" not in seen[0].headers


@pytest.mark.parametrize("text,expected", [("", None), ("  ", None), ('{"api_family": "snap"}', {"api_family": "snap"})])
def test_parse_context(text, expected):
    assert uic.parse_context(text) == expected


@pytest.mark.parametrize("text", ["[1, 2]", "not json", '"s"'])
def test_parse_context_rejects_non_objects(text):
    with pytest.raises(ValueError):
        uic.parse_context(text)
