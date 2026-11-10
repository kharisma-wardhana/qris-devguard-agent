"""Loads configs/agent.yaml plus the values it borrows from model.yaml and security.yaml (p5 spec)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "configs" / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def agent() -> dict[str, Any]:
    return _load("agent.yaml")


@lru_cache(maxsize=1)
def security() -> dict[str, Any]:
    return _load("security.yaml")


@lru_cache(maxsize=1)
def served_model_name() -> str:
    return str(_load("model.yaml")["model"]["served_name"])


def prohibited_objects() -> list[str]:
    """agent.yaml prohibited objects plus security.yaml prohibited_actions (underscores as spaces)."""
    extra = [a.replace("_", " ") for a in security()["prohibited_actions"]]
    return sorted({t.lower() for t in agent()["classify"]["prohibited_objects"] + extra})
