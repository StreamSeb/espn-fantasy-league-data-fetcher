"""Configuration: defaults < .env file < environment < command-line flags.

Every setting can be supplied three ways so the tool works for a one-off
`--league-id 12345 --seasons 2023` invocation, for a checked-out .env, and for
CI where everything arrives as environment variables.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .errors import ConfigError

#: The seasons/{season}/segments/0/leagues/{id} endpoint shape starts here.
#: Earlier years are served, if at all, by a different API that this tool
#: does not model.
EARLIEST_SEASON = 2018

DEFAULT_MAX_WEEK = 18
DEFAULT_DELAY = 1.0
DEFAULT_OUT_DIR = "out"
DEFAULT_SQLITE_PATH = "out/ffl_history.sqlite3"


def load_env_file(path: str | os.PathLike[str] | None) -> None:
    """Load a .env file without overriding variables already in the real
    environment, so `ESPN_LEAGUE_ID=x ffl-history` beats the file."""
    if path is None:
        return
    p = Path(path)
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:  # pragma: no cover - dotenv is a hard dep
        return
    load_dotenv(p, override=False)


def parse_int_spec(spec: str, *, what: str = "value") -> list[int]:
    """Parse "2018-2020,2023" or "2024" or "1,3,5" into a sorted unique list."""
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                raise ConfigError(f"{what} range {part!r} runs backwards")
            out.update(range(lo, hi + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ConfigError(
                f"could not parse {what} {part!r}; expected forms like "
                f"'2023', '2018-2025' or '2019,2021,2024'")
    if not out:
        raise ConfigError(f"no {what}s in {spec!r}")
    return sorted(out)


def parse_seasons(spec: str) -> list[int] | None:
    """None means 'discover from ESPN'."""
    if spec.strip().lower() in {"auto", "all"}:
        return None
    seasons = parse_int_spec(spec, what="season")
    too_old = [s for s in seasons if s < EARLIEST_SEASON]
    if too_old:
        raise ConfigError(
            f"season(s) {', '.join(map(str, too_old))} are before {EARLIEST_SEASON}; "
            "ESPN does not serve them through this API shape")
    return seasons


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass
class Config:
    league_id: str = ""
    espn_s2: str = ""
    swid: str = ""

    #: None means "ask ESPN which seasons this league has".
    seasons: list[int] | None = None
    #: None means "cap each season at its own latest scoring period".
    max_week: int | None = None
    weeks: list[int] | None = None

    delay: float = DEFAULT_DELAY
    timeout: float = 30.0
    retries: int = 3

    raw_store: str = "sqlite"
    raw_db_path: str = DEFAULT_SQLITE_PATH

    formats: list[str] = field(default_factory=lambda: ["sqlite"])
    out_dir: str = DEFAULT_OUT_DIR

    pg_host: str = "127.0.0.1"
    pg_port: int = 5434
    pg_db: str = "ffl_history"
    pg_user: str = "ffl"
    pg_password: str = ""
    pg_dsn: str = ""

    @property
    def dsn(self) -> str:
        if self.pg_dsn:
            return self.pg_dsn
        parts = [
            f"host={self.pg_host}", f"port={self.pg_port}",
            f"dbname={self.pg_db}", f"user={self.pg_user}",
        ]
        if self.pg_password:
            parts.append(f"password={self.pg_password}")
        return " ".join(parts)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_env(cls) -> Config:
        cfg = cls()
        cfg.league_id = _env("ESPN_LEAGUE_ID")
        cfg.espn_s2 = _env("ESPN_S2")
        cfg.swid = _env("SWID")

        spec = _env("ESPN_SEASONS")
        if spec:
            cfg.seasons = parse_seasons(spec)
        else:
            first, last = _env("ESPN_FIRST_SEASON"), _env("ESPN_LAST_SEASON")
            if first or last:
                lo = int(first) if first else EARLIEST_SEASON
                hi = int(last) if last else date.today().year
                cfg.seasons = parse_seasons(f"{lo}-{hi}")

        raw_max = _env("MAX_SCORING_PERIOD")
        if raw_max and raw_max.lower() != "auto":
            cfg.max_week = _env_int("MAX_SCORING_PERIOD", DEFAULT_MAX_WEEK)

        cfg.delay = _env_float("REQUEST_DELAY_SECONDS", DEFAULT_DELAY)
        cfg.timeout = _env_float("REQUEST_TIMEOUT_SECONDS", 30.0)
        cfg.retries = _env_int("REQUEST_RETRIES", 3)

        cfg.raw_store = _env("FFL_RAW_STORE", "sqlite").lower()
        cfg.raw_db_path = _env("FFL_SQLITE_PATH", DEFAULT_SQLITE_PATH)
        cfg.out_dir = _env("FFL_OUT_DIR", DEFAULT_OUT_DIR)
        fmts = _env("FFL_FORMATS")
        if fmts:
            cfg.formats = [f.strip().lower() for f in fmts.split(",") if f.strip()]

        cfg.pg_host = _env("POSTGRES_HOST", "127.0.0.1")
        cfg.pg_port = _env_int("POSTGRES_PORT", 5434)
        cfg.pg_db = _env("POSTGRES_DB", "ffl_history")
        cfg.pg_user = _env("POSTGRES_USER", "ffl")
        cfg.pg_password = os.getenv("POSTGRES_PASSWORD", "")
        cfg.pg_dsn = _env("DATABASE_URL") or _env("POSTGRES_DSN")
        return cfg

    def apply_args(self, args: argparse.Namespace) -> Config:
        """Command-line flags win over everything else."""
        if getattr(args, "league_id", None):
            self.league_id = str(args.league_id).strip()
        if getattr(args, "espn_s2", None):
            self.espn_s2 = args.espn_s2.strip()
        if getattr(args, "swid", None):
            self.swid = args.swid.strip()
        if getattr(args, "seasons", None):
            self.seasons = parse_seasons(args.seasons)
        if getattr(args, "weeks", None):
            self.weeks = parse_int_spec(args.weeks, what="week")
        if getattr(args, "max_week", None) is not None:
            self.max_week = args.max_week
        if getattr(args, "delay", None) is not None:
            self.delay = args.delay
        if getattr(args, "retries", None) is not None:
            self.retries = args.retries
        if getattr(args, "raw_store", None):
            self.raw_store = args.raw_store
        if getattr(args, "raw_db", None):
            self.raw_db_path = args.raw_db
        if getattr(args, "out", None):
            self.out_dir = args.out
        if getattr(args, "format", None):
            self.formats = list(dict.fromkeys(args.format))
        if getattr(args, "dsn", None):
            self.pg_dsn = args.dsn
        return self

    # -- validation --------------------------------------------------------

    def require_espn_credentials(self) -> None:
        missing = [n for n, v in (("ESPN_LEAGUE_ID", self.league_id),
                                  ("ESPN_S2", self.espn_s2),
                                  ("SWID", self.swid)) if not v]
        if missing:
            raise ConfigError(
                f"missing required setting(s): {', '.join(missing)}\n\n"
                "Pass them as flags (--league-id / --espn-s2 / --swid), export\n"
                "them as environment variables, or put them in a .env file\n"
                "(`ffl-history init-env` writes a template).\n\n"
                "ESPN has required auth cookies on historical seasons since\n"
                "August 2025; see the README for where to find espn_s2 and SWID.")
        if not self.league_id.isdigit():
            raise ConfigError(
                f"ESPN_LEAGUE_ID must be numeric, got {self.league_id!r}. "
                "It is the leagueId= value in your ESPN league URL.")
        if not (self.swid.startswith("{") and self.swid.endswith("}")):
            raise ConfigError(
                "SWID must include the surrounding braces, e.g. {1A2B3C4D-...}")

    def week_range(self) -> list[int]:
        """Weeks to request, before any per-season cap is applied."""
        if self.weeks:
            return self.weeks
        return list(range(1, (self.max_week or DEFAULT_MAX_WEEK) + 1))

    def redacted(self) -> dict[str, str]:
        """Everything worth printing, with secrets masked."""
        return {
            "league_id": self.league_id or "(unset)",
            "espn_s2": _mask(self.espn_s2),
            "swid": _mask(self.swid),
            "seasons": (",".join(map(str, self.seasons)) if self.seasons
                        else "auto (discover from ESPN)"),
            "weeks": ("auto (per-season latest scoring period)"
                      if self.max_week is None and not self.weeks
                      else ",".join(map(str, self.week_range()))),
            "raw_store": f"{self.raw_store} ({self.raw_db_path})"
                         if self.raw_store == "sqlite" else "postgres",
            "formats": ",".join(self.formats),
            "out_dir": self.out_dir,
        }


def _mask(value: str) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-2:]} ({len(value)} chars)"
