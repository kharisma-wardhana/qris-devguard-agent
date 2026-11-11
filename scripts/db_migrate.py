"""Create or upgrade the P6 database schema on the local PostgreSQL.

Usage:
  podman compose up -d postgres
  uv run python scripts/db_migrate.py

Applies app/db/migrations/NNNN_*.sql once each (recorded in schema_migrations), then LangGraph's own
checkpoint tables (AsyncPostgresSaver.setup). Safe to re-run. Reads DATABASE_URL from the environment or
.env; never prints it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    import psycopg

    from app.db import migrate
    from app.db.connect import DatabaseNotConfigured, conninfo, use_selector_loop

    sys.stdout.reconfigure(encoding="utf-8")
    use_selector_loop()
    try:
        info = conninfo()
        with psycopg.connect(info, autocommit=True) as conn:
            applied, skipped = migrate.apply(conn)
        asyncio.run(migrate.setup_checkpointer(info))
    except DatabaseNotConfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"error: database not reachable or migration failed ({type(exc).__name__}); "
              "start it with `podman compose up -d postgres`", file=sys.stderr)
        return 1
    print(f"applied {len(applied)} ({', '.join(applied) or '-'}), already applied {len(skipped)}; "
          "checkpoint tables ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
