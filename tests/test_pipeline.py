"""The fetch loop, driven by a stand-in for ESPN.

No network. The fake records what was asked for, which is how the interesting
behaviour gets asserted: that a re-run skips completed units, that an
in-progress season is capped at the week it has actually played, and that bad
credentials stop the run instead of failing several hundred times.
"""

from __future__ import annotations

import conftest
import pytest
from conftest import LEAGUE_ID
from espn_api.requests.espn_requests import ESPNAccessDenied

from espn_fantasy.config import Config
from espn_fantasy.errors import FetchError
from espn_fantasy.espn import SEASON_VIEWS, WEEKLY_VIEWS
from espn_fantasy.pipeline import run_fetch
from espn_fantasy.store.sqlite import SqliteRawStore


class FakeEspn:
    """Answers like ESPN, and remembers every question."""

    def __init__(self, *, latest_period: int = 3, fail_on: set | None = None,
                 fatal_on: set | None = None) -> None:
        self.latest_period = latest_period
        self.fail_on = fail_on or set()
        self.fatal_on = fatal_on or set()
        self.calls: list[tuple[int, str, int | None]] = []

    def fetch_view(self, season, view, scoring_period=None):
        self.calls.append((season, view, scoring_period))
        if (season, view, scoring_period) in self.fatal_on:
            raise ESPNAccessDenied("cookies expired")
        if (season, view, scoring_period) in self.fail_on:
            raise FetchError("ESPNUnknownError: ESPN returned an HTTP 500")
        payloads = {
            "mSettings": conftest.settings_payload(
                season, latest_period=self.latest_period),
            "mTeam": conftest.team_payload(season),
            "mMatchupScore": conftest.matchup_payload(season),
            "mDraftDetail": conftest.draft_payload(season),
            "mBoxscore": conftest.boxscore_payload(season, scoring_period or 1),
            "mTransactions2": conftest.transaction_payload(
                season, scoring_period or 1),
        }
        return payloads[view], 200, f"https://example/{view}"

    def discover_seasons(self):
        return [2023, 2024]


@pytest.fixture
def fetch_cfg(tmp_path):
    return Config(league_id=str(LEAGUE_ID), espn_s2="cookie",
                  swid="{0000-0000}", seasons=[2023],
                  raw_db_path=str(tmp_path / "raw.sqlite3"),
                  out_dir=str(tmp_path / "out"), delay=0.0, max_week=5)


def fetch(cfg, fake, **kwargs):
    with SqliteRawStore(cfg.raw_db_path) as store:
        report = run_fetch(store, cfg, **kwargs)
        return report, store.completed_units()


def test_settings_are_fetched_before_the_weeks_they_cap(fetch_cfg, monkeypatch):
    fake = FakeEspn()
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    fetch(fetch_cfg, fake)
    assert fake.calls[0] == (2023, "mSettings", None)


def test_weeks_are_capped_at_the_latest_scoring_period(fetch_cfg, monkeypatch):
    """A request for a future week returns today's roster, not an empty one,
    so an uncapped fetch would record the same lineups once per week."""
    fake = FakeEspn(latest_period=2)
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    report, _ = fetch(fetch_cfg, fake)

    requested_weeks = {p for _, v, p in fake.calls if v == "mBoxscore"}
    assert requested_weeks == {1, 2}, "weeks 3-5 have not been played"
    assert report.planned == len(SEASON_VIEWS) + 2 * len(WEEKLY_VIEWS)


def test_max_week_narrows_but_never_widens(fetch_cfg, monkeypatch):
    fetch_cfg.max_week = 1
    fake = FakeEspn(latest_period=17)
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    fetch(fetch_cfg, fake)
    assert {p for _, v, p in fake.calls if v == "mBoxscore"} == {1}


def test_explicit_weeks_are_honoured(fetch_cfg, monkeypatch):
    fetch_cfg.weeks = [2, 3]
    fake = FakeEspn(latest_period=17)
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    fetch(fetch_cfg, fake)
    assert {p for _, v, p in fake.calls if v == "mBoxscore"} == {2, 3}


def test_a_rerun_fetches_nothing(fetch_cfg, monkeypatch):
    fake = FakeEspn()
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    first, _ = fetch(fetch_cfg, fake)
    calls_after_first = len(fake.calls)

    second, _ = fetch(fetch_cfg, fake)
    assert len(fake.calls) == calls_after_first, "nothing should be re-requested"
    assert second.fetched == 0
    assert second.skipped == first.planned


def test_force_refetch_ignores_the_ledger(fetch_cfg, monkeypatch):
    fake = FakeEspn()
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    first, _ = fetch(fetch_cfg, fake)
    before = len(fake.calls)
    second, _ = fetch(fetch_cfg, fake, force=True)
    assert len(fake.calls) == before + second.planned


def test_only_failed_units_are_retried(fetch_cfg, monkeypatch):
    failing = (2023, "mBoxscore", 2)
    fake = FakeEspn(fail_on={failing})
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    first, done = fetch(fetch_cfg, fake)
    assert first.failed == 1
    assert failing not in done

    fake.fail_on.clear()
    fake.calls.clear()
    second, done = fetch(fetch_cfg, fake)
    assert fake.calls == [failing]
    assert second.fetched == 1
    assert failing in done


def test_expired_cookies_stop_the_run_immediately(fetch_cfg, monkeypatch):
    """Access denied will fail identically for every remaining unit."""
    fake = FakeEspn(fatal_on={(2023, "mTeam", None)})
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    with pytest.raises(FetchError, match="cookies have expired"):
        fetch(fetch_cfg, fake)
    assert len(fake.calls) == 2, "should not have carried on to the weeks"


def test_seasons_are_discovered_when_not_specified(fetch_cfg, monkeypatch):
    fetch_cfg.seasons = None
    fetch_cfg.max_week = 1
    fake = FakeEspn()
    monkeypatch.setattr("espn_fantasy.pipeline.EspnClient", lambda cfg: fake)
    fetch(fetch_cfg, fake)
    assert {season for season, _, _ in fake.calls} == {2023, 2024}
