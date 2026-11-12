"""API-side reader for the alert receiver's JSONL store (docs/p9-demo-ui-alerts/spec.md Behaviour §6; AC #4).

The file is a trust boundary: the receiver validated each record, but this side validates every line again with a
strict model, re-applies the permalink rule, and redacts title and body with the same rules as user input before
anything leaves the API. Lines that fail are skipped and counted, never returned, never fatal.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent import config
from app.agent.sanitize import redact_text

log = logging.getLogger("devguard.alerts")

SHORT = r"^[A-Za-z0-9_.:+ -]{1,40}$"
ID = r"^[A-Za-z0-9_.:-]{1,128}$"
STORE_ENV = "DEVGUARD_ALERT_STORE"                  # tests only: a temp file instead of configs/agent.yaml store_path


class AlertRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=ID)
    received_at: str = Field(max_length=40)
    timestamp: str | None = Field(None, pattern=SHORT)
    monitor_id: str = Field(pattern=ID)
    severity: Literal["UNKNOWN", "OK", "WARNING", "ALERT", "NO_DATA", "PAUSED"]
    title: str = Field(max_length=config.agent()["alerts"]["title_max_chars"])
    body: str = Field(max_length=config.agent()["alerts"]["body_max_chars"])
    from_timestamp: str | None = Field(None, pattern=SHORT)
    to_timestamp: str | None = Field(None, pattern=SHORT)
    alert_timestamp: str | None = Field(None, pattern=SHORT)
    window: str | None = Field(None, pattern=SHORT)
    view: str | None = Field(None, pattern=SHORT)
    permalink: str | None = Field(None, max_length=500)


def store_path() -> Path:
    override = os.environ.get(STORE_ENV)
    return Path(override) if override else config.ROOT / config.agent()["alerts"]["store_path"]


def read_alerts(path: Path | None = None, limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """(newest-first alerts, number of lines skipped). A missing file is ([], 0)."""
    cfg = config.agent()["alerts"]
    path = path or store_path()
    limit = limit or cfg["list_limit"]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, IsADirectoryError):
        return [], 0
    out: list[dict[str, Any]] = []
    skipped = 0
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            rec = AlertRecord.model_validate(json.loads(line)).model_dump()
        except (ValueError, ValidationError, TypeError):
            skipped += 1
            continue
        if rec["permalink"] and not (rec["permalink"].startswith(cfg["langfuse_base_url"]) and " " not in rec["permalink"]):
            rec["permalink"] = None
        rec["title"], _ = redact_text(rec["title"])
        rec["body"], _ = redact_text(rec["body"])
        if len(out) < limit:
            out.append(rec)
    if skipped:
        log.warning("alert store: %d line(s) skipped (failed validation)", skipped)
    return out, skipped
