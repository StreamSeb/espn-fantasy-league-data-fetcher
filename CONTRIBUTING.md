# Contributing

Bug reports and pull requests are welcome.

## Before you open an issue

**Never paste `ESPN_S2` or `SWID` into an issue** — not in a log, not in a
screenshot, not in a stack trace. Anyone holding them can act as you on ESPN.
`espn-fantasy doctor` prints them masked; use its output.

League ids are less sensitive but still identifying. Redact them if you would
rather not have your league found.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
git config core.hooksPath .githooks    # see below -- please do this
pytest
ruff check .
```

## The pre-commit hook

`.githooks/pre-commit` refuses to commit ESPN cookies or exported league data.
Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

It is not automatic -- git will not run a hook a repository ships until you
point `core.hooksPath` at it, which is deliberate on git's part: a repository
you clone should not be able to execute code on your machine unasked.

It blocks four things: a staged `.env`, staged databases or exports, an
`ESPN_S2`/`SWID` value pasted into any file, and a real-looking
`POSTGRES_PASSWORD`. It reads only added lines, and it is careful about false
positives -- the README has to be able to *describe* what a cookie looks like.
`tests/test_precommit_hook.py` covers both halves: what it must block, and
what it must let through.

If you genuinely need a well-formed fake -- documentation showing what a real
value looks like, or a test fixture -- end the line with `allowlist secret`
and the hook will skip that one line. Nothing else on the file is exempted.

`git commit --no-verify` skips the hook entirely. Please do not.

The test suite needs no network and no database. It runs against a
hand-written fixture league in `tests/conftest.py` — deliberately synthetic, so
the repository carries nobody's member GUIDs or team names.

To also run the Postgres round-trip tests, point `ESPN_FANTASY_TEST_DSN` at a scratch
database. **They drop and recreate every table**, so do not aim it at one
holding a real league:

```bash
docker compose up -d
ESPN_FANTASY_TEST_DSN="postgresql://espn:PASSWORD@127.0.0.1:5434/ffl_scratch" pytest
```

## Adding an output format

1. Write a `Sink` subclass in `espn_fantasy/sinks/`. It receives the parsed
   `Dataset` and writes it; that is the whole interface.
2. Add it to the registry in `espn_fantasy/sinks/__init__.py`.
3. If it needs a package, add an extra in `pyproject.toml` and raise
   `MissingDependency` on the import — never let a missing package surface as
   a bare `ModuleNotFoundError`.
4. Add it to `FILE_FORMATS` in `tests/test_sinks.py`, which asserts that every
   format writes the same row count as every other.

Parsers and the raw store should not need to change.

## Adding a field

1. Add the column to the relevant `Table` in `espn_fantasy/tables.py`.
2. Populate it in `espn_fantasy/parse.py`.
3. Give it a type in `espn_fantasy/sinks/coltypes.py` unless plain text is
   right — the typed sinks share that one classification so their schemas
   cannot drift apart.
4. Add it to `espn_fantasy/schema/postgres.sql`, which is hand-written on
   purpose.

## Style

Match what is there. Comments explain *why*, especially where ESPN's API is
surprising — that is most of the value in this codebase, and most of the
comments earn their place by having cost somebody an afternoon.
