"""Turn stored ESPN payloads into plain row dicts.

Nothing here touches the network or a database. Parsers take payloads in and
hand rows out, which is what lets the same parsed result be written to
Postgres, SQLite, CSV, Parquet or a spreadsheet without a second code path -
and what makes the parsers testable against a captured fixture.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

from espn_api.football.constant import POSITION_MAP, PRO_TEAM_MAP

from . import tables as T
from .espn import BOXSCORE_VIEW, TRANSACTIONS_VIEW
from .store import RawStore

log = logging.getLogger(__name__)

# ESPN uses TWO different id spaces that are easy to conflate:
#
#   lineupSlotId      -> where a player was slotted (QB/RB/FLEX/BE/IR...).
#                        espn_api's POSITION_MAP covers this one.
#   defaultPositionId -> what the player actually IS.
#
# They are NOT the same scale. Running defaultPositionId through POSITION_MAP
# silently mislabels every player: QBs come out as "TQB", WRs as "RB/WR",
# TEs as "WR", kickers as "WR/TE". Hence this explicit map.
DEFAULT_POSITION_MAP = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K",
    7: "P", 9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S",
    14: "DB", 15: "DL", 16: "D/ST",
}

#: Lineup slots that are not "started": bench, injured reserve, and the
#: rarely-used ER slot.
BENCH_SLOTS = frozenset({20, 21, 24})


class Dataset:
    """Parsed rows, keyed by table name and de-duplicated on the natural key.

    De-duplication has to happen here rather than in each sink: ESPN payloads
    repeat entities freely (one player can appear under both the teams[] and
    schedule[] shapes of a single boxscore), and Postgres rejects an
    ON CONFLICT DO UPDATE that would touch the same row twice in one statement.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict[tuple, dict[str, Any]]] = {
            t.name: {} for t in T.ALL_TABLES}

    def add(self, table: T.Table, row: dict[str, Any]) -> None:
        self._rows[table.name][tuple(row.get(k) for k in table.key)] = row

    def extend(self, table: T.Table, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            self.add(table, row)

    def merge(self, table: T.Table, row: dict[str, Any]) -> None:
        """Add a row, keeping any non-null value already recorded.

        Used for dimensions built opportunistically from several views, where
        one payload may know a player's name and another only their id.
        """
        key = tuple(row.get(k) for k in table.key)
        existing = self._rows[table.name].get(key)
        if existing is None:
            self._rows[table.name][key] = row
            return
        for col, value in row.items():
            if value is not None:
                existing[col] = value

    def widen(self, table: T.Table, key: Any, season: int) -> None:
        """Stretch a row's first_seen/last_seen span to include `season`."""
        row = self._rows[table.name][(key,)]
        first, last = row.get("first_seen_season"), row.get("last_seen_season")
        row["first_seen_season"] = season if first is None else min(first, season)
        row["last_seen_season"] = season if last is None else max(last, season)

    def rows(self, table: T.Table | str) -> list[dict[str, Any]]:
        name = table if isinstance(table, str) else table.name
        return list(self._rows[name].values())

    def replace(self, table: T.Table, rows: Iterable[dict[str, Any]]) -> None:
        self._rows[table.name] = {}
        self.extend(table, rows)

    def counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self._rows.items()}

    def is_empty(self) -> bool:
        return not any(self._rows.values())

    def __iter__(self) -> Iterator[tuple[T.Table, list[dict[str, Any]]]]:
        for table in T.ALL_TABLES:
            yield table, self.rows(table)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def _ts(epoch_ms: Any) -> datetime | None:
    if not epoch_ms:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _num(value: Any) -> float | None:
    """Reject bools: in Python `True` is an int, and ESPN does put flags in
    numeric-looking fields."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-view parsers
# ---------------------------------------------------------------------------

def parse_season_settings(data: Dataset, season: int, payload: dict | None,
                          league_id: int) -> None:
    payload = payload or {}
    settings = payload.get("settings") or {}
    schedule_s = settings.get("scheduleSettings") or {}
    roster_s = settings.get("rosterSettings") or {}
    scoring_s = settings.get("scoringSettings") or {}
    draft_s = settings.get("draftSettings") or {}
    status = payload.get("status") or {}

    data.add(T.SEASONS, {
        "season": season,
        "league_id": _int(payload.get("id")) or league_id,
        "league_name": settings.get("name"),
        "team_count": settings.get("size"),
        "scoring_type": scoring_s.get("scoringType"),
        "playoff_team_count": schedule_s.get("playoffTeamCount"),
        "regular_season_length": schedule_s.get("matchupPeriodCount"),
        "matchup_period_count": schedule_s.get("matchupPeriodCount"),
        "final_scoring_period": status.get("finalScoringPeriod"),
        "roster_slot_counts": roster_s.get("lineupSlotCounts"),
        "scoring_settings": scoring_s.get("scoringItems"),
        "draft_type": draft_s.get("type"),
        "settings_raw": settings or None,
    })


def parse_teams(data: Dataset, season: int, payload: dict | None) -> None:
    payload = payload or {}

    for member in payload.get("members") or []:
        if not member.get("id"):
            continue
        data.merge(T.MEMBERS, {
            "member_id": member["id"],
            "display_name": member.get("displayName"),
            "first_name": member.get("firstName"),
            "last_name": member.get("lastName"),
        })
        # Widen the span rather than overwriting it: seasons are parsed in
        # ascending order, so a plain merge would keep only the last one.
        data.widen(T.MEMBERS, member["id"], season)

    for team in payload.get("teams") or []:
        record = (team.get("record") or {}).get("overall") or {}
        owners = [o for o in (team.get("owners") or []) if o]
        # ESPN dropped the split location/nickname fields in 2023 in favour of
        # a single `name`; support both so team names survive across seasons.
        location, nickname = team.get("location"), team.get("nickname")
        name = (team.get("name")
                or " ".join(p for p in (location, nickname) if p)
                or None)
        data.add(T.TEAMS, {
            "season": season,
            "team_id": team.get("id"),
            "member_id": owners[0] if owners else None,
            "team_name": name,
            "location": location,
            "nickname": nickname,
            "abbrev": team.get("abbrev"),
            "logo_url": team.get("logo"),
            "wins": record.get("wins"),
            "losses": record.get("losses"),
            "ties": record.get("ties"),
            "points_for": _num(record.get("pointsFor")),
            "points_against": _num(record.get("pointsAgainst")),
            "playoff_seed": team.get("playoffSeed"),
            "final_rank": team.get("rankCalculatedFinal"),
            "regular_season_rank": team.get("rankFinal"),
            "waiver_rank": team.get("waiverRank"),
        })
        for owner in owners:
            data.add(T.TEAM_OWNERS, {"season": season, "team_id": team.get("id"),
                                     "member_id": owner})


def parse_matchups(data: Dataset, season: int, payload: dict | None) -> None:
    for matchup in (payload or {}).get("schedule") or []:
        home = matchup.get("home") or {}
        away = matchup.get("away") or {}
        data.add(T.MATCHUPS, {
            "season": season,
            "espn_matchup_id": matchup.get("id"),
            "week": matchup.get("matchupPeriodId"),
            "home_team_id": home.get("teamId"),
            "away_team_id": away.get("teamId"),
            "home_score": _num(home.get("totalPoints")),
            "away_score": _num(away.get("totalPoints")),
            "home_tiebreak": _num(home.get("tiebreak")),
            "away_tiebreak": _num(away.get("tiebreak")),
            "winner": matchup.get("winner"),
            "playoff_tier_type": matchup.get("playoffTierType"),
            # A bye appears as a matchup with no away side.
            "is_bye": not bool(away),
        })


def parse_draft(data: Dataset, season: int, payload: dict | None) -> None:
    detail = (payload or {}).get("draftDetail") or {}
    for pick in detail.get("picks") or []:
        if pick.get("overallPickNumber") is None:
            continue
        auto_type = pick.get("autoDraftTypeId")
        data.add(T.DRAFT_PICKS, {
            "season": season,
            "overall_pick": pick.get("overallPickNumber"),
            "round": pick.get("roundId"),
            "round_pick": pick.get("roundPickNumber"),
            "team_id": pick.get("teamId"),
            "player_id": pick.get("playerId"),
            "player_name": None,          # backfilled from players
            "bid_amount": _num(pick.get("bidAmount")),
            "keeper": pick.get("keeper"),
            "nominating_team_id": pick.get("nominatingTeamId"),
            "auto_draft_type": None if auto_type is None else str(auto_type),
        })


def _iter_rosters(payload: dict) -> Iterable[tuple[int | None, list]]:
    """Yield (team_id, entries) across both shapes mBoxscore comes back in.

    Which shape ESPN returns varies by season, so both are read.
    """
    for team in payload.get("teams") or []:
        entries = ((team.get("roster") or {}).get("entries")) or []
        if entries:
            yield team.get("id"), entries
    for matchup in payload.get("schedule") or []:
        for side in ("home", "away"):
            data = matchup.get(side) or {}
            entries = ((data.get("rosterForCurrentScoringPeriod") or {})
                       .get("entries")) or []
            if entries:
                yield data.get("teamId"), entries


def _split_points(stats: list | None, week: int) -> tuple[float | None, float | None]:
    """statSourceId 0 = actual, 1 = ESPN projection, for this scoring period."""
    actual = projected = None
    for stat in stats or []:
        if stat.get("scoringPeriodId") != week:
            continue
        if stat.get("statSourceId") == 0:
            actual = _num(stat.get("appliedTotal"))
        elif stat.get("statSourceId") == 1:
            projected = _num(stat.get("appliedTotal"))
    return actual, projected


def parse_boxscore(data: Dataset, season: int, week: int,
                   payload: dict | None, known_teams: set[int]) -> None:
    for team_id, entries in _iter_rosters(payload or {}):
        if team_id not in known_teams:
            continue
        for entry in entries:
            pool = entry.get("playerPoolEntry") or {}
            player = pool.get("player") or {}
            player_id = player.get("id") or entry.get("playerId")
            if player_id is None:
                continue
            slot_id = entry.get("lineupSlotId")
            actual, projected = _split_points(player.get("stats"), week)
            position = DEFAULT_POSITION_MAP.get(player.get("defaultPositionId"))
            data.merge(T.PLAYERS, {
                "player_id": player_id,
                "full_name": player.get("fullName"),
                "default_position_id": player.get("defaultPositionId"),
                "position": position,
                "pro_team_id": player.get("proTeamId"),
                "pro_team": PRO_TEAM_MAP.get(player.get("proTeamId")),
            })
            data.add(T.ROSTER_SLOTS, {
                "season": season, "week": week, "team_id": team_id,
                "player_id": player_id,
                "player_name": player.get("fullName"),
                "lineup_slot_id": slot_id,
                "lineup_slot": POSITION_MAP.get(slot_id),
                "started": slot_id is not None and slot_id not in BENCH_SLOTS,
                "actual_points": actual,
                "projected_points": projected,
                "position": position,
                "pro_team_id": player.get("proTeamId"),
            })


def parse_transactions(data: Dataset, season: int, week: int,
                       payload: dict | None) -> None:
    """One row per transaction *item*; mTransactions2 is stored per week.

    Rows with a FAILED_* status are kept deliberately - a failed waiver claim
    is part of the league's history, since it records who bid what on whom.
    Filter on status = 'EXECUTED' for moves that actually happened.
    """
    for txn in (payload or {}).get("transactions") or []:
        base = {
            "season": season,
            "transaction_id": str(txn.get("id")),
            "type": txn.get("type"),
            "status": txn.get("status"),
            "execution_type": txn.get("executionType"),
            "team_id": txn.get("teamId"),
            "bid_amount": _num(txn.get("bidAmount")),
            "scoring_period": txn.get("scoringPeriodId", week),
            "proposed_at": _ts(txn.get("proposedDate")),
            "processed_at": _ts(txn.get("processDate")),
            "member_id": txn.get("memberId"),
            "related_transaction_id": txn.get("relatedTransactionId"),
            "is_pending": txn.get("isPending"),
        }
        items = txn.get("items") or []
        if not items:
            # Most TRADE_ACCEPT rows land here: ESPN returns the header with
            # no items, so there is nothing to reconstruct. Stored header-only
            # on purpose rather than dropped or guessed at.
            data.add(T.TRANSACTIONS, {
                **base, "item_index": 0, "item_type": None,
                "from_team_id": None, "to_team_id": None, "player_id": None,
                "player_name": None, "from_lineup_slot_id": None,
                "to_lineup_slot_id": None, "is_keeper": None,
                "has_items": False,
            })
            continue
        for index, item in enumerate(items):
            data.add(T.TRANSACTIONS, {
                **base, "item_index": index,
                "item_type": item.get("type"),
                "from_team_id": item.get("fromTeamId"),
                "to_team_id": item.get("toTeamId"),
                "player_id": item.get("playerId"),
                "player_name": None,       # backfilled from players
                "from_lineup_slot_id": item.get("fromLineupSlotId"),
                "to_lineup_slot_id": item.get("toLineupSlotId"),
                "is_keeper": item.get("isKeeper"),
                "has_items": True,
            })


def backfill_player_names(data: Dataset) -> None:
    """mDraftDetail and mTransactions2 carry player ids but no names.

    Names are taken from the players dimension, which is built from weekly
    rosters. A player drafted or claimed but never on a roster keeps his id
    and a NULL name - that is a real gap in ESPN's data, not an error.
    """
    names = {row["player_id"]: row.get("full_name")
             for row in data.rows(T.PLAYERS)}
    for table in (T.DRAFT_PICKS, T.TRANSACTIONS):
        for row in data.rows(table):
            if row.get("player_name") is None:
                row["player_name"] = names.get(row.get("player_id"))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def parse_store(store: RawStore, league_id: int,
                seasons: list[int] | None = None) -> Dataset:
    """Build the full dataset from whatever the raw store holds."""
    data = Dataset()
    available = store.seasons()
    wanted = [s for s in available if seasons is None or s in seasons]
    if not wanted:
        return data

    for season in wanted:
        log.info("parsing %d", season)
        parse_season_settings(data, season, store.get(season, "mSettings"),
                              league_id)
        parse_teams(data, season, store.get(season, "mTeam"))
        parse_matchups(data, season, store.get(season, "mMatchupScore"))
        parse_draft(data, season, store.get(season, "mDraftDetail"))

        known_teams = {row["team_id"] for row in data.rows(T.TEAMS)
                       if row["season"] == season}
        for week in store.weeks(season, BOXSCORE_VIEW):
            parse_boxscore(data, season, week,
                           store.get(season, BOXSCORE_VIEW, week), known_teams)
        for week in store.weeks(season, TRANSACTIONS_VIEW):
            parse_transactions(data, season, week,
                               store.get(season, TRANSACTIONS_VIEW, week))

    backfill_player_names(data)
    _drop_orphan_owners(data)

    from .derive import derive_all
    derive_all(data)
    return data


def _drop_orphan_owners(data: Dataset) -> None:
    """Keep referential integrity for sinks that enforce foreign keys.

    A team can list an owner GUID that never appears in members[] - usually
    someone who left the league before the earliest season being fetched.
    Postgres would reject those rows outright, so they are nulled out here
    and the fact is logged rather than failing the whole load.
    """
    known = {row["member_id"] for row in data.rows(T.MEMBERS)}
    dropped = 0
    for row in data.rows(T.TEAMS):
        if row["member_id"] is not None and row["member_id"] not in known:
            row["member_id"] = None
            dropped += 1
    kept = [row for row in data.rows(T.TEAM_OWNERS) if row["member_id"] in known]
    dropped += len(data.rows(T.TEAM_OWNERS)) - len(kept)
    data.replace(T.TEAM_OWNERS, kept)
    if dropped:
        log.info("dropped %d owner reference(s) to members ESPN did not "
                 "return (owners who left before the earliest fetched season)",
                 dropped)
