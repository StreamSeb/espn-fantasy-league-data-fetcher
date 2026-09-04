"""The command-line surface: exit codes, and that a bad flag fails fast."""

from __future__ import annotations

from pathlib import Path

import pytest

from ffl_history.cli import build_parser, main


def test_help_lists_every_command(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    for command in ("run", "fetch", "parse", "export", "summary", "doctor",
                    "init-env"):
        assert command in out


def test_help_documents_every_output_format(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for fmt in ("sqlite", "postgres", "csv", "parquet", "xlsx", "duckdb", "raw"):
        assert fmt in out


def test_unknown_format_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--format", "hieroglyphs"])


def test_weeks_and_max_week_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--weeks", "1-5", "--max-week", "9"])


def test_missing_credentials_exit_with_advice_not_a_traceback(capsys, tmp_path):
    code = main(["fetch", "--env-file", str(tmp_path / "absent"),
                 "--raw-db", str(tmp_path / "raw.sqlite3")])
    assert code == 2
    assert "ESPN_LEAGUE_ID" in capsys.readouterr().err


def test_summary_of_a_stored_league(capsys, populated_store):
    code = main(["summary", "--league-id", "999999",
                 "--raw-db", str(populated_store),
                 "--env-file", "/nonexistent"])
    assert code == 0
    out = capsys.readouterr().out
    assert "CHAMPIONS" in out
    assert "Alice FC 2023" in out
    assert "ALL-TIME RECORDS" in out


def test_export_writes_requested_formats(capsys, tmp_path, populated_store):
    code = main(["export", "--league-id", "999999",
                 "--raw-db", str(populated_store),
                 "--out", str(tmp_path / "out"),
                 "--format", "csv", "--format", "json",
                 "--env-file", "/nonexistent"])
    assert code == 0
    assert (tmp_path / "out" / "csv" / "teams.csv").exists()
    assert (tmp_path / "out" / "json" / "teams.json").exists()
    assert "WROTE" in capsys.readouterr().out


def test_init_env_refuses_to_clobber(tmp_path, capsys):
    target = tmp_path / ".env"
    assert main(["init-env", "--path", str(target)]) == 0
    assert target.exists()
    assert oct(target.stat().st_mode)[-3:] == "600"
    assert main(["init-env", "--path", str(target)]) == 1
    assert "not overwriting" in capsys.readouterr().out


def test_packaged_env_template_matches_the_repository_copy():
    """`init-env` reads the packaged copy, because a wheel has no repo root.
    The root .env.example is what people read on GitHub. Keep them identical."""
    from ffl_history.cli import ENV_TEMPLATE

    root = Path(__file__).resolve().parent.parent / ".env.example"
    assert ENV_TEMPLATE.read_text() == root.read_text(), (
        "ffl_history/env.example and .env.example have drifted; "
        "copy one over the other")
