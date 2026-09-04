-- ===========================================================================
-- Example queries.
--
-- Written to run unchanged against both outputs:
--
--   sqlite3 out/ffl_history.sqlite3 < examples/queries.sql
--   psql "$DATABASE_URL" -f examples/queries.sql
--
-- so they avoid anything specific to one engine. `season_champions`,
-- `member_records` and `trades` are real tables in every output format,
-- including the CSV and Parquet exports.
-- ===========================================================================

-- --- 1. Who won, and who they beat -----------------------------------------
SELECT season, champion, champion_member, champion_score,
       runner_up, runner_up_score
FROM season_champions
ORDER BY season;

-- --- 2. All-time table -----------------------------------------------------
SELECT display_name, seasons, wins, losses, ties, win_pct,
       points_for, titles, playoff_appearances
FROM member_records
ORDER BY titles DESC, wins DESC;

-- --- 3. Final standings, season by season ----------------------------------
SELECT t.season, t.final_rank, t.team_name, m.display_name AS owner,
       t.wins, t.losses, t.points_for, t.points_against
FROM teams t
LEFT JOIN members m ON m.member_id = t.member_id
ORDER BY t.season, t.final_rank;

-- --- 4. The single best week anyone ever had -------------------------------
SELECT rs.season, rs.week, t.team_name, m.display_name AS owner,
       ROUND(SUM(rs.actual_points), 2) AS points
FROM roster_slots rs
JOIN teams t ON t.season = rs.season AND t.team_id = rs.team_id
LEFT JOIN members m ON m.member_id = t.member_id
WHERE rs.started
GROUP BY rs.season, rs.week, t.team_name, m.display_name
ORDER BY points DESC
LIMIT 10;

-- --- 5. Points left on the bench -------------------------------------------
-- The gap between what was started and the best lineup available. This is a
-- crude version -- it ignores lineup slot eligibility -- but it settles
-- arguments well enough.
SELECT t.season, t.team_name,
       ROUND(SUM(CASE WHEN rs.started THEN rs.actual_points ELSE 0 END), 2)
           AS started_points,
       ROUND(SUM(CASE WHEN NOT rs.started THEN rs.actual_points ELSE 0 END), 2)
           AS bench_points
FROM roster_slots rs
JOIN teams t ON t.season = rs.season AND t.team_id = rs.team_id
GROUP BY t.season, t.team_name
ORDER BY bench_points DESC
LIMIT 15;

-- --- 6. Biggest winning waiver bids ----------------------------------------
-- Only EXECUTED rows. Failed claims are kept in the table on purpose (they
-- record who bid what on whom), so they have to be filtered out here.
SELECT tr.season, t.team_name, tr.player_name, tr.bid_amount, tr.processed_at
FROM transactions tr
JOIN teams t ON t.season = tr.season AND t.team_id = tr.team_id
WHERE tr.type = 'WAIVER' AND tr.status = 'EXECUTED'
  AND tr.item_type = 'ADD' AND tr.bid_amount > 0
ORDER BY tr.bid_amount DESC
LIMIT 15;

-- --- 7. Draft value: where each season's top scorer was taken --------------
SELECT dp.season, dp.round, dp.overall_pick, dp.player_name,
       t.team_name AS drafted_by,
       ROUND(SUM(rs.actual_points), 2) AS season_points
FROM draft_picks dp
JOIN roster_slots rs ON rs.season = dp.season AND rs.player_id = dp.player_id
LEFT JOIN teams t ON t.season = dp.season AND t.team_id = dp.team_id
GROUP BY dp.season, dp.round, dp.overall_pick, dp.player_name, t.team_name
ORDER BY dp.season, season_points DESC;

-- --- 8. Trades ESPN actually reported --------------------------------------
-- Trades missing from here appear in `transactions` with
-- type = 'TRADE_ACCEPT' AND has_items = 0/false: ESPN confirms they happened
-- but does not return the players, so their contents are not recoverable.
SELECT season, week, from_team, to_team, player_name
FROM trades
ORDER BY season, transaction_id, item_index;

-- --- 9. Head-to-head, regular season only ----------------------------------
SELECT m.season, hm.display_name AS home_owner, m.home_score,
       am.display_name AS away_owner, m.away_score, m.winner
FROM matchups m
LEFT JOIN teams ht ON ht.season = m.season AND ht.team_id = m.home_team_id
LEFT JOIN teams at ON at.season = m.season AND at.team_id = m.away_team_id
LEFT JOIN members hm ON hm.member_id = ht.member_id
LEFT JOIN members am ON am.member_id = at.member_id
WHERE m.playoff_tier_type = 'NONE' AND NOT m.is_bye
ORDER BY m.season, m.week;

-- --- 10. Settings drift between seasons ------------------------------------
SELECT season, league_name, team_count, playoff_team_count,
       regular_season_length, draft_type, scoring_type
FROM seasons
ORDER BY season;

-- --- 11. Coverage check ----------------------------------------------------
-- How many weeks of boxscore data actually landed, per season.
SELECT season, COUNT(DISTINCT week) AS weeks,
       MIN(week) AS first_week, MAX(week) AS last_week, COUNT(*) AS slot_rows
FROM roster_slots
GROUP BY season
ORDER BY season;

-- --- 12. Score reconciliation ----------------------------------------------
-- Each team's matchup score against the sum of its started lineup. Regular
-- season only: a playoff matchup period can span two scoring periods, so
-- matchups.week and roster_slots.week are not comparable there. A mismatch
-- means ESPN applied a stat correction after the fact.
SELECT m.season, m.week, m.home_team_id AS team_id, m.home_score,
       ROUND(SUM(rs.actual_points), 2) AS lineup_sum
FROM matchups m
JOIN roster_slots rs ON rs.season = m.season AND rs.week = m.week
                    AND rs.team_id = m.home_team_id AND rs.started
WHERE m.playoff_tier_type = 'NONE' AND NOT m.is_bye
GROUP BY m.season, m.week, m.home_team_id, m.home_score
HAVING ABS(m.home_score - SUM(rs.actual_points)) > 0.5
ORDER BY m.season, m.week;
