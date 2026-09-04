"""Tables computed from other tables.

These are the answers people actually want - who won, what got traded, whose
all-time record is best - and they are computed in Python rather than as SQL
views so that a CSV or Parquet export carries them too, not just the database
outputs.
"""

from __future__ import annotations

from typing import Any

from . import tables as T
from .parse import Dataset

#: ESPN's playoff bracket tiers. Consolation games must never contaminate a
#: championship query, which is what makes this distinction load-bearing.
CHAMPIONSHIP_TIER = "WINNERS_BRACKET"
REGULAR_SEASON_TIER = "NONE"

DECIDED = frozenset({"HOME", "AWAY"})


def derive_all(data: Dataset) -> None:
    derive_champions(data)
    derive_trades(data)
    derive_member_records(data)


def derive_champions(data: Dataset) -> None:
    """The championship game is the last decided WINNERS_BRACKET matchup."""
    teams = _team_index(data)
    members = {row["member_id"]: row.get("display_name")
               for row in data.rows(T.MEMBERS)}

    finals: dict[int, dict[str, Any]] = {}
    for matchup in data.rows(T.MATCHUPS):
        if matchup.get("playoff_tier_type") != CHAMPIONSHIP_TIER:
            continue
        if matchup.get("winner") not in DECIDED:
            continue
        season = matchup["season"]
        current = finals.get(season)
        if current is None or _later(matchup, current):
            finals[season] = matchup

    rows = []
    for season, final in sorted(finals.items()):
        home_won = final["winner"] == "HOME"
        champ_id = final["home_team_id"] if home_won else final["away_team_id"]
        runner_id = final["away_team_id"] if home_won else final["home_team_id"]
        champ = teams.get((season, champ_id), {})
        runner = teams.get((season, runner_id), {})
        rows.append({
            "season": season,
            "championship_week": final.get("week"),
            "champion_team_id": champ_id,
            "champion": champ.get("team_name"),
            "champion_member": members.get(champ.get("member_id")),
            "champion_score": final["home_score"] if home_won else final["away_score"],
            "runner_up_team_id": runner_id,
            "runner_up": runner.get("team_name"),
            "runner_up_member": members.get(runner.get("member_id")),
            "runner_up_score": final["away_score"] if home_won else final["home_score"],
        })
    data.replace(T.SEASON_CHAMPIONS, rows)


def _later(candidate: dict, current: dict) -> bool:
    """Order finals by matchup period, then by ESPN's own matchup id."""
    return ((candidate.get("week") or 0, candidate.get("espn_matchup_id") or 0)
            > (current.get("week") or 0, current.get("espn_matchup_id") or 0))


def derive_trades(data: Dataset) -> None:
    """Flatten the trades ESPN reported with full item detail.

    Most TRADE_ACCEPT rows come back as a bare header with an empty items[],
    and those cannot be recovered - the players involved are simply not in the
    response. Only items ESPN itself returned are represented here; nothing is
    reconstructed or guessed. `has_items = false` rows in `transactions` mark
    the trades that are known to have happened but whose contents are lost.
    """
    teams = _team_index(data)
    rows = []
    for txn in data.rows(T.TRANSACTIONS):
        if txn.get("type") != "TRADE_ACCEPT" or not txn.get("has_items"):
            continue
        season = txn["season"]
        rows.append({
            "season": season,
            "transaction_id": txn["transaction_id"],
            "item_index": txn["item_index"],
            "week": txn.get("scoring_period"),
            "processed_at": txn.get("processed_at"),
            "from_team_id": txn.get("from_team_id"),
            "from_team": teams.get((season, txn.get("from_team_id")), {}).get("team_name"),
            "to_team_id": txn.get("to_team_id"),
            "to_team": teams.get((season, txn.get("to_team_id")), {}).get("team_name"),
            "player_id": txn.get("player_id"),
            "player_name": txn.get("player_name"),
        })
    rows.sort(key=lambda r: (r["season"], r["transaction_id"], r["item_index"]))
    data.replace(T.TRADES, rows)


def derive_member_records(data: Dataset) -> None:
    """All-time record per person, following them across team renames.

    Keyed on the ESPN member GUID rather than a team name, which is the whole
    point of the members table: names churn yearly, the GUID does not.
    """
    teams = _team_index(data)
    playoff_cuts = {row["season"]: row.get("playoff_team_count")
                    for row in data.rows(T.SEASONS)}

    titles: dict[str, int] = {}
    runners_up: dict[str, int] = {}
    for champ in data.rows(T.SEASON_CHAMPIONS):
        for team_id, bucket in ((champ["champion_team_id"], titles),
                                (champ["runner_up_team_id"], runners_up)):
            member = teams.get((champ["season"], team_id), {}).get("member_id")
            if member:
                bucket[member] = bucket.get(member, 0) + 1

    accumulated: dict[str, dict[str, Any]] = {}
    for team in data.rows(T.TEAMS):
        member_id = team.get("member_id")
        if not member_id:
            continue
        rec = accumulated.setdefault(member_id, {
            "member_id": member_id, "seasons": 0, "wins": 0, "losses": 0,
            "ties": 0, "points_for": 0.0, "points_against": 0.0,
            "playoff_appearances": 0, "best_finish": None,
        })
        rec["seasons"] += 1
        rec["wins"] += team.get("wins") or 0
        rec["losses"] += team.get("losses") or 0
        rec["ties"] += team.get("ties") or 0
        rec["points_for"] += team.get("points_for") or 0.0
        rec["points_against"] += team.get("points_against") or 0.0
        cut = playoff_cuts.get(team["season"])
        if team.get("playoff_seed") and cut and team["playoff_seed"] <= cut:
            rec["playoff_appearances"] += 1
        finish = team.get("final_rank")
        if finish:
            best = rec["best_finish"]
            rec["best_finish"] = finish if best is None else min(best, finish)

    names = {row["member_id"]: row.get("display_name")
             for row in data.rows(T.MEMBERS)}
    rows = []
    for member_id, rec in accumulated.items():
        played = rec["wins"] + rec["losses"] + rec["ties"]
        rows.append({
            **rec,
            "display_name": names.get(member_id),
            "win_pct": (round((rec["wins"] + 0.5 * rec["ties"]) / played, 4)
                        if played else None),
            "points_for": round(rec["points_for"], 2),
            "points_against": round(rec["points_against"], 2),
            "titles": titles.get(member_id, 0),
            "runner_up_finishes": runners_up.get(member_id, 0),
        })
    rows.sort(key=lambda r: (-r["titles"], -r["wins"], r["display_name"] or ""))
    data.replace(T.MEMBER_RECORDS, rows)


# ---------------------------------------------------------------------------

def _team_index(data: Dataset) -> dict[tuple[int, Any], dict[str, Any]]:
    return {(row["season"], row["team_id"]): row for row in data.rows(T.TEAMS)}
