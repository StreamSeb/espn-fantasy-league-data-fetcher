"""CSV: one file per table, written into the output directory."""

from __future__ import annotations

import csv
import logging

from ..parse import Dataset
from . import Sink, SinkResult
from .files import as_text, out_dir

log = logging.getLogger(__name__)


class CsvSink(Sink):
    name = "csv"
    help = "one .csv per table (nested fields become compact JSON strings)"

    def write(self, data: Dataset) -> SinkResult:
        target = out_dir(self.cfg, "csv")
        total = 0
        for table, rows in data:
            path = target / f"{table.name}.csv"
            # The header is written even for an empty table: a downstream
            # loader should see an empty table, not a missing file.
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(table.columns)
                for row in rows:
                    writer.writerow([as_text(row.get(c), c, table)
                                     for c in table.columns])
            total += len(rows)
            log.debug("  %s -> %d rows", path, len(rows))
        return SinkResult("csv", str(target), total)
