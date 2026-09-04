"""Every output format, against the same parsed dataset.

The point of these tests is agreement: a season count read out of the CSV, the
Parquet file, the SQLite database and the spreadsheet must all be the same
number, because they were all written from the same rows.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest
from conftest import LEAGUE_ID

from ffl_history import tables as T
from ffl_history.parse import parse_store
from ffl_history.sinks import available_formats, build_sinks
from ffl_history.sinks.coltypes import classify
from ffl_history.store.sqlite import SqliteRawStore

pytest.importorskip("pyarrow")


@pytest.fixture
def data(populated_store):
    with SqliteRawStore(str(populated_store)) as store:
        return parse_store(store, LEAGUE_ID)


FILE_FORMATS = ["csv", "json", "jsonl", "sqlite", "parquet", "xlsx", "duckdb"]


@pytest.mark.parametrize("fmt", FILE_FORMATS)
def test_sink_writes_every_row(cfg, data, fmt):
    cfg.formats = [fmt]
    result = build_sinks(cfg)[0].write(data)
    expected = sum(len(rows) for _, rows in data)
    assert result.rows == expected
    assert Path(result.destination).exists()


def test_csv_headers_match_the_table_definition(cfg, data):
    cfg.formats = ["csv"]
    build_sinks(cfg)[0].write(data)
    for table in T.ALL_TABLES:
        path = Path(cfg.out_dir) / "csv" / f"{table.name}.csv"
        with path.open() as fh:
            header = next(csv.reader(fh))
        assert header == list(table.columns), table.name


def test_csv_flattens_nested_settings_to_json(cfg, data):
    cfg.formats = ["csv"]
    build_sinks(cfg)[0].write(data)
    with (Path(cfg.out_dir) / "csv" / "seasons.csv").open() as fh:
        row = next(csv.DictReader(fh))
    assert json.loads(row["roster_slot_counts"]) == {"0": 1, "20": 1}


def test_json_keeps_nested_settings_nested(cfg, data):
    cfg.formats = ["json"]
    build_sinks(cfg)[0].write(data)
    rows = json.loads((Path(cfg.out_dir) / "json" / "seasons.json").read_text())
    assert rows[0]["roster_slot_counts"] == {"0": 1, "20": 1}


def test_jsonl_is_one_object_per_line(cfg, data):
    cfg.formats = ["jsonl"]
    build_sinks(cfg)[0].write(data)
    lines = (Path(cfg.out_dir) / "jsonl" / "teams.jsonl").read_text().splitlines()
    assert len(lines) == len(data.rows(T.TEAMS))
    assert json.loads(lines[0])["team_id"] in {1, 2}


def test_sqlite_upserts_rather_than_duplicating(cfg, data):
    cfg.formats = ["sqlite"]
    sink = build_sinks(cfg)[0]
    sink.write(data)
    result = sink.write(data)          # same rows, second time
    conn = sqlite3.connect(result.destination)
    try:
        for table in T.ALL_TABLES:
            count = conn.execute(f'SELECT count(*) FROM "{table.name}"').fetchone()[0]
            assert count == len(data.rows(table)), table.name
    finally:
        conn.close()


def test_sqlite_is_queryable(cfg, data):
    cfg.formats = ["sqlite"]
    result = build_sinks(cfg)[0].write(data)
    conn = sqlite3.connect(result.destination)
    try:
        row = conn.execute(
            "SELECT champion, champion_score FROM season_champions "
            "WHERE season = 2023").fetchone()
    finally:
        conn.close()
    assert row == ("Alice FC 2023", 110.5)


def test_parquet_types_match_the_shared_classification(cfg, data):
    import pyarrow.parquet as pq

    cfg.formats = ["parquet"]
    build_sinks(cfg)[0].write(data)
    table = pq.read_table(Path(cfg.out_dir) / "parquet" / "roster_slots.parquet")
    schema = {f.name: str(f.type) for f in table.schema}
    assert schema["season"] == "int64"
    assert schema["actual_points"] == "double"
    assert schema["started"] == "bool"
    assert schema["player_name"] == "string"


def test_duckdb_round_trips(cfg, data):
    duckdb = pytest.importorskip("duckdb")
    cfg.formats = ["duckdb"]
    result = build_sinks(cfg)[0].write(data)
    conn = duckdb.connect(result.destination, read_only=True)
    try:
        count = conn.execute("SELECT count(*) FROM roster_slots").fetchone()[0]
    finally:
        conn.close()
    assert count == len(data.rows(T.ROSTER_SLOTS))


def test_xlsx_has_a_sheet_per_table(cfg, data):
    openpyxl = pytest.importorskip("openpyxl")
    cfg.formats = ["xlsx"]
    result = build_sinks(cfg)[0].write(data)
    book = openpyxl.load_workbook(result.destination, read_only=True)
    assert set(book.sheetnames) == {t.name for t in T.ALL_TABLES}
    assert book["teams"].max_row == len(data.rows(T.TEAMS)) + 1


def test_raw_dump_mirrors_the_store(cfg, data):
    cfg.formats = ["raw"]
    result = build_sinks(cfg)[0].write(data)
    files = sorted(Path(result.destination).rglob("*.json"))
    assert len(files) == result.rows
    payload = json.loads((Path(result.destination) / "2023" / "mSettings.json").read_text())
    assert payload["settings"]["name"] == "Test League"


def test_every_format_is_reachable_from_the_cli_names():
    assert set(available_formats()) == {
        "sqlite", "postgres", "csv", "json", "jsonl", "parquet", "xlsx",
        "duckdb", "raw"}


def test_no_column_is_accidentally_untyped():
    """TEXT is the fallback. Any column landing there should be genuine text."""
    text_columns = {c.rsplit(".", 1)[1] for c in
                    [f"{t.name}.{c}" for t in T.ALL_TABLES for c in t.columns
                     if classify(t, c) == "text"]}
    numeric_looking = {c for c in text_columns
                       if c.endswith(("_count", "_rank", "_seed", "_pct"))}
    assert not numeric_looking, f"probably wrong type: {numeric_looking}"


def test_a_missing_optional_dependency_fails_before_any_fetching(cfg, monkeypatch):
    """`run` builds its sinks before the first request, so a missing package
    costs a second rather than a completed ten-minute fetch."""
    from ffl_history.errors import MissingDependency
    from ffl_history.sinks.parquet_sink import ParquetSink

    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    cfg.formats = ["parquet"]
    with pytest.raises(MissingDependency, match="pyarrow"):
        build_sinks(cfg)
    # The advice has to name both the extra and the bare package.
    try:
        ParquetSink.check_available()
    except MissingDependency as exc:
        assert "ffl-history[parquet]" in str(exc)
        assert "pip install pyarrow" in str(exc)


def test_formats_with_no_optional_dependency_are_always_available(cfg):
    cfg.formats = ["csv", "json", "jsonl", "sqlite", "raw"]
    assert len(build_sinks(cfg)) == 5
