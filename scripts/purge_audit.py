"""Delete MCP audit rows older than the retention period (p7 spec Behaviour §1, AC #6; TRD §19.3).

Usage:
  uv run python scripts/purge_audit.py              # audit.retention_days from configs/security.yaml (90)
  uv run python scripts/purge_audit.py --days 120   # a longer window; never shorter than one day

Runs as the owner role (DATABASE_URL): the runtime audit role can only insert. Prints the number of rows
deleted; never prints the DSN. Nothing schedules this: it is a developer command.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    import psycopg

    from app.db import audit
    from app.db.connect import DatabaseNotConfigured, conninfo
    from app.security import security

    default = int(security()["audit"]["retention_days"])
    parser = argparse.ArgumentParser(description="Delete audit rows older than the retention period.")
    parser.add_argument("--days", type=int, default=default, help=f"retention in days (default {default})")
    args = parser.parse_args(argv)
    if args.days < 1:
        print("error: --days must be at least 1", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(conninfo(), autocommit=True) as conn:
            deleted = audit.purge(conn, args.days)
    except DatabaseNotConfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"error: database not reachable or purge failed ({type(exc).__name__}); "
              "start it with `podman compose up -d postgres`", file=sys.stderr)
        return 1
    print(f"deleted {deleted} audit rows older than {args.days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
