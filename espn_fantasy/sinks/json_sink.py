"""JSON output, in two shapes.

`json` writes one array per table, which is the easiest thing to hand to a
browser or `jq`. `jsonl` writes one object per line, which streams and is what
BigQuery, DuckDB's read_json_auto and most log tooling expect.
"""

from __future__ import annotations

import json
import logging

from ..parse import Dataset
from . import Sink, SinkResult
from .files import as_json, json_default, out_dir

log = logging.getLogger(__name__)


class JsonSink(Sink):
    name = "json"
    help = "one .json file per table, each a single array of objects"

    def write(self, data: Dataset) -> SinkResult:
        target = out_dir(self.cfg, "json")
        total = 0
        for table, rows in data:
            payload = [
                {c: as_json(row.get(c), c, table) for c in table.columns}
                for row in rows
            ]
            path = target / f"{table.name}.json"
            with path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, default=json_default)
                fh.write("\n")
            total += len(rows)
        return SinkResult("json", str(target), total)


class JsonlSink(Sink):
    name = "jsonl"
    help = "one .jsonl per table, newline-delimited (streams; BigQuery-ready)"

    def write(self, data: Dataset) -> SinkResult:
        target = out_dir(self.cfg, "jsonl")
        total = 0
        for table, rows in data:
            path = target / f"{table.name}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for row in rows:
                    record = {c: as_json(row.get(c), c, table)
                              for c in table.columns}
                    fh.write(json.dumps(record, default=json_default,
                                        separators=(",", ":")))
                    fh.write("\n")
            total += len(rows)
        return SinkResult("jsonl", str(target), total)
