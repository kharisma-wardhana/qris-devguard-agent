"""Delete stored data past its retention period (p8 spec Behaviour §6; AC #26, #27; TRD §19.3).

Usage:
  uv run python scripts/purge_retention.py --dry-run   # counts per table, deletes nothing
  uv run python scripts/purge_retention.py             # deletes

Periods come from configs/agent.yaml `retention` (checkpoints 30 days; tickets, approvals and threads 90 days)
and memory rows are removed once soft-deleted or expired. Runs as the owner role (DATABASE_URL): the runtime
roles cannot delete these rows. Prints counts only; never the DSN or row content. Nothing schedules this.
The audit table has its own command (scripts/purge_audit.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    import psycopg

    from app.db import retention
    from app.db.connect import DatabaseNotConfigured, conninfo

    parser = argparse.ArgumentParser(description="Delete rows past their retention period (owner role).")
    parser.add_argument("--dry-run", action="store_true", help="print the counts and delete nothing")
    args = parser.parse_args(argv)
    p = retention.periods()
    try:
        with psycopg.connect(conninfo(), autocommit=True) as conn:
            counts = retention.purge(conn, dry_run=args.dry_run, **p)
    except DatabaseNotConfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"error: database not reachable or purge failed ({type(exc).__name__}); nothing was deleted",
              file=sys.stderr)
        return 1
    verb = "would delete" if args.dry_run else "deleted"
    print(f"retention ({'dry run' if args.dry_run else 'purge'}): checkpoints after {p['checkpoint_days']} days, "
          f"tickets/approvals/threads after {p['approval_days']} days, memory when deleted or expired")
    for table, n in counts.items():
        print(f"{verb} {n} rows from {table}")
    print(f"{verb} {sum(counts.values())} rows in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
