"""Output formats.

A sink takes the parsed Dataset and writes it somewhere. Every sink sees the
same rows with the same column names, so adding a format means adding one
small class here and nothing else anywhere.
"""

from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import Config
from ..errors import ConfigError, MissingDependency
from ..parse import Dataset


@dataclass
class SinkResult:
    """What a sink wrote, for the run summary."""
    format: str
    destination: str
    rows: int


class Sink(ABC):
    #: Name used by --format.
    name: str = ""
    #: Extra needed to install it, or "" when it has no extra dependency.
    extra: str = ""
    #: Importable module this format needs, if any, and the distribution name
    #: to suggest installing. Checked before the first request rather than at
    #: write time, so a missing package fails in a second instead of after a
    #: ten-minute fetch.
    requires: tuple[str, str] | None = None
    #: One line for `--help` and the README table.
    help: str = ""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @classmethod
    def check_available(cls) -> None:
        if cls.requires is None:
            return
        module, package = cls.requires
        if importlib.util.find_spec(module) is None:
            raise MissingDependency(cls.name, package, cls.extra)

    @abstractmethod
    def write(self, data: Dataset) -> SinkResult: ...


def _registry() -> dict[str, type[Sink]]:
    from .csv_sink import CsvSink
    from .duckdb_sink import DuckdbSink
    from .excel_sink import ExcelSink
    from .json_sink import JsonlSink, JsonSink
    from .parquet_sink import ParquetSink
    from .postgres_sink import PostgresSink
    from .raw_sink import RawJsonSink
    from .sqlite_sink import SqliteSink

    sinks: tuple[type[Sink], ...] = (
        SqliteSink, PostgresSink, CsvSink, JsonSink, JsonlSink,
        ParquetSink, ExcelSink, DuckdbSink, RawJsonSink,
    )
    return {s.name: s for s in sinks}


def available_formats() -> dict[str, type[Sink]]:
    return _registry()


def build_sinks(cfg: Config) -> list[Sink]:
    registry = _registry()
    unknown = [f for f in cfg.formats if f not in registry]
    if unknown:
        raise ConfigError(
            f"unknown output format(s): {', '.join(unknown)}\n"
            f"Available: {', '.join(sorted(registry))}")
    sinks = []
    for name in cfg.formats:
        cls = registry[name]
        cls.check_available()
        sinks.append(cls(cfg))
    return sinks
