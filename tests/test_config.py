"""Season and week specs, and the precedence rules between config sources."""

from __future__ import annotations

import argparse

import pytest

from espn_fantasy.config import Config, parse_int_spec, parse_seasons
from espn_fantasy.errors import ConfigError


@pytest.mark.parametrize("spec,expected", [
    ("2023", [2023]),
    ("2018-2020", [2018, 2019, 2020]),
    ("2019,2021,2024", [2019, 2021, 2024]),
    ("2018-2019,2023", [2018, 2019, 2023]),
    (" 2023 , 2024 ", [2023, 2024]),
    ("2023,2023", [2023]),
    ("2024,2023", [2023, 2024]),
])
def test_season_specs(spec, expected):
    assert parse_seasons(spec) == expected


@pytest.mark.parametrize("spec", ["auto", "AUTO", "all"])
def test_auto_means_discover(spec):
    assert parse_seasons(spec) is None


@pytest.mark.parametrize("spec,message", [
    ("2024-2020", "backwards"),
    ("banana", "could not parse"),
    ("2010", "before 2018"),
    ("", "no season"),
])
def test_bad_season_specs_explain_themselves(spec, message):
    with pytest.raises(ConfigError, match=message):
        parse_seasons(spec)


def test_week_specs():
    assert parse_int_spec("1-3,7", what="week") == [1, 2, 3, 7]


def test_flags_beat_the_environment(monkeypatch):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "111")
    monkeypatch.setenv("ESPN_SEASONS", "2020-2021")
    cfg = Config.from_env()
    assert cfg.league_id == "111"
    assert cfg.seasons == [2020, 2021]

    cfg.apply_args(argparse.Namespace(league_id="222", seasons="2024"))
    assert cfg.league_id == "222"
    assert cfg.seasons == [2024]


def test_first_and_last_season_still_work(monkeypatch):
    """The pre-1.0 setting names remain supported."""
    monkeypatch.setenv("ESPN_FIRST_SEASON", "2021")
    monkeypatch.setenv("ESPN_LAST_SEASON", "2023")
    assert Config.from_env().seasons == [2021, 2022, 2023]


def test_credentials_are_validated_not_just_present():
    cfg = Config(league_id="abc", espn_s2="x", swid="{y}")
    with pytest.raises(ConfigError, match="must be numeric"):
        cfg.require_espn_credentials()

    cfg = Config(league_id="123", espn_s2="x", swid="no-braces")
    with pytest.raises(ConfigError, match="surrounding braces"):
        cfg.require_espn_credentials()


def test_missing_credentials_name_all_of_them():
    with pytest.raises(ConfigError) as exc:
        Config().require_espn_credentials()
    for name in ("ESPN_LEAGUE_ID", "ESPN_S2", "SWID"):
        assert name in str(exc.value)


def test_secrets_are_masked_for_display():
    cfg = Config(league_id="123", espn_s2="A" * 200, swid="{1234-5678}")
    shown = cfg.redacted()
    assert "A" * 20 not in shown["espn_s2"]
    assert "200 chars" in shown["espn_s2"]


def test_dsn_omits_an_empty_password():
    assert "password" not in Config().dsn
    assert "password=s3cret" in Config(pg_password="s3cret").dsn
