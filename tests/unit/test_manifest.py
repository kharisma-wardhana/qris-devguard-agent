"""Deployment manifest checks (p9-final spec AC #27). Identifier test strings are built from parts (CLAUDE.md)."""

import copy
from datetime import datetime, timezone

import pytest
import yaml

from app.evaluation import manifest

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def complete():
    d = manifest.template()
    d["gpu"].update(sku="RTX PRO 4000", driver="580.95.05", cuda="13.0")
    d["model"].update(id="Qwen/Qwen3-8B-FP8", revision="220b46e", quantization="fp8", max_model_len=16384)
    d["vllm"].update(version="0.32.0", image="vllm/vllm-openai:v0.32.0")
    d["lockfile"]["sha256"] = "a" * 64
    d["images"] = {"postgres": {"image": "docker.io/library/postgres:17.11", "digest": "sha256:" + "b" * 64},
                   "clickhouse": {"image": "docker.io/clickhouse/clickhouse-server:26.3.62.3", "digest": "sha256:" + "c" * 64}}
    d["fresh_clone"].update(date="2026-11-18T10:00:00+00:00", commit="abc1234", setup_report_sha256="d" * 64,
                            healthcheck_all_pass=True, checks_passed=7)
    d["fresh_pod"].update(date="2026-11-18", followed_readme=True)
    return d


def test_complete_manifest_has_no_problems():
    assert manifest.problems(complete(), NOW) == []


@pytest.mark.parametrize("section,key", [(s, k) for s, keys in manifest.REQUIRED.items() for k in keys])
def test_every_required_field_is_checked(section, key):
    d = complete()
    d[section][key] = None
    assert f"{section}.{key} is empty" in manifest.problems(d, NOW)


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d["images"]["postgres"].update(digest=None), "images.postgres has no digest"),
    (lambda d: d["fresh_clone"].update(healthcheck_all_pass=False), "fresh-clone health check was not 7/7 PASS"),
    (lambda d: d["fresh_clone"].update(checks_passed=6), "fresh-clone health check was not 7/7 PASS"),
    (lambda d: d["fresh_pod"].update(followed_readme=False), "fresh pod needed steps outside the README"),
    (lambda d: d.update(unlisted_steps=["had to install git-lfs"]), "1 unlisted step(s) were needed; fix the README"),
    (lambda d: d["fresh_clone"].update(mode="synthetic-only"),
     "setup ran --synthetic-only, which does not reproduce the measured results"),
])
def test_rebuild_problems(mutate, expected):
    d = complete()
    mutate(d)
    assert expected in manifest.problems(d, NOW)


def test_identifiers_and_secrets_are_refused():
    ip = ".".join(["203", "0", "113", "7"])
    d = complete()
    d["gpu"]["data_center"] = ip
    assert "contains an IPv4 address" in manifest.problems(d, NOW)
    d = complete()
    d["gpu"]["data_center"] = "@".join(["abc123-64410e1f", ".".join(["ssh", "runpod", "io"])])
    assert "contains a pod address or RunPod host" in manifest.problems(d, NOW)
    d = complete()
    d["vllm"]["image"] = "password: hunter2hunter2"
    assert "contains a secret-shaped value" in manifest.problems(d, NOW)


def test_image_tag_that_looks_like_an_ip_is_fine():
    assert manifest.identifier_problems(complete()) == []


def test_stale_fresh_clone_is_the_only_problem_then_gate_reads_stale(tmp_path):
    d = complete()
    path = tmp_path / "m.yaml"
    path.write_text(manifest.dump(d), encoding="utf-8")
    assert manifest.evaluate(path, NOW) == (True, [], "2026-11-18T10:00:00+00:00")
    assert manifest.evaluate(path, datetime(2026, 12, 10, tzinfo=timezone.utc))[1] == ["stale"]


def test_merge_setup_report_and_fill_local():
    rep = {"finished_at": "2026-11-18T10:00:00+00:00", "commit": "abc1234", "fresh_clone": True, "mode": "full",
           "healthcheck": {"all_pass": True, "checks_passed": 7}}
    d = manifest.merge_setup_report(None, rep, b"{}")
    assert d["fresh_clone"]["checks_passed"] == 7 and len(d["fresh_clone"]["setup_report_sha256"]) == 64
    local = {"model": {"id": "m", "revision": "r", "served_name": "s", "quantization": "fp8", "max_model_len": 1},
             "vllm_version": "0.32.0", "lockfile_sha256": "e" * 64, "images": {"x": {"image": "i", "digest": None}}}
    d2 = manifest.fill_local(copy.deepcopy(d), local)
    assert d2["fresh_clone"] == d["fresh_clone"] and d2["lockfile"]["sha256"] == "e" * 64 and d2["vllm"]["version"] == "0.32.0"


def test_local_fields_reads_config_and_lockfile(monkeypatch):
    monkeypatch.setattr(manifest, "image_digest", lambda image: None)
    local = manifest.local_fields()
    assert local["model"]["quantization"] == "fp8" and len(local["lockfile_sha256"]) == 64
    assert "postgres" in local["images"]


def test_dump_is_yaml_with_header():
    text = manifest.dump(complete())
    assert text.startswith("# Deployment manifest") and yaml.safe_load(text)["gpu"]["sku"] == "RTX PRO 4000"
