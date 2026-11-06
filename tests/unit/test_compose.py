"""docker-compose.yml: loopback ports, pinned images, memory caps, secrets only from .env (spec AC #1-#5)."""

import re
import xml.etree.ElementTree as ET
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


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_no_container_memory_limits(name):
    """Rootless Podman here has no cgroup controllers; these keys stop containers from starting."""
    service = SERVICES[name]
    assert not {"mem_limit", "mem_reservation", "memswap_limit", "deploy"} & set(service), name


def _clickhouse_cap_mib() -> float:
    mounts = SERVICES["clickhouse"]["volumes"]
    mount = next(m for m in mounts if m.endswith("/etc/clickhouse-server/config.d/memory.xml:ro"))
    xml = ET.parse(ROOT / mount.split(":")[0].removeprefix("./")).getroot()
    return int(xml.findtext("max_server_memory_usage")) / 2**20


def _node_heap_mib(name: str) -> float:
    options = SERVICES[name]["environment"]["NODE_OPTIONS"]
    return float(re.search(r"--max-old-space-size=(\d+)", options).group(1))


def _redis_cap_mib() -> float:
    return to_mib(re.search(r"--maxmemory (\d+[mg])b", str(SERVICES["redis"]["command"])).group(1))


def test_heavy_services_are_capped_from_inside():
    assert _clickhouse_cap_mib() <= 4 * 1024
    assert _node_heap_mib("langfuse-web") <= 1536
    assert _node_heap_mib("langfuse-worker") <= 1536
    assert _redis_cap_mib() > 0


def test_service_caps_total_within_budget():
    total = _clickhouse_cap_mib() + _node_heap_mib("langfuse-web") + _node_heap_mib("langfuse-worker") + _redis_cap_mib()
    assert total <= 8 * 1024, total


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
