"""docker-compose.yml: loopback ports, pinned images, memory caps, secrets only from .env (spec AC #1-#5)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TEXT)
SERVICES = COMPOSE["services"]
EXAMPLE = {
    k: v for k, v in (
        l.split("=", 1) for l in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in l and not l.startswith("#")
    )
}
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|SALT|ENCRYPTION_KEY|AUTH|PUBLIC_KEY)$")


def to_mib(limit: str) -> float:
    number, unit = re.fullmatch(r"(\d+)([mg])", str(limit).lower()).groups()
    return int(number) * (1024 if unit == "g" else 1)


def test_exactly_the_specified_services():
    assert set(SERVICES) == {
        "postgres", "qdrant", "clickhouse", "minio", "redis", "langfuse-worker", "langfuse-web",
    }


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_ports_bound_to_loopback(name):
    for port in SERVICES[name].get("ports", []):
        assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_image_pinned(name):
    image = SERVICES[name]["image"]
    assert not image.endswith(":latest")
    assert "@sha256:" in image or re.search(r":[\w.-]*\d[\w.-]*$", image), f"{name}: {image}"


def test_memory_limits_present_and_total_within_budget():
    limits = {name: s.get("mem_limit") for name, s in SERVICES.items()}
    assert all(limits.values()), limits
    assert sum(to_mib(v) for v in limits.values()) <= 12 * 1024


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_secret_env_values_come_from_env_file(name):
    for key, value in (SERVICES[name].get("environment") or {}).items():
        if SECRET_NAME.search(key):
            assert re.fullmatch(r"\$\{[A-Z_]+:\?[^}]*\}", str(value)), f"{name}.{key} is not read from .env"


def test_every_referenced_variable_is_in_env_example_as_placeholder():
    referenced = set(re.findall(r"\$\{([A-Z_]+)", COMPOSE_TEXT))
    assert referenced, "compose references no variables"
    for var in referenced:
        assert var in EXAMPLE, f"{var} missing from .env.example"
        if SECRET_NAME.search(var):
            assert EXAMPLE[var] == "replace-me", var


def test_redis_password_not_hardcoded():
    assert "${REDIS_AUTH" in str(SERVICES["redis"]["command"])


def test_langfuse_telemetry_off():
    for name in ("langfuse-web", "langfuse-worker"):
        assert SERVICES[name]["environment"]["TELEMETRY_ENABLED"] == "false"
