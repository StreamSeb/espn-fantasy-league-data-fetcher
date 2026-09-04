"""DuckDB: a single analytical database file, no server.

Tables are replaced wholesale rather than upserted. A parse always rebuilds
every row from the raw store, so a full replace is both simpler and
guaranteed to leave no stale rows behind from an earlier, wider season range.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .. import tables as T
from ..errors import MissingDependency
from ..parse import Dataset
from . import Sink, SinkResult
from .coltypes import BOOL, FLOAT, INT, JSON, TIMESTAMP, classify

log = logging.getLogger(__name__)

SQL_TYPES = {JSON: "JSON", TIMESTAMP: "TIMESTAMPTZ", BOOL: "BOOLEAN",
             INT: "BIGINT", FLOAT: "DOUBLE"}


class DuckdbSink(Sink):
    name = "duckdb"
    requires = ("duckdb", "duckdb")
    extra = "duckdb"
    help = "a single .duckdb file (needs `pip install 'ffl-history[duckdb]'`)"

    def write(self, data: Dataset) -> SinkResult:
        try:
            import duckdb
        except ModuleNotFoundError as exc:
            raise MissingDependency("duckdb", "duckdb", "duckdb") from exc

        path = Path(self.cfg.out_dir) / "ffl_history.duckdb"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(path))
        total = 0
        try:
            for table, rows in data:
                columns = ", ".join(
                    f'"{c}" {SQL_TYPES.get(classify(table, c), "VARCHAR")}'
                    for c in table.columns)
                conn.execute(f'DROP TABLE IF EXISTS "{table.name}"')
                conn.execute(f'CREATE TABLE "{table.name}" ({columns})')
                if not rows:
                    continue
                placeholders = ", ".join("?" * len(table.columns))
                conn.executemany(
                    f'INSERT INTO "{table.name}" VALUES ({placeholders})',
                    [[_cell(row.get(c), c, table) for c in table.columns]
                     for row in rows])
                total += len(rows)
            conn.commit()
        finally:
            conn.close()
        return SinkResult("duckdb", str(path), total)


def _cell(value: Any, column: str, table: T.Table) -> Any:
    if value is not None and classify(table, column) == JSON:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return value
