"""The end-of-run summary.

Printed from the parsed dataset rather than queried back out of a database, so
the same numbers appear whether the output was Postgres, a spreadsheet or a
directory of CSVs.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from typing import Any

from . import tables as T
from .parse import Dataset
from .store import RawStore

#: Tables interesting enough to break down per season in the summary.
PER_SEASON_TABLES = ("teams", "matchups", "roster_slots", "draft_picks",
                     "transactions")

RULE = "-" * 78


def print_summary(data: Dataset, store: RawStore | None = None,
                  stream: Any = None) -> None:
    out = stream or sys.stdout
    if data.is_empty():
        print("No data. Run `ffl-history fetch` first.", file=out)
        return

    _section(out, "ROWS PER SEASON")
    _per_season_counts(data, out)

    _section(out, "CHAMPIONS")
    _champions(data, out)

    _section(out, "ALL-TIME RECORDS")
    _member_records(data, out)

    _section(out, "DATA QUALITY")
    _quality(data, store, out)
    print(file=out)


def _section(out: Any, title: str) -> None:
    print(f"\n{title}\n{RULE}", file=out)


def _table(out: Any, headers: Sequence[str], rows: Iterable[Sequence[Any]],
           align_right: Sequence[bool] | None = None) -> None:
    materialized = [[("" if c is None else str(c)) for c in row] for row in rows]
    if not materialized:
        print("  (none)", file=out)
        return
    widths = [max(len(h), *(len(r[i]) for r in materialized))
              for i, h in enumerate(headers)]
    right = align_right or [False] * len(headers)

    def fmt(cells: Sequence[str]) -> str:
        return "  ".join(c.rjust(w) if right[i] else c.ljust(w)
                         for i, (c, w) in enumerate(zip(cells, widths, strict=False)))

    print("  " + fmt(headers), file=out)
    print("  " + "  ".join("-" * w for w in widths), file=out)
    for row in materialized:
        print("  " + fmt(row), file=out)


def _per_season_counts(data: Dataset, out: Any) -> None:
    seasons = sorted({row["season"] for row in data.rows(T.SEASONS)})
    counts = {
        name: _count_by_season(data.rows(name)) for name in PER_SEASON_TABLES
    }
    rows = [[season] + [counts[name].get(season, 0) for name in PER_SEASON_TABLES]
            for season in seasons]
    if len(seasons) > 1:
        rows.append(["total"] + [sum(counts[n].values()) for n in PER_SEASON_TABLES])
    headers = ["season", *PER_SEASON_TABLES]
    _table(out, headers, rows, [True] * len(headers))


def _count_by_season(rows: list[dict]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in rows:
        counts[row["season"]] = counts.get(row["season"], 0) + 1
    return counts


def _champions(data: Dataset, out: Any) -> None:
    rows = [
        [c["season"], c.get("champion") or "?", c.get("champion_member") or "",
         _score(c.get("champion_score")), _score(c.get("runner_up_score")),
         c.get("runner_up") or "?"]
        for c in sorted(data.rows(T.SEASON_CHAMPIONS), key=lambda r: r["season"])
    ]
    if not rows:
        print("  (no decided WINNERS_BRACKET matchups found -- an in-progress\n"
              "   season, or a league whose playoffs are not bracketed)", file=out)
        return
    _table(out, ["season", "champion", "owner", "score", "vs", "runner-up"],
           rows, [True, False, False, True, True, False])


def _member_records(data: Dataset, out: Any) -> None:
    rows = [
        [r.get("display_name") or r["member_id"], r["seasons"],
         f"{r['wins']}-{r['losses']}" + (f"-{r['ties']}" if r["ties"] else ""),
         f"{r['win_pct']:.3f}" if r.get("win_pct") is not None else "",
         _score(r.get("points_for")), r["titles"], r["playoff_appearances"]]
        for r in data.rows(T.MEMBER_RECORDS)
    ]
    _table(out, ["owner", "seasons", "record", "win%", "points for",
                 "titles", "playoffs"],
           rows, [False, True, True, True, True, True, True])


def _quality(data: Dataset, store: RawStore | None, out: Any) -> None:
    lines: list[str] = []

    if store is not None:
        ledger = store.fetch_log_summary()
        if ledger:
            lines.append("fetch ledger: "
                         + ", ".join(f"{s}={n}" for s, n in ledger))
            failures = sum(n for s, n in ledger if s == "error")
            if failures:
                lines.append(f"  {failures} unit(s) failed -- re-run to retry "
                             "only those")

    missing_draft = sum(1 for r in data.rows(T.DRAFT_PICKS)
                        if r.get("player_id") and not r.get("player_name"))
    missing_txn = sum(1 for r in data.rows(T.TRANSACTIONS)
                      if r.get("player_id") and not r.get("player_name"))
    if missing_draft or missing_txn:
        lines.append(
            f"unnamed players: {missing_draft} draft pick(s), {missing_txn} "
            "transaction row(s)")
        lines.append("  ESPN returns ids but no names in the draft and "
                     "transaction views; names")
        lines.append("  come from weekly rosters, so a player who was never "
                     "rostered has none.")

    header_only = {r["transaction_id"] for r in data.rows(T.TRANSACTIONS)
                   if r.get("type") == "TRADE_ACCEPT" and not r.get("has_items")}
    full_trades = {r["transaction_id"] for r in data.rows(T.TRADES)}
    if header_only or full_trades:
        lines.append(f"trades: {len(full_trades)} with full detail, "
                     f"{len(header_only)} header-only")
        if header_only:
            lines.append("  ESPN did not return the players involved in the "
                         "header-only trades;")
            lines.append("  they are known to have happened but their "
                         "contents are not recoverable.")

    lines.extend(_reconciliation(data))

    for line in lines or ["nothing to flag"]:
        print(f"  {line}", file=out)


def _reconciliation(data: Dataset) -> list[str]:
    """Does each team's matchup score equal the sum of its started lineup?

    Regular season only. In the playoffs a matchup period can span two scoring
    periods, so matchups.week and roster_slots.week are not comparable there.
    A mismatch means ESPN applied a stat correction after the fact.
    """
    lineup: dict[tuple[int, int, int], float] = {}
    for slot in data.rows(T.ROSTER_SLOTS):
        if not slot.get("started") or slot.get("actual_points") is None:
            continue
        key = (slot["season"], slot["week"], slot["team_id"])
        lineup[key] = lineup.get(key, 0.0) + slot["actual_points"]

    compared = off = 0
    worst = 0.0
    for matchup in data.rows(T.MATCHUPS):
        if matchup.get("playoff_tier_type") != "NONE" or matchup.get("is_bye"):
            continue
        for side in ("home", "away"):
            team_id = matchup.get(f"{side}_team_id")
            score = matchup.get(f"{side}_score")
            key = (matchup["season"], matchup.get("week"), team_id)
            if team_id is None or score is None or key not in lineup:
                continue
            compared += 1
            diff = abs(score - lineup[key])
            worst = max(worst, diff)
            if diff > 0.5:
                off += 1

    if not compared:
        return []
    if not off:
        return [f"points reconcile: all {compared} regular-season team-weeks "
                f"match their lineup sum (worst diff {worst:.2f})"]
    return [f"points reconcile: {compared - off}/{compared} regular-season "
            f"team-weeks match; worst diff {worst:.2f}",
            "  a mismatch usually means ESPN applied a stat correction after "
            "the fact"]


def _score(value: Any) -> str:
    return "" if value is None else f"{float(value):.2f}"
