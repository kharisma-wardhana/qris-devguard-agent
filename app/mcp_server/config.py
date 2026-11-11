"""Loads configs/mcp.yaml and the role/tool matrix and limits from configs/security.yaml (p4 spec)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
MCP_CONFIG = ROOT / "configs" / "mcp.yaml"
SECURITY_CONFIG = ROOT / "configs" / "security.yaml"

TOOL_NAMES = ("search_qris_spec", "validate_request_payload", "validate_test_signature", "lookup_response_code",
              "create_support_ticket")      # p6: the one write tool, approval-checked
READ_ONLY_TOOLS = TOOL_NAMES[:4]


@lru_cache(maxsize=1)
def mcp() -> dict[str, Any]:
    return yaml.safe_load(MCP_CONFIG.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def security() -> dict[str, Any]:
    return yaml.safe_load(SECURITY_CONFIG.read_text(encoding="utf-8"))


def roles() -> frozenset[str]:
    """Every role named anywhere in the tool matrix (the four P0 roles)."""
    return frozenset(r for t in security()["tools"].values() for r in t["roles"])


def tool_roles(tool: str) -> frozenset[str]:
    """Roles allowed to call `tool`. A tool missing from the matrix is allowed to nobody."""
    entry = security()["tools"].get(tool)
    return frozenset(entry["roles"]) if entry else frozenset()


def max_payload_bytes() -> int:
    return int(security()["limits"]["max_payload_bytes"])


def repo_path(rel: str) -> Path:
    return ROOT / Path(*rel.replace("\\", "/").split("/"))


def keys_dir() -> Path:
    v = mcp()["validators"]
    override = os.environ.get(v["keys_dir_env"])
    return Path(override) if override else repo_path(v["keys_dir"])
