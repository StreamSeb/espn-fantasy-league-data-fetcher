"""Raw payload dump: the untouched ESPN responses, as files on disk.

The point of this format is that it does not depend on this tool. ESPN deletes
old league data and reshapes the API roughly annually, so a directory of the
original JSON is the durable backup - anyone can write their own parser
against it years from now, whatever has happened to the endpoint.
"""

from __future__ import annotations

import json
import logging

from ..parse import Dataset
from ..store import open_store
from . import Sink, SinkResult
from .files import out_dir

log = logging.getLogger(__name__)


class RawJsonSink(Sink):
    name = "raw"
    help = "untouched ESPN payloads as raw/<season>/<view>[-week].json"

    def write(self, data: Dataset) -> SinkResult:
        # Reads the store directly: raw payloads are deliberately not part of
        # the parsed Dataset, which holds only normalized rows.
        target = out_dir(self.cfg, "raw")
        written = 0
        with open_store(self.cfg) as store:
            for row in store.iter_raw():
                season_dir = target / str(row.season)
                season_dir.mkdir(parents=True, exist_ok=True)
                suffix = "" if row.scoring_period is None else f"-week{row.scoring_period:02d}"
                path = season_dir / f"{row.view}{suffix}.json"
                with path.open("w", encoding="utf-8") as fh:
                    json.dump(row.payload, fh, indent=1)
                    fh.write("\n")
                written += 1
        return SinkResult("raw", str(target), written)
