"""Bearer token minting and verification (p4 spec AC #1)."""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest

from app.mcp_server import auth, config

SECRET = "s" * 48
A = config.mcp()["auth"]


def verify(token):
    return asyncio.run(auth.HS256Verifier(SECRET).verify_token(token))


def claims(**over):
    now = int(time.time())
    base = {"sub": "dev-1", "role": "developer", "aud": A["audience"], "iss": A["issuer"], "iat": now, "exp": now + 600}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def test_minted_token_verifies_with_role_and_sub():
    tok = verify(auth.mint("dev-1", "developer", secret=SECRET))
    assert tok.subject == "dev-1" and tok.claims["role"] == "developer" and tok.scopes == ["developer"]


@pytest.mark.parametrize("name,token", [
    ("expired", lambda: jwt.encode(claims(iat=int(time.time()) - 1200, exp=int(time.time()) - 600), SECRET, "HS256")),
    ("other secret", lambda: jwt.encode(claims(), "o" * 48, "HS256")),
    ("hs512", lambda: jwt.encode(claims(), SECRET, "HS512")),
    ("alg none", lambda: jwt.encode(claims(), None, "none")),
    ("wrong aud", lambda: jwt.encode(claims(aud="someone-else"), SECRET, "HS256")),
    ("wrong iss", lambda: jwt.encode(claims(iss="elsewhere"), SECRET, "HS256")),
    ("unknown role", lambda: jwt.encode(claims(role="root"), SECRET, "HS256")),
    ("no role", lambda: jwt.encode(claims(role=None), SECRET, "HS256")),
    ("no exp", lambda: jwt.encode(claims(exp=None), SECRET, "HS256")),
    ("ttl over max", lambda: jwt.encode(claims(exp=int(time.time()) + A["max_ttl_minutes"] * 60 + 120), SECRET, "HS256")),
    ("bad sub", lambda: jwt.encode(claims(sub="../etc"), SECRET, "HS256")),
    ("garbage", lambda: "not.a.jwt"),
    ("empty", lambda: ""),
])
def test_invalid_tokens_are_refused(name, token):
    assert verify(token()) is None, name


def test_mint_refuses_unknown_role_long_ttl_and_bad_sub():
    with pytest.raises(ValueError):
        auth.mint("dev-1", "root", secret=SECRET)
    with pytest.raises(ValueError):
        auth.mint("dev-1", "developer", secret=SECRET, ttl_minutes=A["max_ttl_minutes"] + 1)
    with pytest.raises(ValueError):
        auth.mint("dev 1", "developer", secret=SECRET)


def test_load_secret_from_env_and_refuses_short_or_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv(A["secret_env"], "x" * 40)
    assert auth.load_secret(tmp_path / ".env") == "x" * 40
    monkeypatch.delenv(A["secret_env"])
    env = tmp_path / ".env"
    env.write_text(f"{A['secret_env']}=replace-me\n", encoding="utf-8")
    with pytest.raises(auth.AuthConfigError) as exc:
        auth.load_secret(env)
    assert "replace-me" not in str(exc.value)
    env.write_text(f"{A['secret_env']}={'y' * 40}\n", encoding="utf-8")
    assert auth.load_secret(env) == "y" * 40
    env.write_text(f"{A['secret_env']}=short\n", encoding="utf-8")
    with pytest.raises(auth.AuthConfigError):
        auth.load_secret(env)


def test_verifier_repr_hides_secret():
    assert SECRET not in repr(auth.HS256Verifier(SECRET))


def test_default_audience_unchanged_and_api_audience_is_separate():
    """p5 regression: mint/HS256Verifier default to the MCP audience; an API-audience token is a different token."""
    from app.agent import config as agent_config
    api_aud = agent_config.agent()["api"]["audience"]
    default = auth.mint("dev-1", "developer", secret=SECRET)
    api = auth.mint("dev-1", "developer", secret=SECRET, audience=api_aud)
    assert jwt.decode(default, options={"verify_signature": False})["aud"] == A["audience"]
    assert verify(default) is not None and verify(api) is None                       # MCP verifier refuses API token
    api_verifier = auth.HS256Verifier(SECRET, audience=api_aud)
    assert api_verifier.claims(api)["sub"] == "dev-1" and api_verifier.claims(default) is None
