"""Eval-time code for the final evaluation (docs/p9-final-evaluation-packaging/spec.md).

Golden-answer checks, the LLM judge, the benchmark parser, the release gate and the deployment manifest.
This package is not part of the product: nothing else under `app/` imports it, and the `anthropic` SDK
(optional extra `judge`) is imported only inside the judge's client constructor.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs" / "evaluation.yaml"
THRESHOLDS = ROOT / "configs" / "eval_thresholds.yaml"


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    """configs/evaluation.yaml, parsed once."""
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def thresholds() -> dict[str, Any]:
    """configs/eval_thresholds.yaml (read-only here; never written by any evaluation code)."""
    return yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))
