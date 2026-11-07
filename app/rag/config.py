"""Loads configs/retrieval.yaml and the citation limits from configs/security.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_CONFIG = ROOT / "configs" / "retrieval.yaml"
SECURITY_CONFIG = ROOT / "configs" / "security.yaml"


@lru_cache(maxsize=1)
def retrieval() -> dict[str, Any]:
    return yaml.safe_load(RETRIEVAL_CONFIG.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def excerpt_limits() -> dict[str, int]:
    security = yaml.safe_load(SECURITY_CONFIG.read_text(encoding="utf-8"))
    return dict(security["citations"]["max_excerpt_chars"])


def excerpt_limit(permission_basis: str) -> int:
    limits = excerpt_limits()
    if permission_basis not in limits:
        raise ValueError(f"no excerpt limit for permission basis {permission_basis!r}")
    return limits[permission_basis]


def qdrant_url() -> str:
    cfg = retrieval()["qdrant"]
    return os.environ.get(cfg["url_env"]) or cfg["default_url"]
