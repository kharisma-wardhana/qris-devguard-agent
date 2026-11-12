"""Send one signed synthetic Langfuse `monitor-alert` to the alert receiver (docs/p9-demo-ui-alerts/spec.md AC #20).

Usage:
  uv run python scripts/send_test_alert.py --url http://127.0.0.1:8090/langfuse/alerts      # a receiver on loopback
  uv run python scripts/send_test_alert.py --via-container qris-devguard-agent_langfuse-worker_1
      # from inside the Langfuse worker container to http://alert-receiver/langfuse/alerts: the path Langfuse uses

The payload is synthetic and says so. The signing secret is LANGFUSE_ALERT_WEBHOOK_SECRET from the environment or
.env; it is never printed or passed on a command line. In --via-container mode the HMAC of the synthetic body (not
the secret) is passed to `podman exec wget` as a header argument.
Prints the alert id and the receiver's answer; exit 0 on HTTP 200, else 1.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTAINER_URL = "http://alert-receiver/langfuse/alerts"


def load_secret(env_file: Path) -> str | None:
    from app.alerts.receiver import PLACEHOLDERS, SECRET_ENV
    value = os.environ.get(SECRET_ENV, "").strip()
    if not value and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, v = line.partition("=")
            if sep and key.strip() == SECRET_ENV:
                value = v.strip()
    return None if value in PLACEHOLDERS else value


def payload(severity: str) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {"id": f"synthetic-{secrets.token_hex(6)}", "timestamp": now, "type": "monitor-alert", "apiVersion": "v1",
            "payload": {"monitorId": "synthetic-test-monitor", "projectId": "devguard",
                        "permalink": "http://localhost:3000/project/devguard",
                        "message": {"title": "SYNTHETIC test alert (scripts/send_test_alert.py)",
                                    "body": "Synthetic delivery check for the alert receiver; no monitor fired."},
                        "severity": severity, "timestamp": now, "fromTimestamp": now, "toTimestamp": now,
                        "view": "observations", "filters": [], "window": "5m"}}


def main(argv: list[str] | None = None) -> int:
    from app.alerts.receiver import SECRET_ENV, SEVERITIES, sign

    parser = argparse.ArgumentParser(description="Send one signed synthetic alert to the receiver.")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--url", help="a loopback receiver URL, e.g. http://127.0.0.1:8090/langfuse/alerts")
    where.add_argument("--via-container", metavar="NAME", help="post from inside this compose container")
    parser.add_argument("--severity", choices=SEVERITIES, default="ALERT")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.url and not args.url.startswith("http://127.0.0.1:"):
        print("error: --url must be a loopback URL (http://127.0.0.1:<port>/...)", file=sys.stderr)
        return 1
    secret = load_secret(args.env_file)
    if not secret:
        print(f"error: {SECRET_ENV} is not set (environment or .env)", file=sys.stderr)
        return 1
    doc = payload(args.severity)
    body = json.dumps(doc, separators=(",", ":"))
    ts = str(int(time.time()))
    signature = f"t={ts},v1={sign(secret, ts, body.encode('utf-8'))}"
    print(f"alert id: {doc['id']}  severity: {args.severity}")
    if args.url:
        import httpx
        try:
            r = httpx.post(args.url, content=body.encode("utf-8"), timeout=10, trust_env=False,
                           headers={"Content-Type": "application/json", "x-langfuse-signature": signature})
        except httpx.HTTPError as exc:
            print(f"error: receiver not reachable ({type(exc).__name__})", file=sys.stderr)
            return 1
        print(f"receiver: HTTP {r.status_code} {r.text[:200]}")
        return 0 if r.status_code == 200 else 1
    cmd = ["podman", "exec", args.via_container, "wget", "-q", "-O-", "-T", "10",
           "--header=Content-Type: application/json", f"--header=x-langfuse-signature: {signature}",
           f"--post-data={body}", CONTAINER_URL]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    answer = (r.stdout or r.stderr).strip().splitlines()
    print(f"receiver (from {args.via_container} to {CONTAINER_URL}): {answer[-1][:200] if answer else '(no output)'}")
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
