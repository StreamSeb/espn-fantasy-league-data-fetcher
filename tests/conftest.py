"""A miniature two-season league, built as ESPN would return it.

The fixture is hand-written rather than captured from a real league, so it can
live in a public repository without carrying anyone's member GUIDs, team names
or session cookies - and so an edge case can be added by writing it down
rather than by waiting for it to happen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffl_history.config import Config  # noqa: E402
from ffl_history.store import RawRow  # noqa: E402
from ffl_history.store.sqlite import SqliteRawStore  # noqa: E402

LEAGUE_ID = 999999
SEASONS = (2023, 2024)

MEMBERS = [
    {"id": "{AAAA0000-0000-0000-0000-000000000001}", "displayName": "alice",
     "firstName": "Alice", "lastName": "Anderson"},
    {"id": "{BBBB0000-0000-0000-0000-000000000002}", "displayName": "bob",
     "firstName": "Bob", "lastName": "Brown"},
]
ALICE, BOB = MEMBERS[0]["id"], MEMBERS[1]["id"]


def settings_payload(season: int, *, latest_period: int = 3) -> dict:
    return {
        "id": LEAGUE_ID,
        "status": {"finalScoringPeriod": 3, "latestScoringPeriod": latest_period,
                   "previousSeasons": [s for s in SEASONS if s < season]},
        "settings": {
            "name": "Test League",
            "size": 2,
            "scheduleSettings": {"matchupPeriodCount": 2, "playoffTeamCount": 2},
            "rosterSettings": {"lineupSlotCounts": {"0": 1, "20": 1}},
            "scoringSettings": {"scoringType": "H2H_POINTS",
                                "scoringItems": [{"statId": 3, "points": 0.04}]},
            "draftSettings": {"type": "SNAKE"},
        },
    }


def team_payload(season: int) -> dict:
    return {
        "members": MEMBERS,
        "teams": [
            {"id": 1, "name": f"Alice FC {season}", "abbrev": "ALI",
             "owners": [ALICE], "playoffSeed": 1, "rankCalculatedFinal": 1,
             "record": {"overall": {"wins": 2, "losses": 0, "ties": 0,
                                    "pointsFor": 210.5, "pointsAgainst": 180.0}}},
            {"id": 2, "location": "Bob", "nickname": "United", "abbrev": "BOB",
             "owners": [BOB], "playoffSeed": 2, "rankCalculatedFinal": 2,
             "record": {"overall": {"wins": 0, "losses": 2, "ties": 0,
                                    "pointsFor": 180.0, "pointsAgainst": 210.5}}},
        ],
    }


def matchup_payload(season: int) -> dict:
    return {
        "schedule": [
            {"id": 1, "matchupPeriodId": 1, "playoffTierType": "NONE",
             "winner": "HOME",
             "home": {"teamId": 1, "totalPoints": 100.0},
             "away": {"teamId": 2, "totalPoints": 90.0}},
            # The championship: a WINNERS_BRACKET game, which is what the
            # champion derivation must pick out.
            {"id": 2, "matchupPeriodId": 2, "playoffTierType": "WINNERS_BRACKET",
             "winner": "HOME",
             "home": {"teamId": 1, "totalPoints": 110.5},
             "away": {"teamId": 2, "totalPoints": 90.0}},
            # A consolation game in the same week, which must NOT be mistaken
            # for the final even though its matchup id is higher.
            {"id": 3, "matchupPeriodId": 2,
             "playoffTierType": "LOSERS_CONSOLATION_LADDER", "winner": "AWAY",
             "home": {"teamId": 2, "totalPoints": 50.0},
             "away": {"teamId": 1, "totalPoints": 60.0}},
        ]
    }


def draft_payload(season: int) -> dict:
    return {"draftDetail": {"picks": [
        {"overallPickNumber": 1, "roundId": 1, "roundPickNumber": 1,
         "teamId": 1, "playerId": 1001, "bidAmount": 0, "keeper": False},
        {"overallPickNumber": 2, "roundId": 1, "roundPickNumber": 2,
         "teamId": 2, "playerId": 4004, "bidAmount": 0, "keeper": False},
    ]}}


def _player(pid: int, name: str, position_id: int, week: int,
            actual: float, projected: float) -> dict:
    return {
        "id": pid, "fullName": name, "defaultPositionId": position_id,
        "proTeamId": 1,
        "stats": [
            {"scoringPeriodId": week, "statSourceId": 0, "appliedTotal": actual},
            {"scoringPeriodId": week, "statSourceId": 1, "appliedTotal": projected},
            # A different week's line, which must be ignored.
            {"scoringPeriodId": week + 1, "statSourceId": 0, "appliedTotal": 999.0},
        ],
    }


def boxscore_payload(season: int, week: int) -> dict:
    """Returned in BOTH shapes ESPN uses, with one player in each.

    Team 1 arrives under teams[].roster.entries and team 2 under
    schedule[].home/away.rosterForCurrentScoringPeriod.entries, which is what
    the parser has to reconcile.
    """
    return {
        "teams": [{
            "id": 1,
            "roster": {"entries": [
                {"lineupSlotId": 0, "playerId": 1001,
                 "playerPoolEntry": {"player": _player(1001, "Quinn Passer", 1,
                                                       week, 100.0, 95.0)}},
                {"lineupSlotId": 20, "playerId": 2002,
                 "playerPoolEntry": {"player": _player(2002, "Rhea Rusher", 2,
                                                       week, 5.0, 7.0)}},
            ]},
        }],
        "schedule": [{
            "home": {"teamId": 2, "rosterForCurrentScoringPeriod": {"entries": [
                {"lineupSlotId": 0, "playerId": 3003,
                 "playerPoolEntry": {"player": _player(3003, "Wes Wideout", 3,
                                                       week, 90.0, 88.0)}},
            ]}},
            # The same player again under the other shape, to prove that
            # de-duplication on the natural key actually happens.
            "away": {"teamId": 1, "rosterForCurrentScoringPeriod": {"entries": [
                {"lineupSlotId": 0, "playerId": 1001,
                 "playerPoolEntry": {"player": _player(1001, "Quinn Passer", 1,
                                                       week, 100.0, 95.0)}},
            ]}},
        }],
    }


def transaction_payload(season: int, week: int) -> dict:
    return {"transactions": [
        {"id": f"txn-{season}-{week}-1", "type": "WAIVER", "status": "EXECUTED",
         "teamId": 1, "bidAmount": 17, "scoringPeriodId": week,
         "proposedDate": 1700000000000, "processDate": 1700003600000,
         "memberId": ALICE,
         "items": [{"type": "ADD", "playerId": 2002, "toTeamId": 1},
                   {"type": "DROP", "playerId": 5005, "fromTeamId": 1}]},
        # A failed claim, kept on purpose: it records who bid what on whom.
        {"id": f"txn-{season}-{week}-2", "type": "WAIVER",
         "status": "FAILED_ROSTERLIMIT", "teamId": 2, "bidAmount": 3,
         "scoringPeriodId": week,
         "items": [{"type": "ADD", "playerId": 2002, "toTeamId": 2}]},
        # A trade ESPN reported in full.
        {"id": f"txn-{season}-{week}-3", "type": "TRADE_ACCEPT",
         "status": "EXECUTED", "scoringPeriodId": week,
         "processDate": 1700007200000,
         "items": [{"type": "TRADE", "playerId": 1001,
                    "fromTeamId": 1, "toTeamId": 2}]},
        # A trade ESPN reported as a bare header - unrecoverable by design.
        {"id": f"txn-{season}-{week}-4", "type": "TRADE_ACCEPT",
         "scoringPeriodId": week, "items": []},
    ]}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Never let the developer's own settings reach a test.

    Without this, a shell that has sourced a real .env would give the CLI
    tests working credentials, and a test meant to assert "this fails without
    a league id" would instead start fetching somebody's actual league.
    """
    prefixes = ("ESPN_", "FFL_", "POSTGRES_", "SWID")
    extras = {"DATABASE_URL", "MAX_SCORING_PERIOD", "REQUEST_DELAY_SECONDS",
              "REQUEST_TIMEOUT_SECONDS", "REQUEST_RETRIES"}
    for name in list(os.environ):
        # FFL_TEST_DSN survives: it names the scratch database, not a league.
        if name == "FFL_TEST_DSN":
            continue
        if name.startswith(prefixes) or name in extras:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def populated_store(tmp_path: Path):
    """A raw store holding two complete seasons of the fixture league."""
    path = tmp_path / "raw.sqlite3"
    with SqliteRawStore(str(path)) as store:
        for season in SEASONS:
            for view, payload in (
                ("mSettings", settings_payload(season)),
                ("mTeam", team_payload(season)),
                ("mMatchupScore", matchup_payload(season)),
                ("mDraftDetail", draft_payload(season)),
            ):
                store.put(RawRow(season, view, None, f"https://example/{view}",
                                 200, payload))
                store.log_unit(season, view, None, "ok", 200)
            for week in (1, 2):
                store.put(RawRow(season, "mBoxscore", week, "https://example/box",
                                 200, boxscore_payload(season, week)))
                store.log_unit(season, "mBoxscore", week, "ok", 200)
                store.put(RawRow(season, "mTransactions2", week,
                                 "https://example/txn", 200,
                                 transaction_payload(season, week)))
                store.log_unit(season, "mTransactions2", week, "ok", 200)
        store.commit()
    yield path


@pytest.fixture
def cfg(tmp_path: Path, populated_store: Path) -> Config:
    return Config(
        league_id=str(LEAGUE_ID),
        espn_s2="cookie", swid="{0000-0000}",
        seasons=list(SEASONS),
        raw_store="sqlite", raw_db_path=str(populated_store),
        out_dir=str(tmp_path / "out"),
        formats=[],
        delay=0.0,
    )
