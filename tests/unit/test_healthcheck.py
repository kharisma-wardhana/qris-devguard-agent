"""Health-check CLI verdicts, request shape and secret hiding (spec AC #6, #7, #8)."""

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import healthcheck  # noqa: E402

SECRETS = {
    "VLLM_API_KEY": "sentinel-vllm-key-1111",
    "LANGFUSE_PUBLIC_KEY": "pk-lf-sentinel-2222",
    "LANGFUSE_SECRET_KEY": "sk-lf-sentinel-3333",
    "DB_PASSWORD": "sentinel-db-pass-4444",
}
ENV = {
    "VLLM_BASE_URL": "http://vllm.test:8080/v1",
    "VLLM_API_KEY": SECRETS["VLLM_API_KEY"],
    "DATABASE_URL": f"postgresql+psycopg://devguard:{SECRETS['DB_PASSWORD']}@db.test:5432/devguard",
    "QDRANT_URL": "http://qdrant.test:6333",
    "LANGFUSE_HOST": "http://langfuse.test:3000",
    "LANGFUSE_PUBLIC_KEY": SECRETS["LANGFUSE_PUBLIC_KEY"],
    "LANGFUSE_SECRET_KEY": SECRETS["LANGFUSE_SECRET_KEY"],
}
SSE = (
    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"ready"}}]}\n\n'
    b"data: [DONE]\n\n"
)


class Services:
    """Fake vLLM, Qdrant and Langfuse behind one httpx.MockTransport."""

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        routes = {
            "/health": ("vllm_health", lambda: httpx.Response(200)),
            "/v1/models": ("vllm_models", lambda: httpx.Response(200, json={"data": [{"id": "qwen3-8b"}]})),
            "/v1/chat/completions": ("vllm_chat", lambda: httpx.Response(
                200, content=SSE, headers={"content-type": "text/event-stream"})),
            "/readyz": ("qdrant", lambda: httpx.Response(200, text="all shards are ready")),
            "/api/public/health": ("langfuse_health", lambda: httpx.Response(200, json={"status": "OK"})),
            "/api/public/ingestion": ("langfuse_trace", lambda: httpx.Response(
                207, json={"successes": [{"id": "x", "status": 201}], "errors": []})),
        }
        check, ok = routes[path]
        if check == self.fail:
            if check == "vllm_chat":
                raise httpx.ReadTimeout(f"timeout talking to {request.url} key={SECRETS['VLLM_API_KEY']}")
            return httpx.Response(503)
        return ok()


def pg_factory(fail: bool):
    @contextmanager
    def connect(dsn, connect_timeout):
        if fail:
            raise RuntimeError(f"could not connect: {dsn}")  # message carries the password
        class Conn:
            def execute(self, sql):
                assert sql == "SELECT 1"
        yield Conn()
    return connect


@pytest.fixture
def env(monkeypatch):
    for key in list(ENV) + ["LANGFUSE_HOST"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def run(tmp_path, capsys, fail=None):
    services = Services(fail=None if fail == "postgres" else fail)
    report = tmp_path / "report.jsonl"
    code = healthcheck.main(
        ["--env-file", str(tmp_path / "absent.env"), "--report", str(report)],
        transport=httpx.MockTransport(services),
        pg_connect=pg_factory(fail == "postgres"),
    )
    out = capsys.readouterr()
    return code, out, report.read_text(encoding="utf-8"), services


def assert_no_secrets(*texts):
    blob = "".join(texts)
    for value in SECRETS.values():
        assert value not in blob


def test_all_pass(env, tmp_path, capsys):
    code, out, report, _ = run(tmp_path, capsys)
    assert code == 0
    lines = [l for l in out.out.splitlines() if l.startswith(("PASS", "FAIL"))]
    assert [l.split()[1] for l in lines] == list(healthcheck.CHECKS)
    assert all(l.startswith("PASS") for l in lines)
    assert "7/7 checks passed" in out.out
    assert json.loads(report)["all_pass"] is True


@pytest.mark.parametrize("check", healthcheck.CHECKS)
def test_single_failure(env, tmp_path, capsys, check):
    code, out, report, _ = run(tmp_path, capsys, fail=check)
    assert code == 1
    failed = [l.split()[1] for l in out.out.splitlines() if l.startswith("FAIL")]
    assert failed == [check]
    record = json.loads(report)
    assert record["all_pass"] is False
    assert {c["check"]: c["status"] for c in record["checks"]}[check] == "FAIL"
    assert_no_secrets(out.out, out.err, report)


def test_timeout_is_reported_as_exception_class_only(env, tmp_path, capsys):
    code, out, _, _ = run(tmp_path, capsys, fail="vllm_chat")
    chat_line = next(l for l in out.out.splitlines() if " vllm_chat " in l)
    assert chat_line.startswith("FAIL") and chat_line.rstrip().endswith("ReadTimeout")
    assert "vllm.test" not in out.out


def test_chat_request_uses_non_thinking_probe(env, tmp_path, capsys):
    _, _, report, services = run(tmp_path, capsys)
    chat = next(r for r in services.requests if r.url.path == "/v1/chat/completions")
    body = json.loads(chat.content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.7 and body["top_p"] == 0.8
    assert body["model"] == "qwen3-8b" and body["stream"] is True
    assert chat.headers["authorization"] == f"Bearer {SECRETS['VLLM_API_KEY']}"
    record = json.loads(report)
    assert isinstance(record["ttft_ms"], float) and isinstance(record["total_ms"], float)


def test_langfuse_trace_uses_basic_auth(env, tmp_path, capsys):
    _, _, _, services = run(tmp_path, capsys)
    ingest = next(r for r in services.requests if r.url.path == "/api/public/ingestion")
    assert ingest.headers["authorization"].startswith("Basic ")
    assert json.loads(ingest.content)["batch"][0]["type"] == "trace-create"


def test_no_secrets_in_any_output_on_success(env, tmp_path, capsys):
    _, out, report, _ = run(tmp_path, capsys)
    assert_no_secrets(out.out, out.err, report)
    for host in ("vllm.test", "db.test", "qdrant.test", "langfuse.test"):
        assert host not in out.out + report


def test_env_file_is_read_but_environment_wins(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("QDRANT_URL=http://from-file:6333\nAPP_ENV=dev\n# comment\n", encoding="utf-8")
    monkeypatch.setenv("QDRANT_URL", "http://from-env:6333")
    cfg = healthcheck.settings(env_file)
    assert cfg["QDRANT_URL"] == "http://from-env:6333"
    assert cfg["APP_ENV"] == "dev"
