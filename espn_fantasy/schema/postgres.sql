-- ===========================================================================
-- ESPN fantasy football league history - normalized schema (Postgres 15+).
--
-- Applied two ways, both idempotent:
--   1. by postgres' docker-entrypoint-initdb.d on first init of the volume
--   2. by the postgres sink on every run (migration step)
-- Every statement is IF NOT EXISTS / OR REPLACE, so re-application is a no-op.
--
-- The raw_responses and fetch_log tables are created by the raw store, not
-- here: they exist whether or not Postgres is an output target.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- seasons: settings snapshot per year.
--
-- Roster slots, scoring rules and playoff size drift between seasons, so the
-- full mSettings blob is kept alongside the extracted headline values.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seasons (
    season                  integer PRIMARY KEY,
    league_id               bigint,
    league_name             text,
    team_count              integer,
    scoring_type            text,       -- H2H_POINTS, H2H_CATEGORY, ...
    playoff_team_count      integer,
    regular_season_length   integer,    -- matchup periods before the playoffs
    matchup_period_count    integer,
    final_scoring_period    integer,
    roster_slot_counts      jsonb,      -- {"0": 1, "2": 2, "20": 6, ...}
    scoring_settings        jsonb,      -- full scoringItems, keyed by stat id
    draft_type              text,
    settings_raw            jsonb,      -- everything not pulled out above
    updated_at              timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- members: keyed on the ESPN member GUID.
--
-- This is the table that survives team-name churn - the GUID is stable across
-- seasons, so a person can be followed even when their team is renamed yearly.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS members (
    member_id           text PRIMARY KEY,   -- ESPN GUID, e.g. {1A2B-...}
    display_name        text,
    first_name          text,
    last_name           text,
    first_seen_season   integer,
    last_seen_season    integer,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- teams: one row per team per season.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    season              integer NOT NULL,
    team_id             integer NOT NULL,
    -- Primary owner. NULL for orphaned or unclaimed teams; co-owners live in
    -- team_owners below.
    member_id           text REFERENCES members (member_id),
    team_name           text,
    location            text,
    nickname            text,
    abbrev              text,
    logo_url            text,
    wins                integer,
    losses              integer,
    ties                integer,
    points_for          numeric(10, 2),
    points_against      numeric(10, 2),
    playoff_seed        integer,
    final_rank          integer,
    regular_season_rank integer,
    waiver_rank         integer,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season, team_id),
    FOREIGN KEY (season) REFERENCES seasons (season)
);

CREATE INDEX IF NOT EXISTS teams_member_idx ON teams (member_id);

-- ESPN allows co-owned teams, so ownership is many-to-many.
CREATE TABLE IF NOT EXISTS team_owners (
    season      integer NOT NULL,
    team_id     integer NOT NULL,
    member_id   text    NOT NULL REFERENCES members (member_id),
    PRIMARY KEY (season, team_id, member_id),
    FOREIGN KEY (season, team_id) REFERENCES teams (season, team_id)
);

-- ---------------------------------------------------------------------------
-- players: convenience dimension, populated opportunistically from whatever
-- rosters, drafts and transactions reference. Fact tables also carry a
-- denormalized player_name so they stay readable if a player was never on a
-- roster. Deliberately not FK-referenced, so a fact never fails to load
-- because a player blob was missing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    player_id           bigint PRIMARY KEY,
    full_name           text,
    default_position_id integer,
    position            text,
    pro_team_id         integer,
    pro_team            text,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- matchups: one row per scheduled game.
--
-- playoff_tier_type separates NONE (regular season) from WINNERS_BRACKET
-- (championship) and LOSERS_CONSOLATION_LADDER (consolation), so consolation
-- games never pollute a championship query.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matchups (
    season              integer NOT NULL,
    espn_matchup_id     integer NOT NULL,   -- `id` within the schedule array
    -- matchupPeriodId. NOTE: this is NOT the same unit as roster_slots.week,
    -- which is a scoringPeriodId. In the regular season they coincide, but in
    -- many leagues a playoff matchup period spans TWO scoring periods, so
    -- playoff scores here are two-week totals. Only join matchups.week to
    -- roster_slots.week where playoff_tier_type = 'NONE'.
    week                integer,
    home_team_id        integer,
    away_team_id        integer,            -- NULL on a bye
    home_score          numeric(10, 2),
    away_score          numeric(10, 2),
    home_tiebreak       numeric(10, 2),
    away_tiebreak       numeric(10, 2),
    winner              text,               -- HOME | AWAY | TIE | UNDECIDED
    playoff_tier_type   text,
    is_bye              boolean NOT NULL DEFAULT false,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season, espn_matchup_id),
    FOREIGN KEY (season) REFERENCES seasons (season)
);

CREATE INDEX IF NOT EXISTS matchups_season_week_idx ON matchups (season, week);
CREATE INDEX IF NOT EXISTS matchups_tier_idx ON matchups (season, playoff_tier_type);

-- ---------------------------------------------------------------------------
-- roster_slots: per team, per week, per player.
--
-- Sourced from mBoxscore, the only view carrying started/bench state and
-- actual-vs-projected points together.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roster_slots (
    season              integer NOT NULL,
    week                integer NOT NULL,   -- scoringPeriodId; see matchups.week
    team_id             integer NOT NULL,
    player_id           bigint  NOT NULL,
    player_name         text,
    lineup_slot_id      integer,
    lineup_slot         text,               -- QB, RB, FLEX, BE, IR, ...
    started             boolean,
    actual_points       numeric(10, 2),
    projected_points    numeric(10, 2),
    position            text,
    pro_team_id         integer,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season, week, team_id, player_id),
    FOREIGN KEY (season, team_id) REFERENCES teams (season, team_id)
);

CREATE INDEX IF NOT EXISTS roster_slots_player_idx ON roster_slots (player_id);
CREATE INDEX IF NOT EXISTS roster_slots_started_idx
    ON roster_slots (season, week, team_id) WHERE started;

-- ---------------------------------------------------------------------------
-- draft_picks: bid_amount is populated for auction leagues, 0/NULL for snake.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS draft_picks (
    season              integer NOT NULL,
    overall_pick        integer NOT NULL,
    round               integer,
    round_pick          integer,
    team_id             integer,
    player_id           bigint,
    player_name         text,
    bid_amount          numeric(10, 2),
    keeper              boolean,
    nominating_team_id  integer,
    auto_draft_type     text,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season, overall_pick),
    FOREIGN KEY (season) REFERENCES seasons (season)
);

CREATE INDEX IF NOT EXISTS draft_picks_team_idx ON draft_picks (season, team_id);

-- ---------------------------------------------------------------------------
-- transactions: waiver claims, free agent adds/drops and trade headers.
--
-- ESPN models one transaction as a header plus an items[] array, each item
-- being one player moving, so this is one row per item. Most TRADE_ACCEPT
-- rows come back with an empty items[]; those are stored header-only, with
-- has_items = false, and deliberately not reconstructed.
--
-- Rows with a FAILED_* status are kept on purpose: a failed waiver claim is
-- still league history, since it records who bid what on whom. Filter on
-- status = 'EXECUTED' for moves that actually happened.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    season              integer NOT NULL,
    transaction_id      text    NOT NULL,   -- ESPN transaction id
    item_index          integer NOT NULL,   -- position in items[]; 0 if header-only
    type                text,               -- WAIVER | FREEAGENT | TRADE_ACCEPT | ...
    status              text,               -- EXECUTED | CANCELED | FAILED_* | ...
    execution_type      text,
    item_type           text,               -- ADD | DROP | LINEUP
    team_id             integer,
    from_team_id        integer,
    to_team_id          integer,
    player_id           bigint,
    player_name         text,
    bid_amount          numeric(10, 2),
    scoring_period      integer,
    proposed_at         timestamptz,
    processed_at        timestamptz,
    has_items           boolean NOT NULL DEFAULT true,
    -- The acting member. Automated waiver runs report a processor name rather
    -- than a real GUID, so this is not FK'd to members.
    member_id           text,
    related_transaction_id text,
    is_pending          boolean,
    from_lineup_slot_id integer,
    to_lineup_slot_id   integer,
    is_keeper           boolean,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season, transaction_id, item_index),
    FOREIGN KEY (season) REFERENCES seasons (season)
);

CREATE INDEX IF NOT EXISTS transactions_team_idx ON transactions (season, team_id);
CREATE INDEX IF NOT EXISTS transactions_type_idx ON transactions (season, type);
CREATE INDEX IF NOT EXISTS transactions_player_idx ON transactions (player_id);
CREATE INDEX IF NOT EXISTS transactions_status_idx ON transactions (season, status);

-- ===========================================================================
-- Derived tables.
--
-- Computed in Python and rebuilt wholesale on every parse, so that a CSV or
-- Parquet export carries them too rather than them existing only as SQL views
-- that database users get and file users do not.
-- ===========================================================================

-- The final decided WINNERS_BRACKET matchup of each season.
CREATE TABLE IF NOT EXISTS season_champions (
    season              integer PRIMARY KEY REFERENCES seasons (season),
    championship_week   integer,
    champion_team_id    integer,
    champion            text,
    champion_member     text,
    champion_score      numeric(10, 2),
    runner_up_team_id   integer,
    runner_up           text,
    runner_up_member    text,
    runner_up_score     numeric(10, 2),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Trade legs, for the minority of trades ESPN reports with full item detail.
-- Trades whose contents ESPN did not return appear in transactions with
-- type = 'TRADE_ACCEPT' AND has_items = false, and are not recoverable.
CREATE TABLE IF NOT EXISTS trades (
    season          integer NOT NULL REFERENCES seasons (season),
    transaction_id  text    NOT NULL,
    item_index      integer NOT NULL,
    week            integer,
    processed_at    timestamptz,
    from_team_id    integer,
    from_team       text,
    to_team_id      integer,
    to_team         text,
    player_id       bigint,
    player_name     text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season, transaction_id, item_index)
);

-- All-time record per person, following them across team renames.
CREATE TABLE IF NOT EXISTS member_records (
    member_id           text PRIMARY KEY,
    display_name        text,
    seasons             integer,
    wins                integer,
    losses              integer,
    ties                integer,
    win_pct             numeric(6, 4),
    points_for          numeric(12, 2),
    points_against      numeric(12, 2),
    titles              integer,
    runner_up_finishes  integer,
    playoff_appearances integer,
    best_finish         integer,
    updated_at          timestamptz NOT NULL DEFAULT now()
);
