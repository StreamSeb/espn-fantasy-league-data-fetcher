# Contributing

Bug reports and pull requests are welcome.

## Before you open an issue

**Never paste `ESPN_S2` or `SWID` into an issue** — not in a log, not in a
screenshot, not in a stack trace. Anyone holding them can act as you on ESPN.
`ffl-history doctor` prints them masked; use its output.

League ids are less sensitive but still identifying. Redact them if you would
rather not have your league found.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

The test suite needs no network and no database. It runs against a
hand-written fixture league in `tests/conftest.py` — deliberately synthetic, so
the repository carries nobody's member GUIDs or team names.

To also run the Postgres round-trip tests, point `FFL_TEST_DSN` at a scratch
database. **They drop and recreate every table**, so do not aim it at one
holding a real league:

```bash
docker compose up -d
FFL_TEST_DSN="postgresql://ffl:PASSWORD@127.0.0.1:5434/ffl_scratch" pytest
```

## Adding an output format

1. Write a `Sink` subclass in `ffl_history/sinks/`. It receives the parsed
   `Dataset` and writes it; that is the whole interface.
2. Add it to the registry in `ffl_history/sinks/__init__.py`.
3. If it needs a package, add an extra in `pyproject.toml` and raise
   `MissingDependency` on the import — never let a missing package surface as
   a bare `ModuleNotFoundError`.
4. Add it to `FILE_FORMATS` in `tests/test_sinks.py`, which asserts that every
   format writes the same row count as every other.

Parsers and the raw store should not need to change.

## Adding a field

1. Add the column to the relevant `Table` in `ffl_history/tables.py`.
2. Populate it in `ffl_history/parse.py`.
3. Give it a type in `ffl_history/sinks/coltypes.py` unless plain text is
   right — the typed sinks share that one classification so their schemas
   cannot drift apart.
4. Add it to `ffl_history/schema/postgres.sql`, which is hand-written on
   purpose.

## Style

Match what is there. Comments explain *why*, especially where ESPN's API is
surprising — that is most of the value in this codebase, and most of the
comments earn their place by having cost somebody an afternoon.
