"""Postgres: the normalized tables, with real types and foreign keys.

Everything is created IF NOT EXISTS and every write is an upsert on a natural
key, so applying the schema and loading are both safe to re-run. The DDL lives
in schema/postgres.sql rather than being generated, because the Postgres
target is the one where the exact types, constraints and comments are worth
reading and editing by hand.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .. import tables as T
from ..parse import Dataset
from ..store.postgres import connect
from . import Sink, SinkResult
from .coltypes import JSON, classify

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "postgres.sql"


def upsert_sql(table: T.Table) -> str:
    columns = ", ".join(f'"{c}"' for c in table.columns)
    placeholders = ", ".join(["%s"] * len(table.columns))
    key = ", ".join(f'"{c}"' for c in table.key)
    updates = table.value_columns()
    if not updates:
        action = "DO NOTHING"
    else:
        sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in updates)
        action = f"DO UPDATE SET {sets}, updated_at = now()"
    return (f'INSERT INTO "{table.name}" ({columns}) VALUES ({placeholders}) '
            f"ON CONFLICT ({key}) {action}")


class PostgresSink(Sink):
    name = "postgres"
    requires = ("psycopg", "psycopg[binary]")
    extra = "postgres"
    help = "normalized tables in Postgres 15+ (docker-compose.yml ships one)"

    def write(self, data: Dataset) -> SinkResult:
        from psycopg.types.json import Jsonb

        def encode(value: Any, column: str, table: T.Table) -> Any:
            # Jsonb(None) would store a JSON `null`; SQL NULL is what is meant.
            if value is not None and classify(table, column) == JSON:
                return Jsonb(value)
            return value

        conn = connect(self.cfg.dsn)
        total = 0
        try:
            conn.execute(SCHEMA_FILE.read_text())
            conn.commit()
            # Derived tables are rebuilt from scratch each parse. Truncating
            # them keeps a narrower --seasons run from leaving behind rows for
            # seasons that are no longer in the dataset.
            with conn.cursor() as cur:
                for table in T.ALL_TABLES:
                    if table.derived:
                        cur.execute(f'TRUNCATE TABLE "{table.name}"')
                # Tables are ordered parents-first in ALL_TABLES, so foreign
                # keys are satisfied by inserting straight down the list.
                for table, rows in data:
                    if not rows:
                        continue
                    cur.executemany(upsert_sql(table), [
                        [encode(row.get(c), c, table) for c in table.columns]
                        for row in rows])
                    total += len(rows)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return SinkResult("postgres", _describe(self.cfg), total)


def _describe(cfg) -> str:
    """Never echo the password back, even into a local log."""
    if cfg.pg_dsn:
        return "the configured Postgres connection string"
    return f"postgres://{cfg.pg_host}:{cfg.pg_port}/{cfg.pg_db}"
