"""Fixture: a skipped test (like a db test without PostgreSQL)."""

import pytest


def test_skips():
    pytest.skip("no reachable PostgreSQL")
