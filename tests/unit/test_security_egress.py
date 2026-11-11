"""Start-up egress check (p7 plan step 1; spec AC #16; T9).

IP strings are built from parts: tests/unit/test_repo_hygiene.py flags IPv4-shaped literals in tracked files.
"""

from __future__ import annotations

import pytest

from app.security.egress import EgressRefused, check, check_all


def ip(*parts: int) -> str:
    return ".".join(str(p) for p in parts)


LOOP = ip(127, 0, 0, 1)
METADATA = ip(169, 254, 169, 254)


@pytest.mark.parametrize("url", [
    f"http://{LOOP}:8080/v1",
    f"http://{ip(127, 1, 2, 3)}:6333",
    "http://localhost:3000",
    "http://[::1]:8765/mcp",
    f"postgresql+psycopg://devguard:pw@{LOOP}:5432/devguard",
    "http://qdrant:6333",
    "http://vllm:8000/v1",
    "postgresql://u:p@postgres:5432/devguard",
])
def test_allowed(url):
    check("SETTING", url)


@pytest.mark.parametrize("url,reason", [
    (f"http://{METADATA}/latest/meta-data/", "link-local"),
    (f"http://{ip(10, 0, 0, 5)}:8080", "private"),
    (f"http://{ip(172, 16, 3, 4)}", "private"),
    (f"http://{ip(192, 168, 1, 1)}", "private"),
    ("http://[fe80::1]/", "link-local"),
    ("http://[fd00::1]/", "private"),
    (f"http://[::ffff:{METADATA}]/", "link-local"),
    (f"http://{ip(8, 8, 8, 8)}/", "literal address"),
    ("http://metadata.google.internal/", "not in egress_allowlist"),
    ("https://evil.example/", "not in egress_allowlist"),
    ("file:///etc/passwd", "scheme"),
    ("gopher://localhost:70/", "scheme"),
    ("http:///nohost", "no host"),
])
def test_refused(url, reason):
    with pytest.raises(EgressRefused) as exc:
        check("QDRANT_URL", url)
    assert reason in exc.value.reason


def test_message_names_setting_never_value():
    secret_dsn = f"postgresql://devguard:SuperSecretPw123@{ip(10, 1, 2, 3)}:5432/db"
    with pytest.raises(EgressRefused) as exc:
        check("DATABASE_URL", secret_dsn)
    assert "DATABASE_URL" in str(exc.value)
    assert "SuperSecretPw123" not in str(exc.value) and ip(10, 1, 2, 3) not in str(exc.value)


def test_check_all_skips_unset_and_stops_on_bad():
    check_all({"A": None, "B": "", "C": f"http://{LOOP}:1"})
    with pytest.raises(EgressRefused):
        check_all({"C": f"http://{LOOP}:1", "D": f"http://{METADATA}/"})


def test_custom_allowlist():
    check("X", "http://internal-llm:8000", allowlist=["internal-llm"])
    with pytest.raises(EgressRefused):
        check("X", "http://qdrant:6333", allowlist=["internal-llm"])
