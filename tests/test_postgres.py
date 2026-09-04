"""Postgres round-trip.

Skipped unless FFL_TEST_DSN names a Postgres 15+ database that the test may
create and drop tables in. CI provides one; locally, point it at a scratch
database - never at one holding a real league.
"""

from __future__ import annotations

import os

import pytest
from conftest import LEAGUE_ID

from ffl_history import tables as T
from ffl_history.parse import parse_store
from ffl_history.sinks.postgres_sink import PostgresSink
from ffl_history.store import RawRow
from ffl_history.store.postgres import PostgresRawStore
from ffl_history.store.sqlite import SqliteRawStore

#: Read at import time, before the environment-isolation fixture runs.
DSN = os.getenv("FFL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set FFL_TEST_DSN to run")
psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def clean_db():
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        names = ", ".join(f'"{t.name}"' for t in T.ALL_TABLES)
        conn.execute(f"DROP TABLE IF EXISTS {names} CASCADE")
        conn.execute("DROP TABLE IF EXISTS raw_responses, fetch_log CASCADE")
    finally:
        conn.close()
    yield DSN


@pytest.fixture
def data(populated_store):
    with SqliteRawStore(str(populated_store)) as store:
        return parse_store(store, LEAGUE_ID)


def test_schema_applies_and_reapplies(cfg, clean_db, data):
    """The DDL is a migration step, run on every load, so it must be a no-op
    the second time."""
    cfg.pg_dsn = clean_db
    sink = PostgresSink(cfg)
    first = sink.write(data)
    second = sink.write(data)
    assert first.rows == second.rows

    conn = psycopg.connect(clean_db)
    try:
        for table in T.ALL_TABLES:
            count = conn.execute(
                f'SELECT count(*) FROM "{table.name}"').fetchone()[0]
            assert count == len(data.rows(table)), table.name
    finally:
        conn.close()


def test_foreign_keys_hold(cfg, clean_db, data):
    """Loading in ALL_TABLES order must satisfy every declared FK."""
    cfg.pg_dsn = clean_db
    PostgresSink(cfg).write(data)
    conn = psycopg.connect(clean_db)
    try:
        orphans = conn.execute(
            "SELECT count(*) FROM roster_slots rs "
            "LEFT JOIN teams t ON t.season = rs.season AND t.team_id = rs.team_id "
            "WHERE t.team_id IS NULL").fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0


def test_jsonb_columns_are_queryable(cfg, clean_db, data):
    cfg.pg_dsn = clean_db
    PostgresSink(cfg).write(data)
    conn = psycopg.connect(clean_db)
    try:
        value = conn.execute(
            "SELECT roster_slot_counts->>'20' FROM seasons "
            "WHERE season = 2023").fetchone()[0]
    finally:
        conn.close()
    assert value == "1"


def test_narrowing_the_season_range_clears_stale_derived_rows(cfg, clean_db,
                                                              populated_store):
    """Derived tables are rebuilt, not merged: a later run over fewer seasons
    must not leave the earlier season's champion behind."""
    cfg.pg_dsn = clean_db
    with SqliteRawStore(str(populated_store)) as store:
        PostgresSink(cfg).write(parse_store(store, LEAGUE_ID))
        PostgresSink(cfg).write(parse_store(store, LEAGUE_ID, seasons=[2024]))

    conn = psycopg.connect(clean_db)
    try:
        seasons = [r[0] for r in conn.execute(
            "SELECT season FROM season_champions ORDER BY season").fetchall()]
    finally:
        conn.close()
    assert seasons == [2024]


def test_postgres_raw_store_round_trips(clean_db):
    payload = {"settings": {"name": "Test League"}, "nested": [1, 2, {"a": True}]}
    with PostgresRawStore(clean_db) as store:
        store.put(RawRow(2023, "mSettings", None, "https://example", 200, payload))
        store.log_unit(2023, "mSettings", None, "ok", 200)
        store.put(RawRow(2023, "mBoxscore", 4, "https://example", 200, {"x": 1}))
        store.log_unit(2023, "mBoxscore", 4, "ok", 200)
        store.commit()

        assert store.get(2023, "mSettings") == payload
        assert store.seasons() == [2023]
        assert store.weeks(2023, "mBoxscore") == [4]
        assert store.completed_units() == {(2023, "mSettings", None),
                                           (2023, "mBoxscore", 4)}


def test_season_level_rows_upsert_rather_than_duplicating(clean_db):
    """NULL scoring_period must collide, which is what NULLS NOT DISTINCT buys."""
    with PostgresRawStore(clean_db) as store:
        for value in ("first", "second"):
            store.put(RawRow(2023, "mSettings", None, "https://example", 200,
                             {"v": value}))
        store.commit()
        assert store.get(2023, "mSettings") == {"v": "second"}
        count = store.conn.execute(
            "SELECT count(*) FROM raw_responses").fetchone()[0]
    assert count == 1
