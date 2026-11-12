"""Langfuse alert webhook receiver (docs/p9-demo-ui-alerts/spec.md Behaviour §5, Data; AC #6-#12).

Standard library only: the compose service mounts this one file read-only into a stock python image, with no
build and no pip. Do not import from `app` here (tests/unit/test_alert_receiver.py checks it).

POST /langfuse/alerts, in this order:
  1. Content-Length over MAX_BODY_BYTES -> 413 before the body is read (chunked bodies -> 411).
  2. x-langfuse-signature `t=<ts>,v1=<hex>`: HMAC-SHA256 over "<ts>.<raw body>" with the webhook secret,
     constant-time compare -> 401 when missing, malformed or wrong.
  3. |now - t| over MAX_CLOCK_SKEW_SECONDS -> 401 (our replay window; Langfuse defines none).
  4. JSON shape: type monitor-alert, apiVersion v1, a known severity, required fields typed -> else 422.
  5. Only allowlisted fields are kept; title/body truncated; a permalink outside LANGFUSE_BASE_URL is dropped.
  6. Deduplicated by alert id; the newest MAX_RECORDS are kept.
  -> 200 {"stored": true|false}. Nothing else is ever done with an alert.

Logs: one line per request with outcome, alert id (valid alerts only), severity and size. Never the body, a
header value or the signature.

Environment: LANGFUSE_ALERT_WEBHOOK_SECRET (required), ALERT_STORE (default var/alerts/alerts.jsonl),
ALERT_PORT (default 8090), ALERT_BIND (default 127.0.0.1; "all" = every interface, for the compose network only).
The limits below must equal configs/agent.yaml `alerts` (tests/unit/test_alert_receiver.py pins them).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

SECRET_ENV = "LANGFUSE_ALERT_WEBHOOK_SECRET"
PATH = "/langfuse/alerts"
MAX_BODY_BYTES = 65536
MAX_CLOCK_SKEW_SECONDS = 600
MAX_RECORDS = 500
TITLE_MAX_CHARS = 200
BODY_MAX_CHARS = 1000
LANGFUSE_BASE_URL = "http://localhost:3000/"
SEVERITIES = ("UNKNOWN", "OK", "WARNING", "ALERT", "NO_DATA", "PAUSED")
PLACEHOLDERS = ("", "set-me", "replace-me")
SOCKET_TIMEOUT_SECONDS = 10

ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SHORT_RE = re.compile(r"^[A-Za-z0-9_.:+ -]{1,40}$")        # timestamps, window, view
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # keeps \t and \n

log = logging.getLogger("devguard.alert_receiver")


class Refused(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


# --- checks (pure) --------------------------------------------------------------------------------------------

def sign(secret: str, timestamp: str, raw: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()


def verify_signature(header: str | None, raw: bytes, secret: str, now: float) -> None:
    """Raises Refused(401) unless `header` signs `raw` with `secret` and its t= is within the skew window."""
    if not header or len(header) > 256:
        raise Refused(401, "signature_missing")
    parts = dict(p.strip().split("=", 1) for p in header.split(",") if "=" in p)
    ts, sig = parts.get("t", ""), parts.get("v1", "")
    if not ts.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", sig):
        raise Refused(401, "signature_malformed")
    if not hmac.compare_digest(bytes.fromhex(sig), bytes.fromhex(sign(secret, ts, raw))):
        raise Refused(401, "signature_invalid")
    t = int(ts)
    if t > 10**11:                                        # milliseconds (the unit is not documented)
        t //= 1000
    if abs(now - t) > MAX_CLOCK_SKEW_SECONDS:
        raise Refused(401, "signature_stale")


def _text(v: Any, limit: int) -> str:
    if not isinstance(v, str):
        raise Refused(422, "invalid_shape")
    return CONTROL_RE.sub("", v)[:limit]


def _short(v: Any, required: bool = False) -> str | None:
    if v is None and not required:
        return None
    if not isinstance(v, str) or not SHORT_RE.fullmatch(v):
        raise Refused(422, "invalid_shape")
    return v


def parse(raw: bytes, received_at: str) -> dict[str, Any]:
    """The stored record: allowlisted fields only. Raises Refused(422) on anything off-shape."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused(422, "invalid_json") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("payload"), dict):
        raise Refused(422, "invalid_shape")
    if doc.get("type") != "monitor-alert" or doc.get("apiVersion") != "v1":
        raise Refused(422, "unsupported_type")
    p = doc["payload"]
    if p.get("severity") not in SEVERITIES:
        raise Refused(422, "unknown_severity")
    alert_id, monitor_id = doc.get("id"), p.get("monitorId")
    if not isinstance(alert_id, str) or not ID_RE.fullmatch(alert_id):
        raise Refused(422, "invalid_shape")
    if not isinstance(monitor_id, str) or not ID_RE.fullmatch(monitor_id):
        raise Refused(422, "invalid_shape")
    msg = p.get("message")
    if not isinstance(msg, dict):
        raise Refused(422, "invalid_shape")
    link = p.get("permalink")
    keep_link = isinstance(link, str) and len(link) <= 500 and link.startswith(LANGFUSE_BASE_URL) \
        and not CONTROL_RE.search(link) and " " not in link
    return {"id": alert_id, "received_at": received_at, "timestamp": _short(doc.get("timestamp")),
            "monitor_id": monitor_id, "severity": p["severity"],
            "title": _text(msg.get("title"), TITLE_MAX_CHARS), "body": _text(msg.get("body"), BODY_MAX_CHARS),
            "from_timestamp": _short(p.get("fromTimestamp")), "to_timestamp": _short(p.get("toTimestamp")),
            "alert_timestamp": _short(p.get("timestamp")), "window": _short(p.get("window")),
            "view": _short(p.get("view")), "permalink": link if keep_link else None}


# --- store --------------------------------------------------------------------------------------------------

class Store:
    """One JSONL file, written by this process only. Single-threaded server, so no locking is needed here; a
    rewrite (cap) goes through a temp file and os.replace, so the API never reads a half-written file."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def _lines(self) -> list[str]:
        try:
            return [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except FileNotFoundError:
            return []

    def add(self, record: dict[str, Any]) -> bool:
        lines = self._lines()
        for ln in lines:
            try:
                if json.loads(ln).get("id") == record["id"]:
                    return False
            except ValueError:
                continue
        lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        if len(lines) > MAX_RECORDS:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".alerts-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines[-MAX_RECORDS:]) + "\n")
            os.replace(tmp, self.path)
        else:
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(lines[-1] + "\n")
        return True


# --- HTTP ---------------------------------------------------------------------------------------------------

def handler_for(store: Store, secret: str, clock=time.time) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "devguard-alert-receiver"
        sys_version = ""
        timeout = SOCKET_TIMEOUT_SECONDS

        def log_message(self, format: str, *args: Any) -> None:   # noqa: A002 - silence the default access log
            return

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def _other(self) -> None:
            path = self.path.split("?", 1)[0]
            self._reply(405 if path == PATH else 404, {"error": "method_not_allowed" if path == PATH else "not_found"})
            log.info("alert outcome=%s size=-", "method_not_allowed" if path == PATH else "not_found")

        do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _other   # noqa: N815

        def do_POST(self) -> None:   # noqa: N802
            size: Any = "-"
            try:
                if self.path.split("?", 1)[0] != PATH:
                    raise Refused(404, "not_found")
                if self.headers.get("Transfer-Encoding"):
                    raise Refused(411, "length_required")
                length = self.headers.get("Content-Length", "")
                if not length.isdigit():
                    raise Refused(411, "length_required")
                size = int(length)
                if size > MAX_BODY_BYTES:
                    raise Refused(413, "payload_too_large")
                raw = self.rfile.read(size)
                verify_signature(self.headers.get("x-langfuse-signature"), raw, secret, clock())
                record = parse(raw, datetime.now(timezone.utc).isoformat(timespec="seconds"))
                stored = store.add(record)
            except Refused as r:
                log.info("alert outcome=%s size=%s", r.code, size)
                self._reply(r.status, {"error": r.code})
                return
            except Exception as exc:  # noqa: BLE001 - never a stack trace or a value in the response
                log.error("alert outcome=internal_error type=%s size=%s", type(exc).__name__, size)
                self._reply(500, {"error": "internal_error"})
                return
            log.info("alert outcome=%s id=%s severity=%s size=%s", "stored" if stored else "duplicate",
                     record["id"], record["severity"], size)
            self._reply(200, {"stored": stored})

    return Handler


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    secret = os.environ.get(SECRET_ENV, "").strip()
    if secret in PLACEHOLDERS:
        print(f"error: {SECRET_ENV} is not set; copy the webhook secret from Langfuse into .env", file=sys.stderr)
        return 2
    bind = os.environ.get("ALERT_BIND", "127.0.0.1")
    host = "" if bind == "all" else bind
    port = int(os.environ.get("ALERT_PORT", "8090"))
    store = Store(Path(os.environ.get("ALERT_STORE", "var/alerts/alerts.jsonl")))
    server = HTTPServer((host, port), handler_for(store, secret))
    log.info("alert receiver listening bind=%s port=%d path=%s", bind, port, PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
