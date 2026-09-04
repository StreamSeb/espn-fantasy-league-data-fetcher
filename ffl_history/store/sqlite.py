"""SQLite raw store - the zero-setup default.

One file, no server, no container. `sqlite3` is in the standard library, so
this backend works on any machine that can run Python.

Season-level views have no scoring period. SQLite treats NULLs as distinct in
a unique index, so a NULL scoring_period would insert a fresh duplicate row on
every run instead of upserting. The sentinel below gives those rows a real,
comparable key; NULL is restored on the way back out.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..errors import StoreError
from . import RawRow, RawStore, Unit

#: Stand-in for "this view has no scoring period", inside the unique index only.
NO_PERIOD = -1

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_responses (
    season          INTEGER NOT NULL,
    "view"          TEXT    NOT NULL,
    period_key      INTEGER NOT NULL,
    scoring_period  INTEGER,
    url             TEXT    NOT NULL,
    http_status     INTEGER NOT NULL,
    fetched_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    payload         TEXT    NOT NULL,
    PRIMARY KEY (season, "view", period_key)
);

CREATE INDEX IF NOT EXISTS raw_responses_season_idx ON raw_responses (season);

CREATE TABLE IF NOT EXISTS fetch_log (
    season          INTEGER NOT NULL,
    "view"          TEXT    NOT NULL,
    period_key      INTEGER NOT NULL,
    scoring_period  INTEGER,
    status          TEXT    NOT NULL CHECK (status IN ('ok', 'error', 'empty')),
    http_status     INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_error      TEXT,
    fetched_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (season, "view", period_key)
);
"""


def period_key(scoring_period: int | None) -> int:
    return NO_PERIOD if scoring_period is None else scoring_period


class SqliteRawStore(RawStore):
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteRawStore:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(self.path)
        except sqlite3.Error as exc:
            raise StoreError(f"could not open SQLite database {self.path}: {exc}") from exc
        # WAL keeps a long fetch from blocking a reader in another shell.
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    @property
    def _c(self) -> sqlite3.Connection:
        if self.conn is None:
            raise StoreError("SQLite store used outside a `with` block")
        return self.conn

    def put(self, row: RawRow) -> None:
        self._c.execute(
            """INSERT INTO raw_responses
                   (season, "view", period_key, scoring_period, url,
                    http_status, payload, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (season, "view", period_key) DO UPDATE SET
                   url = excluded.url,
                   http_status = excluded.http_status,
                   payload = excluded.payload,
                   fetched_at = excluded.fetched_at""",
            (row.season, row.view, period_key(row.scoring_period),
             row.scoring_period, row.url, row.http_status,
             json.dumps(row.payload, separators=(",", ":"))),
        )

    def get(self, season: int, view: str,
            scoring_period: int | None = None) -> Any | None:
        cur = self._c.execute(
            """SELECT payload FROM raw_responses
               WHERE season = ? AND "view" = ? AND period_key = ?""",
            (season, view, period_key(scoring_period)))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def log_unit(self, season: int, view: str, scoring_period: int | None,
                 status: str, http_status: int | None = None,
                 error: str | None = None) -> None:
        self._c.execute(
            """INSERT INTO fetch_log
                   (season, "view", period_key, scoring_period, status,
                    http_status, last_error, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (season, "view", period_key) DO UPDATE SET
                   status = excluded.status,
                   http_status = excluded.http_status,
                   last_error = excluded.last_error,
                   attempts = fetch_log.attempts + 1,
                   fetched_at = excluded.fetched_at""",
            (season, view, period_key(scoring_period), scoring_period,
             status, http_status, error),
        )

    def completed_units(self) -> set[Unit]:
        cur = self._c.execute(
            """SELECT season, "view", scoring_period FROM fetch_log
               WHERE status IN ('ok', 'empty')""")
        return {(r[0], r[1], r[2]) for r in cur.fetchall()}

    def seasons(self) -> list[int]:
        cur = self._c.execute(
            "SELECT DISTINCT season FROM raw_responses ORDER BY season")
        return [r[0] for r in cur.fetchall()]

    def weeks(self, season: int, view: str) -> list[int]:
        cur = self._c.execute(
            """SELECT scoring_period FROM raw_responses
               WHERE season = ? AND "view" = ? AND scoring_period IS NOT NULL
               ORDER BY scoring_period""", (season, view))
        return [r[0] for r in cur.fetchall()]

    def iter_raw(self) -> Iterator[RawRow]:
        cur = self._c.execute(
            """SELECT season, "view", scoring_period, url, http_status, payload
               FROM raw_responses ORDER BY season, "view", period_key""")
        for season, view, period, url, status, payload in cur:
            yield RawRow(season, view, period, url, status, json.loads(payload))

    def fetch_log_summary(self) -> list[tuple[str, int]]:
        cur = self._c.execute(
            "SELECT status, count(*) FROM fetch_log GROUP BY status ORDER BY 1")
        return [(r[0], r[1]) for r in cur.fetchall()]

    def commit(self) -> None:
        self._c.commit()

    def describe(self) -> str:
        return f"sqlite:{self.path}"
