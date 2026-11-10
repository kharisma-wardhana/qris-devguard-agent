"""Knowledge-pipeline CLIs run at their real entry points (p2 spec AC #10; plan steps 5-8)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script: str, *args: str, env_extra: dict | None = None, cwd: Path = ROOT):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=300)


# --- fetch_models.py (step 5) ------------------------------------------------

def test_fetch_models_dry_run_prints_pinned_revision():
    result = run("fetch_models.py", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "BAAI/bge-m3" in result.stdout
    assert "5617a9f61b028005a4858fdac845db406aefb181" in result.stdout
    assert "nothing downloaded" in result.stdout


def test_fetch_models_dry_run_covers_the_reranker():
    """p3 spec AC #8: both models, both full revisions, no download."""
    result = run("fetch_models.py", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "BAAI/bge-reranker-v2-m3" in result.stdout
    assert "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e" in result.stdout
    assert "model.safetensors" in result.stdout
    assert "cached:" not in result.stdout


def test_fetch_models_without_embed_stack_fails_with_one_line(tmp_path):
    # Shadow the optional packages with modules that fail to import, whether or not the extra is installed.
    for name in ("truststore", "huggingface_hub"):
        (tmp_path / f"{name}.py").write_text("raise ImportError('shadowed for test')\n", encoding="utf-8")
    result = run("fetch_models.py", env_extra={"PYTHONPATH": str(tmp_path)})
    assert result.returncode == 2
    assert result.stderr.strip() == "error: embedding stack not installed: run `uv sync --extra embed`"
    assert "Traceback" not in result.stderr


# --- ingest.py (step 6) --------------------------------------------------------

TEST_ENV = {"DEVGUARD_TEST_BACKENDS": "1"}


def synthetic_corpus(tmp_path: Path) -> Path:
    import shutil

    import yaml

    real = yaml.safe_load((ROOT / "knowledge_base" / "manifests" / "sources.yaml").read_text(encoding="utf-8"))
    keep = [e for e in real["sources"] if e["document_id"].startswith("synthetic-qris-")]
    syn = tmp_path / "knowledge_base" / "synthetic"
    syn.mkdir(parents=True)
    for e in keep:
        shutil.copy(ROOT / e["local_path"], syn)
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump({"sources": keep}, allow_unicode=True), encoding="utf-8")
    return manifest


def ingest_args(tmp_path: Path, manifest: Path) -> list[str]:
    return ["--manifest", str(manifest), "--root", str(tmp_path), "--report-dir", str(tmp_path / "reports"),
            "--test-backends", "--qdrant-path", str(tmp_path / "qdrant")]


def test_ingest_cli_with_test_backends(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    first = run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV)
    assert first.returncode == 0, first.stdout + first.stderr
    lines = [l for l in first.stdout.splitlines() if l.startswith(("ingested", "unchanged"))]
    assert len(lines) == 2 and all(l.startswith("ingested") for l in lines)
    assert "truncated=n/a" in first.stdout              # fake embedder has no tokenizer
    second = run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV)
    assert second.returncode == 0
    assert sum(l.startswith("unchanged") for l in second.stdout.splitlines()) == 2
    assert len(list((tmp_path / "reports").glob("ingest-*.json"))) >= 1


def test_ingest_cli_refuses_test_backends_without_env(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    env = {"DEVGUARD_TEST_BACKENDS": ""}
    result = run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=env)
    assert result.returncode != 0
    assert "for tests only" in result.stderr


def test_ingest_cli_missing_manifest_is_one_line(tmp_path):
    result = run("ingest.py", "--manifest", str(tmp_path / "absent.yaml"), env_extra=TEST_ENV)
    assert result.returncode == 1
    assert result.stderr.strip().startswith("error: manifest not found")
    assert "Traceback" not in result.stderr


def test_ingest_cli_missing_source_file_fails(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    next((tmp_path / "knowledge_base" / "synthetic").glob("*.md")).unlink()
    result = run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV)
    assert result.returncode == 1
    assert "failed" in result.stdout and "file not found" in result.stdout
    assert "Traceback" not in result.stderr


# --- search.py (step 7) --------------------------------------------------------

def test_search_cli_after_ingest(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    assert run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV).returncode == 0
    backend = ["--test-backends", "--qdrant-path", str(tmp_path / "qdrant")]
    result = run("search.py", "4094700 duplicate partnerReferenceNo", "--limit", "5", *backend, env_extra=TEST_ENV)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert 1 <= len(lines) <= 5 and lines[0].split()[1] == "CIT-000001"
    as_json = run("search.py", "X-TIMESTAMP", "--json", "--limit", "2", *backend, env_extra=TEST_ENV)
    assert as_json.returncode == 0
    import json
    first = json.loads(as_json.stdout.splitlines()[0])
    assert {"citation_id", "chunk_id", "document_version", "quote_or_excerpt", "untrusted"} <= set(first)


def test_search_cli_missing_query_shows_usage():
    result = run("search.py")
    assert result.returncode == 2 and "usage:" in result.stderr


def test_search_cli_without_collection_is_one_line(tmp_path):
    backend = ["--test-backends", "--qdrant-path", str(tmp_path / "empty")]
    result = run("search.py", "anything", *backend, env_extra=TEST_ENV)
    assert result.returncode == 1
    assert result.stderr.strip().startswith("error: collection") and "Traceback" not in result.stderr


# --- eval_retrieval.py (step 8) ------------------------------------------------

SAMPLE_GOLDEN = ROOT / "tests" / "fixtures" / "rag" / "golden_sample.yaml"


def test_eval_cli_on_sample(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    assert run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV).returncode == 0
    result = run("eval_retrieval.py", "--golden", str(SAMPLE_GOLDEN), "--report-dir", str(tmp_path / "reports"),
                 "--test-backends", "--qdrant-path", str(tmp_path / "qdrant"), env_extra=TEST_ENV)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = {line.split()[0] for line in result.stdout.splitlines()}
    assert {"hybrid", "dense", "sparse"} <= rows
    assert "queries: 2" in result.stdout
    report = next((tmp_path / "reports").glob("retrieval-*.json")).read_text(encoding="utf-8")
    assert "first_hit_rank" in report and "X-TIMESTAMP" not in report


def test_eval_cli_rejects_gold_section_missing_from_corpus(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    assert run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV).returncode == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text(SAMPLE_GOLDEN.read_text(encoding="utf-8").replace("2.response-codes", "9.nowhere"), encoding="utf-8")
    result = run("eval_retrieval.py", "--golden", str(bad), "--report-dir", str(tmp_path / "reports"),
                 "--test-backends", "--qdrant-path", str(tmp_path / "qdrant"), env_extra=TEST_ENV)
    assert result.returncode == 1
    assert "gold sections not in the corpus: s01:synthetic-qris-mpm-service-spec#9.nowhere" in result.stderr


# --- search.py --rerank (p3 step 5) ------------------------------------------

def test_search_cli_rerank_shows_both_ranks(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    assert run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV).returncode == 0
    backend = ["--test-backends", "--qdrant-path", str(tmp_path / "qdrant")]
    result = run("search.py", "X-SIGNATURE HMAC_SHA512 minify body", "--rerank", "--limit", "5", *backend,
                 env_extra=TEST_ENV)
    assert result.returncode == 0, result.stderr
    rows = [l for l in result.stdout.splitlines() if "(fused" in l]
    assert len(rows) == 5
    assert [int(l.split()[0]) for l in rows] == [1, 2, 3, 4, 5]
    assert result.stdout.strip().splitlines()[-1].startswith("reranked 20 candidates in")   # 28 chunks -> pool of 20


def test_search_cli_rerank_without_collection_is_one_line(tmp_path):
    backend = ["--test-backends", "--qdrant-path", str(tmp_path / "empty")]
    result = run("search.py", "anything", "--rerank", *backend, env_extra=TEST_ENV)
    assert result.returncode == 1
    assert result.stderr.strip().startswith("error: collection") and "Traceback" not in result.stderr



# --- eval before/after (p3 step 6) -------------------------------------------

SUBSET_GOLDEN = ROOT / "tests" / "fixtures" / "rag" / "golden_sample_subsets.yaml"


def test_eval_cli_before_after_on_the_same_pool(tmp_path):
    import json

    manifest = synthetic_corpus(tmp_path)
    assert run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV).returncode == 0
    result = run("eval_retrieval.py", "--golden", str(SUBSET_GOLDEN), "--report-dir", str(tmp_path / "reports"),
                 "--test-backends", "--qdrant-path", str(tmp_path / "qdrant"), env_extra=TEST_ENV)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert {"hybrid", "dense", "sparse"} <= {l.split()[0] for l in out.splitlines() if l.strip()}   # P2 table kept
    assert "before/after on the same hybrid top-20 pool" in out and "FAKE" in out
    rows = {l.split()[0]: l for l in out.splitlines() if l.split() and l.split()[0] in ("original", "hard", "all")}
    assert set(rows) == {"original", "hard", "all"}
    assert rows["original"].split()[1] == "2" and rows["hard"].split()[1] == "1" and rows["all"].split()[1] == "3"
    assert "rerank latency per query: median" in out and "pairs over max_length: n/a (fake)" in out
    report = json.loads(next((tmp_path / "reports").glob("retrieval-*.json")).read_text(encoding="utf-8"))
    assert all(r["is_permutation"] for r in report["rerank_per_query"])
    for subset in ("original", "hard", "all"):           # reranking can't change the candidate pool
        assert 0.0 <= report["before_after"][subset]["candidate_recall@20"] <= 1.0
    assert "X-SIGNATURE" not in json.dumps(report)        # IDs and numbers only, no chunk text


def test_eval_cli_no_rerank_keeps_p2_output(tmp_path):
    manifest = synthetic_corpus(tmp_path)
    assert run("ingest.py", *ingest_args(tmp_path, manifest), env_extra=TEST_ENV).returncode == 0
    result = run("eval_retrieval.py", "--golden", str(SUBSET_GOLDEN), "--no-rerank", "--report-dir",
                 str(tmp_path / "reports"), "--test-backends", "--qdrant-path", str(tmp_path / "qdrant"),
                 env_extra=TEST_ENV)
    assert result.returncode == 0
    assert "before/after" not in result.stdout


def test_eval_cli_malformed_golden_is_one_line(tmp_path):
    """Regression (found in p3 step 1): a malformed golden file gave a traceback."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("dataset_version: x\n  - id: broken\n", encoding="utf-8")
    result = run("eval_retrieval.py", "--golden", str(bad), env_extra=TEST_ENV)
    assert result.returncode == 1
    assert result.stderr.strip().startswith("error: golden set unreadable:")
    assert "Traceback" not in result.stderr
