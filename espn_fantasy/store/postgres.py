"""Postgres raw store.

Uses NULLS NOT DISTINCT on the unique index so season-level rows, where
scoring_period IS NULL, collide and upsert instead of duplicating. That
requires Postgres 15 or newer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..errors import StoreError
from . import RawRow, RawStore, Unit

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_responses (
    id              bigserial PRIMARY KEY,
    season          integer     NOT NULL,
    view            text        NOT NULL,
    scoring_period  integer,
    url             text        NOT NULL,
    http_status     integer     NOT NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    payload         jsonb       NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS raw_responses_key_uidx
    ON raw_responses (season, view, scoring_period) NULLS NOT DISTINCT;

CREATE INDEX IF NOT EXISTS raw_responses_season_idx ON raw_responses (season);

CREATE TABLE IF NOT EXISTS fetch_log (
    id              bigserial PRIMARY KEY,
    season          integer     NOT NULL,
    view            text        NOT NULL,
    scoring_period  integer,
    status          text        NOT NULL CHECK (status IN ('ok', 'error', 'empty')),
    http_status     integer,
    attempts        integer     NOT NULL DEFAULT 1,
    last_error      text,
    fetched_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS fetch_log_key_uidx
    ON fetch_log (season, view, scoring_period) NULLS NOT DISTINCT;
"""

MIN_SERVER_VERSION = 150000


class PostgresRawStore(RawStore):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn: Any = None

    def __enter__(self) -> PostgresRawStore:
        self.conn = connect(self.dsn)
        if self.conn.info.server_version < MIN_SERVER_VERSION:
            raise StoreError(
                "the Postgres raw store needs server version 15 or newer "
                "(for NULLS NOT DISTINCT); this server reports "
                f"{self.conn.info.server_version}. Use --raw-store sqlite "
                "instead, or upgrade the server.")
        self.conn.execute(SCHEMA)
        self.conn.commit()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def put(self, row: RawRow) -> None:
        from psycopg.types.json import Jsonb

        self.conn.execute(
            """INSERT INTO raw_responses
                   (season, view, scoring_period, url, http_status, payload)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (season, view, scoring_period) DO UPDATE SET
                   url = EXCLUDED.url,
                   http_status = EXCLUDED.http_status,
                   payload = EXCLUDED.payload,
                   fetched_at = now()""",
            (row.season, row.view, row.scoring_period, row.url,
             row.http_status, Jsonb(row.payload)),
        )

    def get(self, season: int, view: str,
            scoring_period: int | None = None) -> Any | None:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT payload FROM raw_responses
                   WHERE season = %s AND view = %s
                     AND scoring_period IS NOT DISTINCT FROM %s""",
                (season, view, scoring_period))
            row = cur.fetchone()
        return row[0] if row else None

    def log_unit(self, season: int, view: str, scoring_period: int | None,
                 status: str, http_status: int | None = None,
                 error: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO fetch_log
                   (season, view, scoring_period, status, http_status, last_error)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (season, view, scoring_period) DO UPDATE SET
                   status = EXCLUDED.status,
                   http_status = EXCLUDED.http_status,
                   last_error = EXCLUDED.last_error,
                   attempts = fetch_log.attempts + 1,
                   fetched_at = now()""",
            (season, view, scoring_period, status, http_status, error),
        )

    def completed_units(self) -> set[Unit]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT season, view, scoring_period FROM fetch_log "
                        "WHERE status IN ('ok', 'empty')")
            return {(r[0], r[1], r[2]) for r in cur.fetchall()}

    def seasons(self) -> list[int]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT season FROM raw_responses ORDER BY season")
            return [r[0] for r in cur.fetchall()]

    def weeks(self, season: int, view: str) -> list[int]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT scoring_period FROM raw_responses
                   WHERE season = %s AND view = %s AND scoring_period IS NOT NULL
                   ORDER BY scoring_period""", (season, view))
            return [r[0] for r in cur.fetchall()]

    def iter_raw(self) -> Iterator[RawRow]:
        with self.conn.cursor(name="raw_scan") as cur:
            cur.execute(
                """SELECT season, view, scoring_period, url, http_status, payload
                   FROM raw_responses ORDER BY season, view, scoring_period""")
            for season, view, period, url, status, payload in cur:
                yield RawRow(season, view, period, url, status, payload)

    def fetch_log_summary(self) -> list[tuple[str, int]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM fetch_log "
                        "GROUP BY status ORDER BY 1")
            return [(r[0], r[1]) for r in cur.fetchall()]

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def describe(self) -> str:
        return "postgres"


def connect(dsn: str) -> Any:
    """Open a Postgres connection, turning driver errors into advice."""
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        from ..errors import MissingDependency
        raise MissingDependency("postgres", "psycopg[binary]", "postgres") from exc

    try:
        return psycopg.connect(dsn, autocommit=False)
    except psycopg.OperationalError as exc:
        raise StoreError(
            f"could not connect to Postgres: {exc}\n"
            "Start the bundled stack with `docker compose up -d`, point\n"
            "POSTGRES_* / DATABASE_URL at your own server, or drop Postgres\n"
            "entirely and use the default SQLite output.") from exc
