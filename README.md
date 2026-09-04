# ESPN Fantasy League Data Fetcher

[![CI](https://github.com/StreamSeb/espn-fantasy-league-data-fetcher/actions/workflows/ci.yml/badge.svg)](https://github.com/StreamSeb/espn-fantasy-league-data-fetcher/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Pull your **entire ESPN fantasy football league history** — every season, every
week, every roster, draft pick and waiver bid — out of ESPN and into a format
you can actually use.

> I wanted to build my own stats page for our fantasy football league, and
> there was no good way to get the data out of ESPN. So I built this. It's the
> tool I wished existed — hopefully it saves you the afternoon it cost me.
>
> — Sebastian

```bash
espn-fantasy run
```

That's it. It works out which seasons your league has played, downloads them,
and writes a single SQLite file you can open with anything. No database to set
up, no configuration beyond your league id and two cookies.

Want CSVs instead? `--format csv`. A spreadsheet? `--format xlsx`. Postgres,
Parquet, JSON, DuckDB? All there. Pick as many as you like.

---

## What you get

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

ALL-TIME RECORDS
------------------------------------------------------------------------------
  owner  seasons  record   win%  points for  titles  playoffs
  sam          2    15-11  0.577     3204.18       1         2
  dana         2    14-12  0.538     3102.44       1         2
```

Twelve tables, ready to query:

| Table | One row per |
|---|---|
| `seasons` | year — settings, scoring rules, roster slots |
| `members` | person — keyed on ESPN's GUID, so it survives team renames |
| `teams` | team, per season |
| `team_owners` | co-owner (ESPN allows several per team) |
| `players` | player — name, position, pro team |
| `matchups` | scheduled game, with the playoff bracket tier |
| `roster_slots` | player, per team, per week — started, points, projection |
| `draft_picks` | pick, with `bid_amount` for auction leagues |
| `transactions` | waiver/free-agent *item* — including the failed bids |
| `season_champions` | season — who won, who they beat, final score |
| `member_records` | person — all-time W-L-T, points, titles, playoff berths |
| `trades` | trade leg, for the trades ESPN reports in full |

The last three are worked out for you, and they're **real tables in every
format** — so your CSV export has the all-time standings in it, not just the
raw ingredients.

---

## Getting started

### 1. Install

You need Python 3.10 or newer.

```bash
git clone https://github.com/StreamSeb/espn-fantasy-league-data-fetcher.git
cd espn-fantasy-league-data-fetcher
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Find your league id

It's in your league's URL:

```
https://fantasy.espn.com/football/league?leagueId=123456
                                                  ^^^^^^
```

### 3. Get your two cookies

Since August 2025, ESPN requires you to be logged in to read *any* season —
even your own, even ones that finished years ago. So the tool needs to borrow
your session.

1. Log in at [fantasy.espn.com](https://fantasy.espn.com).
2. Open developer tools (<kbd>F12</kbd>).
3. Go to **Application** (Chrome/Edge) or **Storage** (Firefox) →
   **Cookies** → `https://fantasy.espn.com`.
4. Copy these two:

| Cookie | Looks like | Watch out for |
|---|---|---|
| `espn_s2` | `AEB...%2F...` — very long | Paste it exactly. No quotes. |
| `SWID` | `{1A2B3C4D-...}` | **Keep the curly braces.** |

### 4. Save them

```bash
espn-fantasy init-env      # writes a .env file, locked to your user
```

Open `.env` and fill in the three values. Then:

```bash
espn-fantasy doctor        # checks everything before a long download
espn-fantasy run           # go
```

<details>
<summary>What <code>doctor</code> looks like</summary>

```
$ espn-fantasy doctor
settings
  league_id    123456
  espn_s2      AEBG...3D (326 chars)
  swid         {1A2...D} (38 chars)
  seasons      auto (discover from ESPN)

checks
  [ok]   league id and cookies are present and well-formed
  [ok]   output format 'sqlite' is available
  [ok]   raw store reachable (sqlite), 0 season(s) stored
  [ok]   ESPN reachable; seasons to fetch: 2019, 2020, 2021, 2022, 2023, 2024
ready
```
</details>

**Cookies expire every few months.** When they do, the run stops immediately
with a clear message instead of hammering ESPN. Paste in fresh ones and run
again — everything already downloaded is skipped.

---

## Choosing what to download

By default it downloads **every season your league has ever played**, worked
out by asking ESPN. To narrow it:

```bash
espn-fantasy run --seasons 2023             # one year
espn-fantasy run --seasons 2018-2024        # a range
espn-fantasy run --seasons 2019,2021,2024   # specific years
espn-fantasy run --seasons 2020-2022,2024   # mix and match
```

2018 is the earliest year ESPN serves through this API.

Weeks are handled for you: each season stops at the last week actually played.
That matters more than it sounds — **ask ESPN about a week that hasn't happened
yet and it hands back today's roster, not an empty one.** Without the cap, a
season still in progress would record this week's lineups over and over, once
per remaining week, as if they were real results. Override it if you need to:

```bash
espn-fantasy run --max-week 14       # stop at week 14
espn-fantasy run --weeks 15-17       # only these weeks
espn-fantasy run --delay 2.0         # be gentler on ESPN (default: 1s)
```

Downloads resume. Every request is logged, so a re-run skips what you already
have and retries only what failed. Ctrl-C is safe.

---

## Choosing the output

Repeat `--format` for as many as you want — they all contain the same data.

| `--format` | You get | Extra install |
|---|---|---|
| `sqlite` | `out/espn_fantasy.sqlite3` — one file, opens anywhere | *default* |
| `csv` | `out/csv/<table>.csv` | — |
| `json` | `out/json/<table>.json` | — |
| `jsonl` | `out/jsonl/<table>.jsonl` — one object per line | — |
| `xlsx` | `out/espn_fantasy.xlsx` — a sheet per table | `.[excel]` |
| `parquet` | `out/parquet/<table>.parquet` | `.[parquet]` |
| `duckdb` | `out/espn_fantasy.duckdb` | `.[duckdb]` |
| `postgres` | Tables in Postgres 15+ | `.[postgres]` |
| `raw` | `out/raw/<season>/<view>.json` — ESPN's untouched replies | — |

```bash
espn-fantasy run --format csv --format xlsx
pip install -e '.[parquet]'
espn-fantasy export --format parquet     # no re-downloading
```

That last line is the point of the design: **`export` never touches the
network.** Every ESPN reply is saved before anything is interpreted, so
changing your mind about the format, or fixing a parser bug, costs nothing.

The `raw` format is worth knowing about. It's the one output that doesn't
depend on this tool — a folder of ESPN's original JSON. If this project
disappears and ESPN changes the API again, that folder is still readable.

---

## Actually using the data

Twelve ready-made queries are in [`examples/queries.sql`](examples/queries.sql)
— best single week ever, points left on the bench, biggest waiver bids, draft
value, head-to-head records. They run unchanged on both SQLite and Postgres:

```bash
sqlite3 out/espn_fantasy.sqlite3 < examples/queries.sql
```

Or just poke around:

```bash
sqlite3 out/espn_fantasy.sqlite3
sqlite> SELECT * FROM member_records ORDER BY titles DESC;
```

Python:

```python
import sqlite3, pandas as pd
db = sqlite3.connect("out/espn_fantasy.sqlite3")
pd.read_sql("SELECT * FROM roster_slots WHERE started", db)
```

---

## Using Postgres instead

Point it at any Postgres 15 or newer:

```bash
export DATABASE_URL='postgresql://user:pass@host:5432/dbname'
espn-fantasy run --format postgres
```

Or use the bundled one. It runs on port 5434, bound to localhost, in its own
container and volume, so it can't collide with any Postgres you already have:

```bash
cp .env.example .env      # set POSTGRES_PASSWORD
docker compose up -d
espn-fantasy run --format postgres
```

---

## Things ESPN's API does that will confuse you

Each of these cost real debugging. They're handled automatically — this is
here so the data makes sense, and to save you the same afternoon.

<details>
<summary><b>Future weeks return today's roster, not an empty one</b></summary>

Ask for week 14 in September and ESPN cheerfully returns the current lineup.
Download all 18 weeks of a live season and you'd get 12 identical copies of
today's rosters recorded as history. Weeks are capped at what's been played.
</details>

<details>
<summary><b>Playoff scores look impossibly high</b></summary>

`matchups.week` is a *matchup period*; `roster_slots.week` is an *NFL week*.
They're the same during the regular season, but a playoff matchup often spans
two NFL weeks — so a championship shows ~250–390 points, not ~130.

**Only join `matchups.week` to `roster_slots.week` where
`playoff_tier_type = 'NONE'`.**
</details>

<details>
<summary><b>Most trades can't be recovered</b></summary>

ESPN returns most `TRADE_ACCEPT` records as a bare header — it confirms a trade
happened but not what was in it. Nothing is invented to fill the gap: those
stay in `transactions` with `has_items = false`. The minority ESPN does report
properly are flattened into the `trades` table.
</details>

<details>
<summary><b>Failed waiver claims are kept on purpose</b></summary>

`status` includes `FAILED_ROSTERLIMIT`, `CANCELED`, `PENDING` and friends. A
failed claim is still league history — it records who bid what on whom.
**Filter on `status = 'EXECUTED'`** when you only want moves that happened.
</details>

<details>
<summary><b>Some player names are blank</b></summary>

Draft and transaction records carry player ids but no names. Names come from
weekly rosters — so someone drafted and cut before ever being rostered keeps
their id and has no name. That's ESPN's gap, not a bug. The summary counts
them.
</details>

<details>
<summary><b>Two id systems that look interchangeable and aren't</b></summary>

`lineupSlotId` (where a player was slotted) and `defaultPositionId` (what he
actually is) are different scales. Mixing them up relabels every player on the
board — QBs become "TQB", kickers become "WR/TE".
</details>

<details>
<summary><b>Boxscores arrive in two different shapes</b></summary>

`teams[].roster.entries` in some seasons, `schedule[].home/away.
rosterForCurrentScoringPeriod.entries` in others. Both are read.
</details>

<details>
<summary><b>Transactions silently return nothing without a week</b></summary>

Request `mTransactions2` without `scoringPeriodId` and ESPN returns HTTP 200
with the data simply missing. No error. It has to be fetched a week at a time.
</details>

---

## Keeping your cookies safe

`espn_s2` and `SWID` are your live ESPN session. **Anyone who has both can act
as you on ESPN.** Treat them like a password.

- `.env` is gitignored, and `init-env` creates it readable only by you.
- A **pre-commit hook** blocks committing them by accident. Turn it on once:
  ```bash
  git config core.hooksPath .githooks
  ```
  It refuses `.env`, refuses exported league data, and catches a cookie pasted
  into any other file — including one force-added past `.gitignore`. If you
  need a well-formed fake in docs or a test, end the line with
  `allowlist secret`.
- Never paste them into an issue, a screenshot, or a gist.
- Prefer `.env` over `--espn-s2` / `--swid`, which land in your shell history.
- Nothing is ever logged: `doctor` prints them masked.

Leaked them? Log out of ESPN everywhere — that kills the session.

---

## Commands

| Command | Does |
|---|---|
| `espn-fantasy run` | Download, parse, write. The one you'll use. |
| `espn-fantasy fetch` | Download only. |
| `espn-fantasy parse` | Rebuild outputs from what's downloaded. **Offline.** |
| `espn-fantasy export` | Same as `parse` — for adding a format later. |
| `espn-fantasy summary` | Print the summary, write nothing. |
| `espn-fantasy doctor` | Check settings and connectivity, then stop. |
| `espn-fantasy init-env` | Create your `.env`. |

`python -m espn_fantasy` and `python extract.py` do the same thing.
`espn-fantasy <command> --help` explains every flag.

---

## Contributing

Bug reports and pull requests welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

```bash
pip install -e '.[dev]'
git config core.hooksPath .githooks
pytest          # no network, no database needed
ruff check .
```

The tests run against a small made-up league defined in `tests/conftest.py`,
not captured traffic, so this repo carries nobody's real data.

<details>
<summary>How the code is laid out</summary>

```
espn_fantasy/
  cli.py       the commands
  config.py    defaults < .env < environment < flags
  espn.py      talking to ESPN: retries, season discovery
  parse.py     ESPN's JSON -> plain rows. No network, no database.
  derive.py    champions, all-time records, trades
  tables.py    the column list every output format shares
  report.py    the summary you see at the end
  store/       where raw replies are kept (sqlite, postgres)
  sinks/       one small file per output format
  schema/      hand-written Postgres DDL
```

Adding an output format means writing one class in `sinks/`. The parser and
the downloader don't change.
</details>

---

## Licence

MIT — see [LICENSE](LICENSE). Do what you like with it.

Not affiliated with, endorsed by, or connected to ESPN or Disney. It reads the
same endpoints the ESPN fantasy website uses, with your own login, for your own
league. Keep the request delay sensible and don't point it at leagues that
aren't yours.
