"""One column-type classification, shared by every typed sink.

CSV and JSON do not need types, but Parquet, DuckDB, SQLite and Postgres all
do, and they must agree: a `season` that is BIGINT in DuckDB and VARCHAR in
Parquet would make the two exports of the same league refuse to join.

Types are declared here rather than inferred from the data. Inference on a
column that happens to be all-NULL for one small league would pick a different
type than the same column in a bigger one.
"""

from __future__ import annotations

from .. import tables as T

JSON = "json"
TIMESTAMP = "timestamp"
BOOL = "bool"
INT = "int"
FLOAT = "float"
TEXT = "text"

#: Columns whose type the name-based rules below would get wrong.
#: `*_id` is an integer almost everywhere, but not for ESPN's member GUIDs or
#: its opaque transaction ids.
OVERRIDES: dict[str, str] = {
    "member_id": TEXT,
    "transaction_id": TEXT,
    "related_transaction_id": TEXT,
    "win_pct": FLOAT,
    "logo_url": TEXT,
    "auto_draft_type": TEXT,
}

BOOLS = frozenset({
    "is_bye", "started", "keeper", "has_items", "is_pending", "is_keeper",
})

FLOATS = frozenset({
    "points_for", "points_against", "bid_amount", "home_score", "away_score",
    "home_tiebreak", "away_tiebreak", "actual_points", "projected_points",
    "champion_score", "runner_up_score",
})

INTS = frozenset({
    "season", "week", "wins", "losses", "ties", "seasons", "team_count",
    "playoff_team_count", "regular_season_length", "matchup_period_count",
    "final_scoring_period", "playoff_seed", "final_rank",
    "regular_season_rank", "waiver_rank", "overall_pick", "round",
    "round_pick", "item_index", "scoring_period", "lineup_slot_id",
    "from_lineup_slot_id", "to_lineup_slot_id", "default_position_id",
    "first_seen_season", "last_seen_season", "championship_week", "titles",
    "runner_up_finishes", "playoff_appearances", "best_finish",
})


def classify(table: T.Table, column: str) -> str:
    if column in table.json_columns:
        return JSON
    if column in table.timestamp_columns:
        return TIMESTAMP
    if column in OVERRIDES:
        return OVERRIDES[column]
    if column in BOOLS:
        return BOOL
    if column in FLOATS:
        return FLOAT
    if column in INTS or column.endswith("_id"):
        return INT
    return TEXT


def check_coverage() -> list[str]:
    """Every column resolves to a type; TEXT is the fallback, not a bug.

    Exposed for the test suite so a column added to tables.py without a type
    rule shows up as a deliberate decision rather than a silent VARCHAR.
    """
    return [f"{t.name}.{c}" for t in T.ALL_TABLES for c in t.columns
            if classify(t, c) == TEXT]
