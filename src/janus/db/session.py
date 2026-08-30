"""Postgres connections and migration runner."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from janus.config import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"


@contextmanager
def connect(autocommit: bool = False):
    """Full-privilege connection. Never used for generated SQL."""
    with psycopg.connect(get_settings().dsn, autocommit=autocommit) as conn:
        try:
            register_vector(conn)
        except Exception:
            pass  # extension not yet created on a fresh DB
        yield conn


@contextmanager
def connect_readonly(statement_timeout_ms: int = 10_000):
    """SELECT-only role used EXCLUSIVELY by the generated-SQL executor (SPEC §4.7)."""
    with psycopg.connect(get_settings().ro_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
            cur.execute("SET default_transaction_read_only = on")
        yield conn


def run_migrations(only: list[str] | None = None) -> list[str]:
    applied = []
    files = sorted(MIGRATIONS.glob("*.sql"))
    if only:
        files = [f for f in files if f.name in only or f.stem in only]
    for f in files:
        with connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f.read_text())
        applied.append(f.name)
    return applied
