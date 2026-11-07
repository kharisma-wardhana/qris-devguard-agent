#!/usr/bin/env bash
# Runs once, on first start of an empty PostgreSQL volume (docker-entrypoint-initdb.d).
# The `devguard` database and user come from POSTGRES_DB / POSTGRES_USER.
# This adds a separate `langfuse` user and database on the same server.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v lfpass="$LANGFUSE_DB_PASSWORD" <<'SQL'
CREATE ROLE langfuse LOGIN PASSWORD :'lfpass';
CREATE DATABASE langfuse OWNER langfuse;
SQL
