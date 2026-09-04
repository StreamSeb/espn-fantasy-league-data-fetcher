"""Parquet: one file per table, for pandas, polars and DuckDB."""

from __future__ import annotations

import json
import logging
from typing import Any

from .. import tables as T
from ..errors import MissingDependency
from ..parse import Dataset
from . import Sink, SinkResult
from .coltypes import BOOL, FLOAT, INT, JSON, TIMESTAMP, classify
from .files import out_dir

log = logging.getLogger(__name__)


class ParquetSink(Sink):
    name = "parquet"
    requires = ("pyarrow", "pyarrow")
    extra = "parquet"
    help = "one .parquet per table (needs `pip install 'espn-fantasy[parquet]'`)"

    def write(self, data: Dataset) -> SinkResult:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:
            raise MissingDependency("parquet", "pyarrow", "parquet") from exc

        arrow = {
            JSON: pa.string(), TIMESTAMP: pa.timestamp("us", tz="UTC"),
            BOOL: pa.bool_(), INT: pa.int64(), FLOAT: pa.float64(),
        }
        target = out_dir(self.cfg, "parquet")
        total = 0
        for table, rows in data:
            schema = pa.schema([
                pa.field(c, arrow.get(classify(table, c), pa.string()))
                for c in table.columns
            ])
            columns = {c: [_cell(row.get(c), c, table) for row in rows]
                       for c in table.columns}
            pq.write_table(pa.Table.from_pydict(columns, schema=schema),
                           target / f"{table.name}.parquet", compression="zstd")
            total += len(rows)
        return SinkResult("parquet", str(target), total)


def _cell(value: Any, column: str, table: T.Table) -> Any:
    if value is not None and classify(table, column) == JSON:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return value
