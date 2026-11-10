"""Bearer authentication for the MCP server (p4 spec AC #1; T1, T2).

Tokens are HS256 JWTs signed with MCP_AUTH_SECRET, carrying `sub` and `role`. Only HS256 is
accepted; `exp`, `iat`, `sub`, `aud` and `iss` are required, and a token may live at most
`max_ttl_minutes`. An invalid token makes the SDK's bearer middleware answer HTTP 401 before any
tool runs. The secret is never logged or returned.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from app.mcp_server import config

SUB = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


class AuthConfigError(RuntimeError):
    """MCP_AUTH_SECRET missing or too short. The message never contains the value."""


def load_secret(env_file: Path | None = None) -> str:
    """MCP_AUTH_SECRET from the environment, else from the repo's .env (written by scripts/init_env.py)."""
    a = config.mcp()["auth"]
    secret = os.environ.get(a["secret_env"])
    env_file = env_file or (config.ROOT / ".env")
    if not secret and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == a["secret_env"]:
                secret = value.strip()
    if not secret or secret == "replace-me" or len(secret) < a["min_secret_chars"]:
        raise AuthConfigError(f"{a['secret_env']} is missing or shorter than {a['min_secret_chars']} characters; "
                              "run scripts/init_env.py or set it in the environment")
    return secret


def mint(sub: str, role: str, *, secret: str, ttl_minutes: int | None = None, now: int | None = None) -> str:
    a = config.mcp()["auth"]
    ttl = a["default_ttl_minutes"] if ttl_minutes is None else ttl_minutes
    if not SUB.match(sub):
        raise ValueError("sub must match [A-Za-z0-9._@-]{1,64}")
    if role not in config.roles():
        raise ValueError(f"unknown role; one of {sorted(config.roles())}")
    if not 1 <= ttl <= a["max_ttl_minutes"]:
        raise ValueError(f"ttl_minutes must be between 1 and {a['max_ttl_minutes']}")
    iat = int(time.time()) if now is None else now
    claims = {"sub": sub, "role": role, "aud": a["audience"], "iss": a["issuer"], "iat": iat, "exp": iat + ttl * 60}
    return jwt.encode(claims, secret, algorithm="HS256")


class HS256Verifier:
    """`mcp.server.auth.provider.TokenVerifier` for locally minted HS256 tokens."""

    def __init__(self, secret: str):
        self._secret = secret
        self._cfg = config.mcp()["auth"]

    def __repr__(self) -> str:
        return "HS256Verifier(<secret hidden>)"

    def claims(self, token: str) -> dict | None:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"], audience=self._cfg["audience"],
                                issuer=self._cfg["issuer"],
                                options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        except jwt.PyJWTError:
            return None
        if not isinstance(claims.get("sub"), str) or not SUB.match(claims["sub"]):
            return None
        if claims.get("role") not in config.roles():
            return None
        if not isinstance(claims.get("iat"), int) or claims["exp"] - claims["iat"] > self._cfg["max_ttl_minutes"] * 60:
            return None
        return claims

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = self.claims(token)
        if claims is None:
            return None
        return AccessToken(token=token, client_id=claims["sub"], scopes=[claims["role"]], expires_at=claims["exp"],
                           subject=claims["sub"], claims={"iss": claims["iss"], "role": claims["role"]})


def current_identity() -> tuple[str, str] | None:
    """(sub, role) of the caller, from the SDK's per-request auth context; None if unauthenticated."""
    token = get_access_token()
    if token is None or not token.claims or token.claims.get("role") not in config.roles():
        return None
    return str(token.subject or token.client_id), str(token.claims["role"])
