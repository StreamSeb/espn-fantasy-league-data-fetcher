"""SQLite: one portable database file. The default output.

Chosen as the default because it needs nothing installed - `sqlite3` ships
with Python - and the result is a single file you can copy, commit to a backup
drive, open in a GUI, or query from DuckDB, pandas, R or Excel.

Rows are upserted on their natural key, so re-running never duplicates and a
partial run can be topped up.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import tables as T
from ..errors import StoreError
from ..parse import Dataset
from . import Sink, SinkResult
from .coltypes import BOOL, FLOAT, INT, JSON, TIMESTAMP, classify

log = logging.getLogger(__name__)

#: SQLite's type affinities. BOOLEAN and TIMESTAMP are accepted as declared
#: types and map to INTEGER/NUMERIC affinity; naming them keeps the schema
#: self-documenting for anyone opening the file in a GUI.
SQL_TYPES = {JSON: "TEXT", TIMESTAMP: "TEXT", BOOL: "INTEGER",
             INT: "INTEGER", FLOAT: "REAL"}

INDEXES = {
    "teams": [("teams_member_idx", "member_id")],
    "matchups": [("matchups_season_week_idx", "season, week"),
                 ("matchups_tier_idx", "season, playoff_tier_type")],
    "roster_slots": [("roster_slots_player_idx", "player_id"),
                     ("roster_slots_week_idx", "season, week, team_id")],
    "draft_picks": [("draft_picks_team_idx", "season, team_id")],
    "transactions": [("transactions_team_idx", "season, team_id"),
                     ("transactions_type_idx", "season, type"),
                     ("transactions_player_idx", "player_id"),
                     ("transactions_status_idx", "season, status")],
}


def ddl(table: T.Table) -> str:
    columns = ",\n    ".join(
        f'"{c}" {SQL_TYPES.get(classify(table, c), "TEXT")}'
        + (" NOT NULL" if c in table.key else "")
        for c in table.columns)
    key = ", ".join(f'"{c}"' for c in table.key)
    return (f'CREATE TABLE IF NOT EXISTS "{table.name}" (\n    {columns},\n'
            f'    PRIMARY KEY ({key})\n);')


def upsert_sql(table: T.Table) -> str:
    columns = ", ".join(f'"{c}"' for c in table.columns)
    placeholders = ", ".join("?" * len(table.columns))
    key = ", ".join(f'"{c}"' for c in table.key)
    updates = table.value_columns()
    if not updates:
        action = "DO NOTHING"
    else:
        sets = ", ".join(f'"{c}" = excluded."{c}"' for c in updates)
        action = f"DO UPDATE SET {sets}"
    return (f'INSERT INTO "{table.name}" ({columns}) VALUES ({placeholders}) '
            f"ON CONFLICT ({key}) {action}")


def encode(value: Any, column: str, table: T.Table) -> Any:
    """SQLite stores no JSON, boolean or datetime types natively."""
    if value is None:
        return None
    kind = classify(table, column)
    if kind == JSON:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


class SqliteSink(Sink):
    name = "sqlite"
    help = "a single .sqlite3 file (default; no server, no dependencies)"

    def write(self, data: Dataset) -> SinkResult:
        path = Path(self.cfg.out_dir) / "espn_fantasy.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error as exc:
            raise StoreError(f"could not open {path}: {exc}") from exc

        total = 0
        try:
            for table, rows in data:
                conn.execute(ddl(table))
                for name, columns in INDEXES.get(table.name, []):
                    conn.execute(f'CREATE INDEX IF NOT EXISTS {name} '
                                 f'ON "{table.name}" ({columns})')
                if rows:
                    conn.executemany(upsert_sql(table), [
                        [encode(row.get(c), c, table) for c in table.columns]
                        for row in rows])
                total += len(rows)
            conn.commit()
        finally:
            conn.close()
        log.debug("wrote %d rows to %s", total, path)
        return SinkResult("sqlite", str(path), total)
