"""Start-up egress check (p7 spec Behaviour §5, AC #16; T9).

Every outbound destination in this system comes from configuration (vLLM base URL, Qdrant, PostgreSQL, the MCP
URL, Langfuse); no tool or model output ever chooses one. So the check runs once, when a process starts:

- the scheme must be http, https or postgresql;
- the host must be a loopback literal (the IPv4 loopback block or ::1) or a name in `egress_allowlist`;
- anything else is refused: private, link-local, cloud-metadata and other literal addresses, and unlisted names.

`EgressRefused` names the setting, never its value (a DSN can carry a password).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from app.security import security

SCHEMES = {"http", "https", "postgresql", "postgres", "postgresql+psycopg"}


class EgressRefused(RuntimeError):
    def __init__(self, setting: str, reason: str) -> None:
        super().__init__(f"{setting}: outbound destination refused ({reason}); see configs/security.yaml "
                         "egress_allowlist")
        self.setting = setting
        self.reason = reason


def _check_conninfo(setting: str, info: str, names: set[str]) -> None:
    """A libpq `key=value` connection string (tests pass these as DATABASE_URL): every host must pass."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        hosts = str(conninfo_to_dict(info).get("host") or "").split(",")
    except Exception:  # noqa: BLE001 - psycopg raises ProgrammingError; any failure is "unparseable"
        raise EgressRefused(setting, "unparseable") from None
    for host in hosts:
        host = host.strip()
        if not host:
            raise EgressRefused(setting, "no host")
        if not host.startswith("/"):                     # a Unix socket path is not network egress
            check(setting, f"postgresql://{host}/", names)


def check(setting: str, url: str, allowlist: Iterable[str] | None = None) -> None:
    names = {n.lower() for n in (allowlist if allowlist is not None else security()["egress_allowlist"])}
    if "://" not in url and "=" in url:
        _check_conninfo(setting, url, names)
        return
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        scheme = parts.scheme.lower()
    except ValueError:
        raise EgressRefused(setting, "unparseable") from None
    if scheme not in SCHEMES:
        raise EgressRefused(setting, "scheme not allowed")
    if not host:
        raise EgressRefused(setting, "no host")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if host in names:
            return
        raise EgressRefused(setting, "host not in egress_allowlist") from None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped                      # ::ffff:a.b.c.d is judged as a.b.c.d
    if addr.is_loopback:
        return
    if addr.is_link_local:
        raise EgressRefused(setting, "link-local or metadata address")
    if addr.is_private:
        raise EgressRefused(setting, "private address")
    raise EgressRefused(setting, "literal address not in egress_allowlist")


def check_all(destinations: Mapping[str, str | None], allowlist: Iterable[str] | None = None) -> None:
    """Check every configured destination; unset ones (None/empty) are skipped."""
    for setting, url in destinations.items():
        if url:
            check(setting, url, allowlist)
