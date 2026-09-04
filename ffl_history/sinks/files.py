"""Shared helpers for sinks that write files rather than talk to a database."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import tables as T
from ..config import Config


def out_dir(cfg: Config, subdir: str = "") -> Path:
    path = Path(cfg.out_dir) / subdir if subdir else Path(cfg.out_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def as_text(value: Any, column: str, table: T.Table) -> Any:
    """Flatten one value for a text-oriented format (CSV, spreadsheet).

    Nested structures become compact JSON so nothing is silently lost, and
    timestamps become ISO-8601 so they sort correctly as strings.
    """
    if value is None:
        return ""
    if column in table.json_columns:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def as_json(value: Any, column: str, table: T.Table) -> Any:
    """Flatten one value for JSON output: nested structures stay nested."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__} to JSON")
