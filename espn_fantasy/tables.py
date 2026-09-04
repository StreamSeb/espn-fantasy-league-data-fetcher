"""Canonical table definitions.

Every sink writes the same tables with the same columns in the same order, so
a CSV header, a Parquet schema and a Postgres table all line up. The parser
produces plain dicts keyed by these column names; nothing downstream needs to
know where a row came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    #: Natural key. Sinks that support upserts conflict on exactly these.
    key: tuple[str, ...]
    #: Columns holding nested structures (dict/list) rather than scalars.
    json_columns: frozenset[str] = field(default_factory=frozenset)
    #: Columns holding timezone-aware datetimes.
    timestamp_columns: frozenset[str] = field(default_factory=frozenset)
    #: True for tables computed from other tables rather than parsed from
    #: ESPN payloads. They are rebuilt wholesale on every parse.
    derived: bool = False
    description: str = ""

    def value_columns(self) -> tuple[str, ...]:
        return tuple(c for c in self.columns if c not in self.key)


SEASONS = Table(
    name="seasons",
    columns=(
        "season", "league_id", "league_name", "team_count", "scoring_type",
        "playoff_team_count", "regular_season_length", "matchup_period_count",
        "final_scoring_period", "roster_slot_counts", "scoring_settings",
        "draft_type", "settings_raw",
    ),
    key=("season",),
    json_columns=frozenset({"roster_slot_counts", "scoring_settings", "settings_raw"}),
    description="One row per year: league settings, roster slots, scoring rules.",
)

MEMBERS = Table(
    name="members",
    columns=(
        "member_id", "display_name", "first_name", "last_name",
        "first_seen_season", "last_seen_season",
    ),
    key=("member_id",),
    description="People, keyed on the stable ESPN member GUID.",
)

TEAMS = Table(
    name="teams",
    columns=(
        "season", "team_id", "member_id", "team_name", "location", "nickname",
        "abbrev", "logo_url", "wins", "losses", "ties", "points_for",
        "points_against", "playoff_seed", "final_rank", "regular_season_rank",
        "waiver_rank",
    ),
    key=("season", "team_id"),
    description="One row per team per season.",
)

TEAM_OWNERS = Table(
    name="team_owners",
    columns=("season", "team_id", "member_id"),
    key=("season", "team_id", "member_id"),
    description="Co-ownership; ESPN allows several owners per team.",
)

PLAYERS = Table(
    name="players",
    columns=(
        "player_id", "full_name", "default_position_id", "position",
        "pro_team_id", "pro_team",
    ),
    key=("player_id",),
    description="Player dimension, built from rosters, drafts and transactions.",
)

MATCHUPS = Table(
    name="matchups",
    columns=(
        "season", "espn_matchup_id", "week", "home_team_id", "away_team_id",
        "home_score", "away_score", "home_tiebreak", "away_tiebreak", "winner",
        "playoff_tier_type", "is_bye",
    ),
    key=("season", "espn_matchup_id"),
    description="One row per scheduled game. week is a matchupPeriodId.",
)

ROSTER_SLOTS = Table(
    name="roster_slots",
    columns=(
        "season", "week", "team_id", "player_id", "player_name",
        "lineup_slot_id", "lineup_slot", "started", "actual_points",
        "projected_points", "position", "pro_team_id",
    ),
    key=("season", "week", "team_id", "player_id"),
    description="season x week x team x player. week is a scoringPeriodId.",
)

DRAFT_PICKS = Table(
    name="draft_picks",
    columns=(
        "season", "overall_pick", "round", "round_pick", "team_id",
        "player_id", "player_name", "bid_amount", "keeper",
        "nominating_team_id", "auto_draft_type",
    ),
    key=("season", "overall_pick"),
    description="Draft board. bid_amount is populated for auction leagues.",
)

TRANSACTIONS = Table(
    name="transactions",
    columns=(
        "season", "transaction_id", "item_index", "type", "status",
        "execution_type", "item_type", "team_id", "from_team_id", "to_team_id",
        "player_id", "player_name", "bid_amount", "scoring_period",
        "proposed_at", "processed_at", "has_items", "member_id",
        "related_transaction_id", "is_pending", "from_lineup_slot_id",
        "to_lineup_slot_id", "is_keeper",
    ),
    key=("season", "transaction_id", "item_index"),
    timestamp_columns=frozenset({"proposed_at", "processed_at"}),
    description="One row per transaction *item* (ESPN: header + items[] array).",
)

# --- derived -------------------------------------------------------------

SEASON_CHAMPIONS = Table(
    name="season_champions",
    columns=(
        "season", "championship_week", "champion_team_id", "champion",
        "champion_member", "champion_score", "runner_up_team_id", "runner_up",
        "runner_up_member", "runner_up_score",
    ),
    key=("season",),
    derived=True,
    description="The final WINNERS_BRACKET matchup of each season.",
)

TRADES = Table(
    name="trades",
    columns=(
        "season", "transaction_id", "item_index", "week", "processed_at",
        "from_team_id", "from_team", "to_team_id", "to_team",
        "player_id", "player_name",
    ),
    key=("season", "transaction_id", "item_index"),
    timestamp_columns=frozenset({"processed_at"}),
    derived=True,
    description="Trade legs, for the trades ESPN reported with full detail.",
)

MEMBER_RECORDS = Table(
    name="member_records",
    columns=(
        "member_id", "display_name", "seasons", "wins", "losses", "ties",
        "win_pct", "points_for", "points_against", "titles",
        "runner_up_finishes", "playoff_appearances", "best_finish",
    ),
    key=("member_id",),
    derived=True,
    description="All-time record per person, following them across renames.",
)


#: Load order matters: parents before children, so a Postgres sink with
#: foreign keys can insert straight down the list.
ALL_TABLES: tuple[Table, ...] = (
    SEASONS, MEMBERS, TEAMS, TEAM_OWNERS, PLAYERS, MATCHUPS, ROSTER_SLOTS,
    DRAFT_PICKS, TRANSACTIONS, SEASON_CHAMPIONS, TRADES, MEMBER_RECORDS,
)

BY_NAME: dict[str, Table] = {t.name: t for t in ALL_TABLES}
