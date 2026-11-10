"""CI-mode wiring for the agent (p5 plan step 3). Tests and `scripts/eval_agent.py` (CI mode) only.

`McpHarness` serves the real P4 MCP app (auth, role matrix, strict arguments, envelope) on a free loopback
port in a background thread. Its tool context differs from production only in:
- the corpus: an in-memory Qdrant with the committed synthetic documents plus the P2 conflict fixtures
  (`synthetic-qr-validity` 0.1.0 and 0.2.0), embedded with the deterministic FakeEmbedder;
- the keys: test keys generated into a temp directory;
- the reranker: never built.
No production path imports this module.
"""

from __future__ import annotations

import secrets
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFLICT_DOC = "synthetic-qr-validity"
CONFLICT_FILES = {"0.1.0": "synthetic-conflict-v010.md", "0.2.0": "synthetic-conflict-v020.md"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_corpus(workdir: Path):
    """(store, embedder): the two committed synthetic documents plus two versions of one conflict document."""
    from qdrant_client import QdrantClient

    from app.rag import ingest as ing
    from app.rag.embedding import FakeEmbedder
    from app.rag.store import Store

    manifest = yaml.safe_load((ROOT / "knowledge_base/manifests/sources.yaml").read_text(encoding="utf-8"))
    real = {e["document_id"]: e for e in manifest["sources"]}
    entries = []
    for doc in ("synthetic-qris-mpm-service-spec", "synthetic-qris-troubleshooting-sop"):
        dest = workdir / real[doc]["local_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / real[doc]["local_path"], dest)
        entries.append(real[doc])
    sop = real["synthetic-qris-troubleshooting-sop"]
    for version, name in CONFLICT_FILES.items():
        rel = f"knowledge_base/synthetic/{CONFLICT_DOC}-{version}.md"
        shutil.copy(ROOT / "tests/fixtures/rag" / name, workdir / rel)
        entries.append({**sop, "document_id": CONFLICT_DOC, "document_version": version, "local_path": rel,
                        "checksum_sha256": None, "kind": "specification",
                        "title": "QR Validity Rules (SYNTHETIC — not an official ASPI/BI document)"})
    path = workdir / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": entries}, allow_unicode=True), encoding="utf-8")
    store, emb = Store(QdrantClient(":memory:"), "harness_kb"), FakeEmbedder()
    report = ing.ingest(path, workdir, store, emb)
    bad = [d for d in report.documents if d.status != "ingested"]
    if bad:
        raise RuntimeError(f"harness corpus failed to ingest: {bad}")
    return store, emb


class McpHarness:
    def __init__(self) -> None:
        self.secret = secrets.token_hex(24)
        self.port = free_port()
        self.url = ""
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> McpHarness:
        import uvicorn

        from app.mcp_server import config as mcp_config
        from app.mcp_server.server import build_app, build_server
        from app.mcp_server.tools import default_context
        from app.validators import keys

        self._tmp = tempfile.TemporaryDirectory(prefix="devguard-harness-")
        work = Path(self._tmp.name)
        ctx = default_context()
        keys.generate(work / "keys", ctx.signature_cases["keys"])
        ctx.keystore = keys.Keystore(work / "keys")
        store, emb = build_corpus(work / "kb")
        ctx.backends = lambda: (store, emb)
        ctx.reranker = None
        app = build_app(build_server(self.secret, ctx, port=self.port), port=self.port)
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                     access_log=False))
        self._thread = threading.Thread(target=self._server.run, name="mcp-harness", daemon=True)
        self._thread.start()
        deadline = time.time() + 30
        while not self._server.started:
            if time.time() > deadline or not self._thread.is_alive():
                raise RuntimeError("MCP harness did not start")
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}{mcp_config.mcp()['server']['path']}"
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> McpHarness:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
