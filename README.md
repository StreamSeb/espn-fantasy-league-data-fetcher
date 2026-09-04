# ffl-history

Export an **ESPN fantasy football league's entire history** — every season,
every week, every roster, draft pick and waiver bid — into whatever you
actually want to work in: a SQLite file, Postgres, CSV, JSON, Parquet, a
spreadsheet, DuckDB, or the raw ESPN payloads themselves.

```bash
pip install -e .
ffl-history init-env          # writes a .env template (mode 600)
# fill in ESPN_LEAGUE_ID, ESPN_S2 and SWID, then:
ffl-history run
```

That discovers which seasons your league has, fetches them, and writes
`out/ffl_history.sqlite3`. Nothing else needs installing — SQLite ships with
Python.

```
ROWS PER SEASON
------------------------------------------------------------------------------
  season  teams  matchups  roster_slots  draft_picks  transactions
    2021     10        70          1830          160           612
    2022     10        70          1840          160           733
   total     20       140          3670          320          1345

CHAMPIONS
------------------------------------------------------------------------------
  season  champion            owner     score      vs  runner-up
    2021  Team Roster Tetris  dana     142.68  131.02  The Waiver Wire
    2022  Fourth And Long     sam      160.14  151.70  Team Roster Tetris
```

---

## Contents

- [Why this exists](#why-this-exists)
- [Install](#install)
- [Getting your ESPN credentials](#getting-your-espn-credentials)
- [Usage](#usage)
- [Choosing seasons and weeks](#choosing-seasons-and-weeks)
- [Output formats](#output-formats)
- [What you get](#what-you-get)
- [Postgres](#postgres)
- [Things ESPN's API will do to you](#things-espns-api-will-do-to-you)
- [Security](#security)
- [Development](#development)
- [Licence and disclaimer](#licence-and-disclaimer)

---

## Why this exists

ESPN deletes old league data, and reshapes this API roughly once a year. If
your league's history matters to you, the only safe copy is one you hold.

This tool is built around that. **Every response is written to a raw store
before anything is parsed**, and the normalized tables are then built *by
reading those payloads back out again* — never from the response still in
memory. That has two consequences worth knowing about:

- Re-parsing is free and offline. Add an output format, fix a parser bug, or
  change your mind about what you want, and you never ask ESPN for anything
  twice.
- The re-parse path is the *only* path, so it is exercised on every run rather
  than being a feature that quietly rots.

## Install

Python 3.10 or newer.

```bash
git clone https://github.com/OWNER/ffl-history.git
cd ffl-history
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

The default SQLite/CSV/JSON output needs nothing more. Other formats pull in
one dependency each:

```bash
pip install -e '.[parquet]'    # --format parquet
pip install -e '.[excel]'      # --format xlsx
pip install -e '.[duckdb]'     # --format duckdb
pip install -e '.[postgres]'   # --format postgres
pip install -e '.[all]'        # all of them
```

## Getting your ESPN credentials

You need three things.

**1. Your league id.** It is the `leagueId=` parameter in your league's URL:

```
https://fantasy.espn.com/football/league?leagueId=123456
                                                  ^^^^^^
```

**2 and 3. Two cookies.** Since August 2025 ESPN requires authentication on
historical seasons — even for a league you own, and even for seasons that
finished years ago.

In a browser logged in to ESPN, open developer tools and go to
**Application** (Chrome/Edge) or **Storage** (Firefox) → **Cookies** →
`https://fantasy.espn.com`, then copy:

| Cookie | Looks like | Notes |
|---|---|---|
| `espn_s2` | `AEB...%2F...` (very long) | URL-encoded. Paste verbatim, no quotes. |
| `SWID` | `{1A2B3C4D-...}` | **Include the braces.** |

Put them in `.env` (`ffl-history init-env` writes the template), or export them
as environment variables, or pass `--espn-s2` / `--swid`.

They expire every few months. When they do, requests start failing with an
access-denied error and the run stops immediately rather than hammering ESPN
several hundred times. Refresh both values and run again — everything already
fetched is skipped.

Run `ffl-history doctor` to check all of this before a long fetch:

```
$ ffl-history doctor
settings
  league_id    123456
  espn_s2      AEBG...3D (326 chars)
  swid         {1A2...D} (38 chars)
  seasons      auto (discover from ESPN)
  ...
checks
  [ok]   league id and cookies are present and well-formed
  [ok]   output format 'sqlite' is available
  [ok]   raw store reachable (sqlite), 0 season(s) stored
  [ok]   ESPN reachable; seasons to fetch: 2019, 2020, 2021, 2022, 2023, 2024
ready
```

## Usage

| Command | What it does |
|---|---|
| `ffl-history run` | Fetch anything missing, parse it, write every format. The usual one. |
| `ffl-history fetch` | Fetch raw payloads only. |
| `ffl-history parse` | Rebuild outputs from stored payloads. **Makes no requests.** |
| `ffl-history export` | Alias for `parse`, for when you just want another format. |
| `ffl-history summary` | Print the summary, write nothing. |
| `ffl-history doctor` | Check settings, dependencies and connectivity, then stop. |
| `ffl-history init-env` | Write a `.env` template. |

`python -m ffl_history` and `python extract.py` are equivalent to
`ffl-history`.

**Resuming.** Every `(season, view, week)` unit of work is recorded in a
ledger. A re-run skips anything already stored and retries only failures, so
it is safe to interrupt with Ctrl-C and start again. Every write is an upsert
on a natural key, so re-running never duplicates a row.

## Choosing seasons and weeks

```bash
ffl-history run                            # every season ESPN says you have
ffl-history run --seasons 2023             # one season
ffl-history run --seasons 2018-2024        # a range
ffl-history run --seasons 2019,2021,2024   # a list
ffl-history run --seasons 2020-2022,2024   # both
```

With no `--seasons`, the tool asks ESPN which seasons the league actually has
(`status.previousSeasons`, plus the current year if it is under way) and
fetches all of them. **2018 is the earliest year this API shape serves.**

Weeks are capped automatically, per season, at the last week actually played.
That matters more than it sounds: **ESPN answers a request for a future week
with the current roster, not an empty one.** Fetching all 18 weeks of an
in-progress season would otherwise record today's lineups once per remaining
week, as though they were real weekly results. Override with:

```bash
ffl-history run --max-week 14     # never request past week 14
ffl-history run --weeks 15-17     # only these weeks
```

Be polite to an API with no published quota:

```bash
ffl-history run --delay 2.0       # seconds between requests (default 1.0)
ffl-history run --retries 5       # attempts per request (default 3)
```

## Output formats

Repeat `--format` for as many as you want. They all contain the same rows.

| `--format` | Output | Needs |
|---|---|---|
| `sqlite` | `out/ffl_history.sqlite3` — one portable file | *(default)* |
| `csv` | `out/csv/<table>.csv` | |
| `json` | `out/json/<table>.json` — one array per table | |
| `jsonl` | `out/jsonl/<table>.jsonl` — streams; BigQuery-ready | |
| `parquet` | `out/parquet/<table>.parquet` — zstd, typed | `[parquet]` |
| `xlsx` | `out/ffl_history.xlsx` — a sheet per table | `[excel]` |
| `duckdb` | `out/ffl_history.duckdb` | `[duckdb]` |
| `postgres` | Tables in Postgres 15+ | `[postgres]` |
| `raw` | `out/raw/<season>/<view>.json` — untouched ESPN payloads | |

```bash
ffl-history run --format csv --format xlsx
ffl-history export --format parquet          # add one later, no re-fetching
```

Set `--out DIR` to write somewhere other than `out/`.

The `raw` format deserves a note: it is the one output that does not depend on
this tool. A directory of original ESPN JSON is the durable backup — whatever
happens to the endpoint, or to this project, someone can write a parser
against it in ten years.

## What you get

Nine tables parsed from ESPN, and three computed for you.

| Table | Grain |
|---|---|
| `seasons` | One row per year: settings, roster slots, scoring rules, and the full settings blob |
| `members` | People, keyed on the ESPN member GUID — follows someone across team renames |
| `teams` | One row per team per season |
| `team_owners` | Co-ownership; ESPN allows several owners per team |
| `players` | Player dimension: name, position, pro team |
| `matchups` | One row per scheduled game, with the playoff bracket tier |
| `roster_slots` | season × week × team × player: slot, started, actual and projected points |
| `draft_picks` | The draft board, with `bid_amount` for auction leagues |
| `transactions` | One row per transaction *item* (ESPN models a transaction as a header plus an `items[]` array) |
| **`season_champions`** | *Derived.* Each season's champion, runner-up and final score |
| **`member_records`** | *Derived.* All-time record per person: W-L-T, win%, points, titles, playoff appearances |
| **`trades`** | *Derived.* Trade legs, flattened, for the trades ESPN reported in full |

The three derived tables are real tables in **every** format, not SQL views —
so the CSV and Parquet exports carry them too.

`examples/queries.sql` has a dozen worked queries — best week ever, points left
on the bench, biggest waiver bids, draft value, head-to-head — written to run
unchanged against both SQLite and Postgres:

```bash
sqlite3 out/ffl_history.sqlite3 < examples/queries.sql
```

## Postgres

Bring your own server:

```bash
export DATABASE_URL='postgresql://user:pass@host:5432/dbname'
ffl-history run --format postgres
```

…or use the bundled stack, which is isolated enough that it cannot disturb any
Postgres you already run — its own compose project, container, volume, network,
and port 5434 bound to loopback only:

```bash
cp .env.example .env      # set POSTGRES_PASSWORD
docker compose up -d
docker compose ps         # wait for "healthy"
ffl-history run --format postgres
```

**Postgres 15 or newer is required** (the raw store relies on
`NULLS NOT DISTINCT`). `ffl_history/schema/postgres.sql` is the hand-written
DDL, with types, foreign keys and comments; it is applied on every run and
every statement is `IF NOT EXISTS`, so it doubles as the migration step.

You can also keep the raw payloads in Postgres rather than SQLite, with
`--raw-store postgres`.

## Things ESPN's API will do to you

Documented here because each one cost real debugging.

**`mTransactions2` silently returns nothing without `scoringPeriodId`.** No
error, no empty array — HTTP 200 with the `transactions` key simply absent. It
has to be fetched one week at a time, which is why it sits with the weekly
views rather than the season-level ones.

**Future weeks return today's roster.** See
[Choosing seasons and weeks](#choosing-seasons-and-weeks). This is handled
automatically, but it is the single easiest way to corrupt this dataset by
hand.

**`lineupSlotId` and `defaultPositionId` are different id spaces.** Running a
`defaultPositionId` through the lineup-slot map mislabels every player on the
board — QBs become `TQB`, WRs become `RB/WR`, kickers become `WR/TE`. They look
interchangeable and are not.

**Playoff matchup periods can span two scoring periods.** `matchups.week` is a
`matchupPeriodId`; `roster_slots.week` is a `scoringPeriodId`. They coincide in
the regular season but not in the playoffs, where one matchup period often
covers two NFL weeks — which is why championship scores can look like ~250–390
instead of ~130. **Only join `matchups.week` to `roster_slots.week` where
`playoff_tier_type = 'NONE'`.**

**`mBoxscore` comes back in two different shapes**, `teams[].roster.entries`
and `schedule[].home/away.rosterForCurrentScoringPeriod.entries`, and which one
you get varies by season. Both are parsed, and rows are de-duplicated on the
natural key.

**Most trades are not recoverable.** ESPN returns the majority of
`TRADE_ACCEPT` rows as a bare header with an empty `items[]` — it confirms the
trade happened but not what was in it. Those rows are stored as-is with
`has_items = false`; nothing is reconstructed or guessed. The minority ESPN
does report in full are flattened into `trades`.

**Failed and pending transactions are kept on purpose.** `status` includes
`FAILED_ROSTERLIMIT`, `CANCELED`, `PENDING` and friends alongside `EXECUTED`.
A failed waiver claim is still league history — it records who bid what on
whom. **Filter on `status = 'EXECUTED'` for moves that actually happened.**

**Some player names are missing, and that is ESPN's gap, not a bug.**
`mDraftDetail` and `mTransactions2` return player ids but no names. Names come
from the `players` dimension, which is built from weekly rosters — so a player
who was drafted or claimed but never appeared on a roster keeps his
`player_id` and has a NULL `player_name`. The run summary counts these.

**Team names lost their structure in 2023.** ESPN dropped the split
`location`/`nickname` fields for a single `name`. Both are read, so team names
survive across the boundary.

**Points should reconcile exactly.** The summary checks every regular-season
team-week against the sum of its started lineup. A mismatch means ESPN applied
a stat correction after the fact — worth knowing about, not necessarily worth
fixing.

## Security

**`.env` holds session cookies for your ESPN account.** Anyone who has
`espn_s2` and `SWID` can act as you on ESPN.

- `.env` is gitignored and `init-env` writes it mode 600. Keep it that way.
- Never paste those values into an issue, a pull request, a screenshot or a
  gist.
- Prefer environment variables or `.env` over `--espn-s2` / `--swid`, which
  land in your shell history.
- `out/` is gitignored too. It is your league's data, not the tool's.
- The tool never logs cookie values; `doctor` prints them masked.

If you think you have leaked them, log out of ESPN everywhere — that
invalidates the session the cookies represent.

## Development

```bash
pip install -e '.[dev]'
pytest                 # no network, no database required
ruff check .
```

The test suite runs against a hand-written fixture league in
`tests/conftest.py` rather than captured traffic, so it carries nobody's GUIDs
or team names, and an edge case can be added by writing it down instead of
waiting for it to happen.

To also run the Postgres round-trip tests, point `FFL_TEST_DSN` at a **scratch
database** — the tests drop and recreate every table:

```bash
docker compose up -d
FFL_TEST_DSN="postgresql://ffl:PASSWORD@127.0.0.1:5434/ffl_scratch" pytest
```

Layout:

```
ffl_history/
  cli.py          argparse; the commands above
  config.py       defaults < .env < environment < flags
  espn.py         transport, retries, season discovery
  parse.py        payloads -> plain row dicts. No network, no database.
  derive.py       champions, trades, all-time records
  tables.py       the canonical column list every sink shares
  report.py       the run summary
  store/          raw payload storage: sqlite.py, postgres.py
  sinks/          one small module per output format
  schema/         hand-written Postgres DDL
```

Adding an output format means writing one class in `sinks/` and adding it to
the registry — the parsers and the store do not change.

## Licence and disclaimer

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or connected to ESPN or the Walt Disney
Company. It reads the same undocumented endpoints the ESPN fantasy web app
uses, with your own credentials, for your own league's data. Use it in line
with ESPN's terms of service, keep the request delay reasonable, and do not
point it at leagues that are not yours.
