"""The parser, against the fixture league."""

from __future__ import annotations

from conftest import ALICE, LEAGUE_ID, SEASONS

from ffl_history import tables as T
from ffl_history.parse import parse_store
from ffl_history.store.sqlite import SqliteRawStore


def parsed(path):
    with SqliteRawStore(str(path)) as store:
        return parse_store(store, LEAGUE_ID)


def test_all_seasons_parsed(populated_store):
    data = parsed(populated_store)
    assert [r["season"] for r in data.rows(T.SEASONS)] == list(SEASONS)
    assert data.rows(T.SEASONS)[0]["league_name"] == "Test League"
    assert data.rows(T.SEASONS)[0]["team_count"] == 2


def test_team_name_is_built_from_either_shape(populated_store):
    """ESPN dropped location/nickname for a single `name` in 2023."""
    teams = {(r["season"], r["team_id"]): r for r in parsed(populated_store).rows(T.TEAMS)}
    assert teams[(2023, 1)]["team_name"] == "Alice FC 2023"
    assert teams[(2023, 2)]["team_name"] == "Bob United"


def test_members_span_seasons(populated_store):
    members = {r["member_id"]: r for r in parsed(populated_store).rows(T.MEMBERS)}
    assert members[ALICE]["display_name"] == "alice"
    assert members[ALICE]["first_seen_season"] == 2023
    assert members[ALICE]["last_seen_season"] == 2024


def test_both_boxscore_shapes_are_read_and_deduplicated(populated_store):
    """Team 1's QB appears under both shapes; he must land once per week."""
    slots = parsed(populated_store).rows(T.ROSTER_SLOTS)
    qb = [s for s in slots
          if s["season"] == 2023 and s["week"] == 1 and s["player_id"] == 1001]
    assert len(qb) == 1
    # Team 2 only ever appears under the schedule[] shape.
    assert any(s["team_id"] == 2 for s in slots)


def test_points_come_from_the_right_scoring_period(populated_store):
    """The fixture plants a 999.0 line for the following week."""
    slots = {(s["week"], s["player_id"]): s
             for s in parsed(populated_store).rows(T.ROSTER_SLOTS)
             if s["season"] == 2023}
    assert slots[(1, 1001)]["actual_points"] == 100.0
    assert slots[(1, 1001)]["projected_points"] == 95.0


def test_bench_slots_are_not_started(populated_store):
    slots = {(s["week"], s["player_id"]): s
             for s in parsed(populated_store).rows(T.ROSTER_SLOTS)
             if s["season"] == 2023}
    assert slots[(1, 1001)]["started"] is True
    assert slots[(1, 2002)]["started"] is False   # lineupSlotId 20 = bench


def test_default_position_is_not_run_through_the_slot_map(populated_store):
    """defaultPositionId and lineupSlotId are different id spaces."""
    players = {p["player_id"]: p for p in parsed(populated_store).rows(T.PLAYERS)}
    assert players[1001]["position"] == "QB"
    assert players[2002]["position"] == "RB"
    assert players[3003]["position"] == "WR"


def test_player_names_are_backfilled_into_draft_and_transactions(populated_store):
    data = parsed(populated_store)
    picks = {p["overall_pick"]: p for p in data.rows(T.DRAFT_PICKS)
             if p["season"] == 2023}
    assert picks[1]["player_name"] == "Quinn Passer"
    # Pick 2 is a player who never appeared on a roster: id kept, name NULL.
    assert picks[2]["player_id"] == 4004
    assert picks[2]["player_name"] is None


def test_failed_transactions_are_kept(populated_store):
    statuses = {t["status"] for t in parsed(populated_store).rows(T.TRANSACTIONS)}
    assert "FAILED_ROSTERLIMIT" in statuses
    assert "EXECUTED" in statuses


def test_header_only_trades_are_flagged_not_dropped(populated_store):
    trades = [t for t in parsed(populated_store).rows(T.TRANSACTIONS)
              if t["type"] == "TRADE_ACCEPT"]
    assert any(t["has_items"] for t in trades)
    assert any(not t["has_items"] for t in trades)


def test_transaction_items_become_one_row_each(populated_store):
    rows = [t for t in parsed(populated_store).rows(T.TRANSACTIONS)
            if t["transaction_id"] == "txn-2023-1-1"]
    assert {r["item_index"] for r in rows} == {0, 1}
    assert {r["item_type"] for r in rows} == {"ADD", "DROP"}
    assert all(r["bid_amount"] == 17 for r in rows)


def test_timestamps_are_timezone_aware(populated_store):
    row = next(t for t in parsed(populated_store).rows(T.TRANSACTIONS)
               if t["transaction_id"] == "txn-2023-1-1")
    assert row["processed_at"].tzinfo is not None


def test_settings_blob_is_preserved(populated_store):
    settings = parsed(populated_store).rows(T.SEASONS)[0]["settings_raw"]
    assert settings["rosterSettings"]["lineupSlotCounts"] == {"0": 1, "20": 1}


def test_parse_is_deterministic(populated_store):
    first, second = parsed(populated_store), parsed(populated_store)
    assert first.counts() == second.counts()


def test_season_filter(populated_store):
    with SqliteRawStore(str(populated_store)) as store:
        data = parse_store(store, LEAGUE_ID, seasons=[2024])
    assert {r["season"] for r in data.rows(T.SEASONS)} == {2024}
    assert {r["season"] for r in data.rows(T.ROSTER_SLOTS)} == {2024}
