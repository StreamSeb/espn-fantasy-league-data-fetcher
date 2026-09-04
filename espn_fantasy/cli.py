"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_OUT_DIR,
    DEFAULT_SQLITE_PATH,
    Config,
    load_env_file,
)
from .errors import FflError
from .parse import Dataset
from .pipeline import resolve_seasons, run_export, run_fetch, run_parse
from .report import print_summary
from .sinks import available_formats, build_sinks
from .store import open_store

log = logging.getLogger("espn_fantasy")

#: Shipped inside the package, not read from the repository root: a wheel
#: installed from PyPI has no repository root to read from. The root
#: .env.example is a copy, and a test asserts the two stay identical.
ENV_TEMPLATE = Path(__file__).resolve().parent / "env.example"

EPILOG = """\
examples:
  # everything, discovering which seasons your league has, into out/
  espn-fantasy run --league-id 123456

  # just two seasons, as CSV and a spreadsheet
  espn-fantasy run --seasons 2023,2024 --format csv --format xlsx

  # load into the Postgres stack in docker-compose.yml
  docker compose up -d
  espn-fantasy run --format postgres

  # add a new format later, with no further requests to ESPN
  espn-fantasy export --format parquet

  # top up an in-progress season; already-stored weeks are skipped
  espn-fantasy run --seasons 2025
"""


def build_parser() -> argparse.ArgumentParser:
    formats = available_formats()
    format_help = "\n".join(f"  {name:9s} {cls.help}"
                            for name, cls in formats.items())

    parser = argparse.ArgumentParser(
        prog="espn-fantasy",
        description="Export an ESPN fantasy football league's full history.",
        epilog=EPILOG + "\noutput formats:\n" + format_help,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"espn-fantasy {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", default=".env", metavar="PATH",
                        help="file to read settings from (default: .env; "
                             "real environment variables always win)")
    verbosity = common.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true",
                           help="log every request and row count")
    verbosity.add_argument("-q", "--quiet", action="store_true",
                           help="only warnings and errors")

    league = argparse.ArgumentParser(add_help=False)
    league.add_argument("--league-id", metavar="ID",
                        help="numeric league id, from the leagueId= parameter "
                             "in your ESPN league URL")
    league.add_argument("--espn-s2", metavar="COOKIE",
                        help="espn_s2 cookie (prefer the ESPN_S2 environment "
                             "variable, so it stays out of your shell history)")
    league.add_argument("--swid", metavar="COOKIE",
                        help="SWID cookie, braces included (prefer the SWID "
                             "environment variable)")

    scope = argparse.ArgumentParser(add_help=False)
    scope.add_argument("--seasons", metavar="SPEC",
                       help="which years to pull: '2023', '2018-2024', "
                            "'2019,2021,2024', or 'auto' (the default) to ask "
                            "ESPN which seasons the league has")
    weeks = scope.add_mutually_exclusive_group()
    weeks.add_argument("--weeks", metavar="SPEC",
                       help="which weeks to pull, e.g. '1-14' or '1,2,3'; "
                            "capped per season at the last one played")
    weeks.add_argument("--max-week", type=int, metavar="N",
                       help="highest week to request (default: as many as "
                            "each season actually played)")

    behaviour = argparse.ArgumentParser(add_help=False)
    behaviour.add_argument("--delay", type=float, metavar="SECONDS",
                           help="pause between requests (default: 1.0). ESPN "
                                "publishes no rate limit; be a good citizen")
    behaviour.add_argument("--retries", type=int, metavar="N",
                           help="attempts per request before giving up on it "
                                "(default: 3)")

    store = argparse.ArgumentParser(add_help=False)
    store.add_argument("--raw-store", choices=["sqlite", "postgres"],
                       help="where raw ESPN payloads are kept "
                            "(default: sqlite)")
    store.add_argument("--raw-db", metavar="PATH",
                       help=f"SQLite file for the raw store "
                            f"(default: {DEFAULT_SQLITE_PATH})")
    store.add_argument("--dsn", metavar="URL",
                       help="Postgres connection string; overrides the "
                            "POSTGRES_* settings")

    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("-f", "--format", action="append",
                        choices=sorted(formats), metavar="FORMAT",
                        help="output format, repeatable (default: sqlite). "
                             "One of: " + ", ".join(sorted(formats)))
    output.add_argument("-o", "--out", metavar="DIR",
                        help=f"directory for file outputs "
                             f"(default: {DEFAULT_OUT_DIR})")

    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser(
        "run", parents=[common, league, scope, behaviour, store, output],
        help="fetch, parse and export (the usual command)",
        description="Fetch anything missing, parse it, and write every "
                    "requested output format.")
    run.add_argument("--force-refetch", action="store_true",
                     help="ignore the ledger and re-request every unit")
    run.set_defaults(func=cmd_run)

    fetch = subparsers.add_parser(
        "fetch", parents=[common, league, scope, behaviour, store],
        help="fetch raw payloads only, no parsing or export")
    fetch.add_argument("--force-refetch", action="store_true",
                       help="ignore the ledger and re-request every unit")
    fetch.set_defaults(func=cmd_fetch)

    parse = subparsers.add_parser(
        "parse", parents=[common, league, scope, store, output],
        help="rebuild outputs from stored payloads; makes no requests",
        description="Re-parse whatever the raw store already holds. Never "
                    "touches the network, so it is free to re-run.")
    parse.set_defaults(func=cmd_parse)

    export = subparsers.add_parser(
        "export", parents=[common, league, scope, store, output],
        help="alias for `parse`, for when you only want a new file format")
    export.set_defaults(func=cmd_parse)

    summary = subparsers.add_parser(
        "summary", parents=[common, league, scope, store],
        help="print the summary from stored payloads, writing nothing")
    summary.set_defaults(func=cmd_summary)

    doctor = subparsers.add_parser(
        "doctor", parents=[common, league, scope, behaviour, store, output],
        help="check settings, dependencies and connectivity, then stop")
    doctor.set_defaults(func=cmd_doctor)

    init = subparsers.add_parser(
        "init-env", parents=[common],
        help="write a .env template to fill in")
    init.add_argument("--path", default=".env", metavar="PATH")
    init.set_defaults(func=cmd_init_env)

    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    # Sinks are built before the first request so that a typo in --format, or
    # a missing pyarrow, fails in a second rather than after a long fetch.
    sinks = build_sinks(cfg)
    with open_store(cfg) as store:
        run_fetch(store, cfg, force=args.force_refetch)
        data = run_parse(store, cfg)
        _export_and_report(data, cfg, store, sinks)
    return 0


def cmd_fetch(cfg: Config, args: argparse.Namespace) -> int:
    with open_store(cfg) as store:
        report = run_fetch(store, cfg, force=args.force_refetch)
    print(f"\nStored {report.fetched} new payload(s) in {cfg.raw_store}; "
          f"{report.skipped} were already there.")
    if report.failed:
        print(f"{report.failed} unit(s) failed. Re-run to retry only those.")
    print("Next: `espn-fantasy parse` to build the tables.")
    return 0


def cmd_parse(cfg: Config, args: argparse.Namespace) -> int:
    sinks = build_sinks(cfg)
    with open_store(cfg) as store:
        data = run_parse(store, cfg)
        if data.is_empty():
            return 1
        _export_and_report(data, cfg, store, sinks)
    return 0


def cmd_summary(cfg: Config, args: argparse.Namespace) -> int:
    with open_store(cfg) as store:
        data = run_parse(store, cfg)
        print_summary(data, store)
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    """Check everything that can be checked without a full run."""
    ok = True
    print("settings")
    for key, value in cfg.redacted().items():
        print(f"  {key:12s} {value}")

    print("\nchecks")
    try:
        cfg.require_espn_credentials()
        print("  [ok]   league id and cookies are present and well-formed")
    except FflError as exc:
        ok = False
        print(f"  [FAIL] {textwrap.indent(str(exc), '         ').strip()}")

    sinks = _try(lambda: build_sinks(cfg))
    if sinks is None:
        ok = False
    for sink in sinks or []:
        print(f"  [ok]   output format '{sink.name}' is available")

    try:
        with open_store(cfg) as store:
            seasons = store.seasons()
        print(f"  [ok]   raw store reachable ({cfg.raw_store}), "
              f"{len(seasons)} season(s) stored"
              + (f": {', '.join(map(str, seasons))}" if seasons else ""))
    except FflError as exc:
        ok = False
        print(f"  [FAIL] raw store: {exc}")

    if ok and cfg.league_id:
        from .espn import EspnClient
        try:
            seasons = resolve_seasons(cfg, EspnClient(cfg))
            print(f"  [ok]   ESPN reachable; seasons to fetch: "
                  f"{', '.join(map(str, seasons))}")
        except Exception as exc:
            ok = False
            print(f"  [FAIL] ESPN: {exc}")

    print("\nready" if ok else "\nnot ready -- fix the failures above")
    return 0 if ok else 1


def cmd_init_env(cfg: Config, args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists():
        print(f"{target} already exists; not overwriting it.")
        return 1
    target.write_text(ENV_TEMPLATE.read_text())
    # The file is about to hold session cookies for the user's ESPN account.
    os.chmod(target, 0o600)
    print(f"Wrote {target} (mode 600). Fill in ESPN_LEAGUE_ID, ESPN_S2 and "
          f"SWID, then run:\n  espn-fantasy run")
    return 0


def _export_and_report(data: Dataset, cfg: Config, store, sinks) -> None:
    results = run_export(data, cfg, sinks)
    print_summary(data, store)
    print("WROTE\n" + "-" * 78)
    for result in results:
        print(f"  {result.format:9s} {result.rows:>8,} rows  ->  "
              f"{result.destination}")
    print()


def _try(fn):
    try:
        return fn()
    except FflError as exc:
        print(f"  [FAIL] {exc}")
        return None


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    level = (logging.DEBUG if getattr(args, "verbose", False)
             else logging.WARNING if getattr(args, "quiet", False)
             else logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    try:
        load_env_file(getattr(args, "env_file", None))
        cfg = Config.from_env().apply_args(args)
        return args.func(cfg, args)
    except FflError as exc:
        # Expected failures: a missing cookie is not a bug, so print advice
        # rather than a traceback.
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted. Progress is saved -- re-run to pick up where "
              "this left off.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
