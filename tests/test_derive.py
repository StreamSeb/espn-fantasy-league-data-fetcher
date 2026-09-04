"""Derived tables: champions, trades and all-time records."""

from __future__ import annotations

from conftest import ALICE, BOB, LEAGUE_ID

from espn_fantasy import tables as T
from espn_fantasy.parse import parse_store
from espn_fantasy.store.sqlite import SqliteRawStore


def parsed(path):
    with SqliteRawStore(str(path)) as store:
        return parse_store(store, LEAGUE_ID)


def test_champion_is_the_winners_bracket_final(populated_store):
    """A consolation game in the same week has a higher matchup id, and must
    not be mistaken for the championship."""
    champs = {c["season"]: c for c in parsed(populated_store).rows(T.SEASON_CHAMPIONS)}
    assert champs[2023]["champion"] == "Alice FC 2023"
    assert champs[2023]["champion_member"] == "alice"
    assert champs[2023]["champion_score"] == 110.5
    assert champs[2023]["runner_up"] == "Bob United"
    assert champs[2023]["runner_up_score"] == 90.0


def test_only_trades_espn_reported_in_full_are_flattened(populated_store):
    data = parsed(populated_store)
    trades = data.rows(T.TRADES)
    assert trades, "the fixture has one recoverable trade per season-week"
    assert all(t["player_id"] is not None for t in trades)
    # The header-only trade stays in transactions and never reaches trades.
    header_only = {t["transaction_id"] for t in data.rows(T.TRANSACTIONS)
                   if t["type"] == "TRADE_ACCEPT" and not t["has_items"]}
    assert header_only
    assert header_only.isdisjoint({t["transaction_id"] for t in trades})


def test_trades_carry_team_names(populated_store):
    trade = parsed(populated_store).rows(T.TRADES)[0]
    assert trade["from_team"] == "Alice FC 2023"
    assert trade["to_team"] == "Bob United"


def test_member_records_follow_a_person_across_renames(populated_store):
    """Alice's team is renamed every season; her record must still add up."""
    records = {r["member_id"]: r for r in parsed(populated_store).rows(T.MEMBER_RECORDS)}
    alice = records[ALICE]
    assert alice["seasons"] == 2
    assert alice["wins"] == 4 and alice["losses"] == 0
    assert alice["win_pct"] == 1.0
    assert alice["titles"] == 2
    assert alice["points_for"] == 421.0
    assert records[BOB]["titles"] == 0
    assert records[BOB]["runner_up_finishes"] == 2


def test_records_are_ordered_by_titles_then_wins(populated_store):
    records = parsed(populated_store).rows(T.MEMBER_RECORDS)
    assert [r["member_id"] for r in records] == [ALICE, BOB]
