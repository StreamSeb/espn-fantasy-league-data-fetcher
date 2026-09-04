"""The pre-commit hook, exercised against real staged commits.

A guard that is never tested is a guard that quietly stops working. Each case
here builds a throwaway repository, stages something, and asserts whether the
commit is allowed through - so both the blocking and the *not* blocking are
covered. False positives matter as much as misses: a hook that cries wolf gets
disabled within a week.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".githooks" / "pre-commit"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    hooks = path / ".githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-commit")
    (hooks / "pre-commit").chmod(0o755)
    git(path, "config", "core.hooksPath", ".githooks")
    # A first commit, so the hook has a diff to inspect on later ones.
    (path / "README.md").write_text("# test\n")
    git(path, "add", "README.md")
    git(path, "commit", "-qm", "initial")
    return path


def commit(repo: Path, message: str = "wip") -> subprocess.CompletedProcess:
    return git(repo, "commit", "-m", message)


def stage(repo: Path, name: str, content: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    # -f, because a realistic repo gitignores these; the point of the hook is
    # to be the backstop when .gitignore is bypassed.
    git(repo, "add", "-f", name)


# --- things that must be blocked -------------------------------------------

def test_env_file_is_blocked(repo):
    stage(repo, ".env", "ESPN_LEAGUE_ID=123\n")
    result = commit(repo)
    assert result.returncode != 0
    assert ".env is staged" in result.stderr


def test_a_pasted_cookie_is_blocked_even_in_an_ordinary_file(repo):
    """The case .gitignore cannot catch: a cookie in a README or a test."""
    stage(repo, "notes.md", "here is my cookie:\nESPN_S2=" + "AEB%2F" * 60 + "\n")
    result = commit(repo)
    assert result.returncode != 0
    assert "ESPN_S2 value looks pasted" in result.stderr


def test_a_real_swid_guid_is_blocked(repo):
    stage(repo, "bug_report.md",
          "SWID={1A2B3C4D-5E6F-7081-92A3-B4C5D6E7F809}\n")  # allowlist secret
    result = commit(repo)
    assert result.returncode != 0
    assert "SWID value looks pasted" in result.stderr


def test_extracted_data_is_blocked(repo):
    stage(repo, "espn_fantasy.sqlite3", "SQLite format 3\x00")
    assert commit(repo).returncode != 0

    git(repo, "reset", "-q")
    stage(repo, "out/csv/teams.csv", "season,team_id\n2024,1\n")
    result = commit(repo)
    assert result.returncode != 0
    assert "out/" in result.stderr


def test_a_real_looking_database_password_is_blocked(repo):
    stage(repo, "docker-compose.yml",
          "      POSTGRES_PASSWORD: hunter2CorrectHorseBattery\n")  # allowlist secret
    assert commit(repo).returncode != 0


# --- things that must NOT be blocked ---------------------------------------

def test_ordinary_source_commits_are_untouched(repo):
    stage(repo, "espn_fantasy/parse.py", "def parse():\n    return []\n")
    result = commit(repo)
    assert result.returncode == 0, result.stderr


def test_the_env_template_is_allowed(repo):
    """.env.example is meant to be committed; it has the names, not values."""
    stage(repo, ".env.example",
          "ESPN_LEAGUE_ID=\nESPN_S2=\nSWID=\nPOSTGRES_PASSWORD=change-me\n")
    result = commit(repo)
    assert result.returncode == 0, result.stderr


def test_documentation_describing_the_cookies_is_allowed(repo):
    """The README has to be able to explain what these look like."""
    stage(repo, "README.md", (
        "# test\n\n"
        "| `espn_s2` | `AEB...%2F...` (very long) | paste verbatim |\n"
        "| `SWID` | `{1A2B3C4D-...}` | include the braces |\n\n"
        "Set ESPN_S2 and SWID in your .env file.\n"))
    result = commit(repo)
    assert result.returncode == 0, result.stderr


def test_placeholder_passwords_are_allowed(repo):
    stage(repo, "compose.yml", "  POSTGRES_PASSWORD: change-me\n")
    assert commit(repo).returncode == 0


def test_no_verify_still_works_as_an_escape_hatch(repo):
    stage(repo, ".env", "ESPN_S2=secret\n")
    assert git(repo, "commit", "--no-verify", "-m", "forced").returncode == 0


def test_an_explicit_allowlist_marker_is_respected(repo):
    """Documentation and this file's own fixtures need well-formed fakes."""
    stage(repo, "docs.md",
          "SWID={1A2B3C4D-5E6F-7081-92A3-B4C5D6E7F809}  <!-- allowlist secret -->\n")
    result = commit(repo)
    assert result.returncode == 0, result.stderr


def test_the_marker_only_exempts_its_own_line(repo):
    """An allowlisted line must not open the gate for the rest of the file."""
    stage(repo, "docs.md",
          "SWID={1A2B3C4D-5E6F-7081-92A3-B4C5D6E7F809}  <!-- allowlist secret -->\n"
          "ESPN_S2=" + "AEB%2F" * 60 + "\n")
    assert commit(repo).returncode != 0


def test_throwaway_ci_credentials_are_allowed(repo):
    """CI needs a password in plain sight; it must not look like a leak."""
    stage(repo, ".github/workflows/ci.yml",
          "        env:\n"
          "          POSTGRES_USER: espn\n"
          "          POSTGRES_PASSWORD: espn\n")
    result = commit(repo)
    assert result.returncode == 0, result.stderr
