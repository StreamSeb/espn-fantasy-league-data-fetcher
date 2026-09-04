"""The raw store: where untouched ESPN payloads and the fetch ledger live.

Raw first. Every API response is written here before anything is parsed, and
the normalized tables are then built by reading these payloads back out of the
store - never from the in-memory response. That is deliberate: it means
`parse` exercises exactly the same code path as a fresh `fetch`, so the
"re-parse without re-fetching" guarantee is tested on every run rather than
asserted.

ESPN deletes old league data and reshapes this API roughly annually, so the
raw store - not any derived table - is the thing worth backing up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

#: A unit of work: one (season, view, scoring_period) request.
Unit = tuple[int, str, "int | None"]


@dataclass(frozen=True)
class RawRow:
    season: int
    view: str
    scoring_period: int | None
    url: str
    http_status: int
    payload: Any


class RawStore(ABC):
    """Storage for raw payloads plus the resume ledger."""

    @abstractmethod
    def __enter__(self) -> RawStore: ...

    @abstractmethod
    def __exit__(self, *exc: object) -> None: ...

    @abstractmethod
    def put(self, row: RawRow) -> None:
        """Upsert one payload on (season, view, scoring_period)."""

    @abstractmethod
    def get(self, season: int, view: str,
            scoring_period: int | None = None) -> Any | None: ...

    @abstractmethod
    def log_unit(self, season: int, view: str, scoring_period: int | None,
                 status: str, http_status: int | None = None,
                 error: str | None = None) -> None:
        """Record the outcome of a unit. status is 'ok', 'empty' or 'error'."""

    @abstractmethod
    def completed_units(self) -> set[Unit]:
        """Units already fetched successfully, so a re-run can skip them."""

    @abstractmethod
    def seasons(self) -> list[int]:
        """Seasons with at least one stored payload."""

    @abstractmethod
    def weeks(self, season: int, view: str) -> list[int]:
        """Scoring periods stored for one season and view."""

    @abstractmethod
    def iter_raw(self) -> Iterator[RawRow]:
        """Every stored payload, ordered by season then view then week."""

    @abstractmethod
    def fetch_log_summary(self) -> list[tuple[str, int]]:
        """(status, count) pairs across the whole ledger."""

    @abstractmethod
    def commit(self) -> None: ...

    def rollback(self) -> None:  # noqa: B027
        """Discard the open transaction.

        Deliberately concrete and empty rather than abstract: a store with no
        transaction has nothing to undo, and should not be forced to say so.
        """

    @abstractmethod
    def describe(self) -> str:
        """A short, secret-free description for log output."""


def open_store(cfg) -> RawStore:
    """Build the raw store named by the config."""
    from ..errors import ConfigError

    kind = (cfg.raw_store or "sqlite").lower()
    if kind == "sqlite":
        from .sqlite import SqliteRawStore
        return SqliteRawStore(cfg.raw_db_path)
    if kind in {"postgres", "postgresql", "pg"}:
        from .postgres import PostgresRawStore
        return PostgresRawStore(cfg.dsn)
    raise ConfigError(
        f"unknown raw store {kind!r}; expected 'sqlite' or 'postgres'")
