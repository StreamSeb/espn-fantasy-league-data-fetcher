"""ESPN transport.

Transport comes from espn_api's EspnFantasyRequests, which already owns the
current lm-api-reads host, the cookie auth, and the 2018+/leagueHistory
endpoint switching. It is subclassed only to expose the HTTP status and final
URL, which the library otherwise swallows but raw_responses records.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date
from typing import Any

import requests
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    EspnFantasyRequests,
    ESPNInvalidLeague,
    ESPNUnknownError,
)

from .config import EARLIEST_SEASON, Config
from .errors import FetchError

log = logging.getLogger(__name__)

#: Season-level views, fetched once per season.
SEASON_VIEWS = ("mSettings", "mTeam", "mMatchupScore", "mDraftDetail")

#: mTransactions2 belongs with the weekly views, not the season-level ones:
#: without a scoringPeriodId parameter ESPN returns HTTP 200 and simply omits
#: the `transactions` key entirely - no error, just silently no data. It has
#: to be requested one scoring period at a time.
BOXSCORE_VIEW = "mBoxscore"
TRANSACTIONS_VIEW = "mTransactions2"
WEEKLY_VIEWS = (BOXSCORE_VIEW, TRANSACTIONS_VIEW)

#: Errors that mean "ESPN is unhappy right now"; worth a retry with backoff.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: Errors that will never succeed on retry, however many times we ask.
FATAL_EXCEPTIONS = (ESPNAccessDenied, ESPNInvalidLeague)


class RawEspnRequests(EspnFantasyRequests):
    """EspnFantasyRequests that also reports the HTTP status and final URL.

    The parent returns only the decoded body. checkRequestStatus is reused
    unchanged: it carries the 401 fallback to the alternate endpoint shape and
    ESPN's error classification.
    """

    timeout: float = 30.0

    def raw_league_get(self, params: dict | None = None,
                       headers: dict | None = None) -> tuple[Any, int, str]:
        response = requests.get(
            self.LEAGUE_ENDPOINT, params=params, headers=headers,
            cookies=self.cookies, timeout=self.timeout)
        alternate = self.checkRequestStatus(
            response.status_code, extend="", params=params, headers=headers)
        if alternate is not None:
            # The 401 fallback fired; LEAGUE_ENDPOINT now holds the URL used.
            return unwrap(alternate), 200, self.LEAGUE_ENDPOINT
        return unwrap(response.json()), response.status_code, response.url


def unwrap(payload: Any) -> Any:
    """leagueHistory-shaped responses arrive wrapped in a single-element list."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload


class EspnClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cookies = {"espn_s2": cfg.espn_s2, "SWID": cfg.swid}
        self._clients: dict[int, RawEspnRequests] = {}

    def _client(self, season: int) -> RawEspnRequests:
        if season not in self._clients:
            client = RawEspnRequests(
                sport="nfl", year=season, league_id=int(self.cfg.league_id),
                cookies=self.cookies)
            client.timeout = self.cfg.timeout
            self._clients[season] = client
        return self._clients[season]

    def fetch_view(self, season: int, view: str,
                   scoring_period: int | None = None) -> tuple[Any, int, str]:
        """Fetch one view, retrying transient failures with backoff."""
        params: dict[str, Any] = {"view": view}
        if scoring_period is not None:
            params["scoringPeriodId"] = scoring_period

        attempts = max(1, self.cfg.retries)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._client(season).raw_league_get(params=params)
            except FATAL_EXCEPTIONS:
                raise
            except (ESPNUnknownError, requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt == attempts or not _is_retryable(exc):
                    break
                # Full jitter: several seasons' worth of requests hitting a
                # rate limit should not all come back at the same instant.
                delay = min(30.0, 2 ** attempt) * (0.5 + random.random() / 2)
                log.warning("  %s (attempt %d/%d), retrying in %.1fs",
                            exc, attempt, attempts, delay)
                time.sleep(delay)
        raise FetchError(f"{type(last_exc).__name__}: {last_exc}") from last_exc

    def discover_seasons(self) -> list[int]:
        """Ask ESPN which seasons this league actually has.

        `status.previousSeasons` lists every completed season the league has
        ever played. Probing the current year first also picks up an
        in-progress season, which previousSeasons omits by definition.
        """
        this_year = date.today().year
        for probe in (this_year, this_year - 1):
            try:
                payload, _, _ = self.fetch_view(probe, "mSettings")
            except (FetchError, *FATAL_EXCEPTIONS) as exc:
                log.debug("season discovery: %d unavailable (%s)", probe, exc)
                continue
            status = payload.get("status") or {}
            previous = [int(s) for s in (status.get("previousSeasons") or [])]
            seasons = sorted({*previous, probe})
            usable = [s for s in seasons if s >= EARLIEST_SEASON]
            if usable:
                dropped = len(seasons) - len(usable)
                if dropped:
                    log.warning(
                        "%d season(s) before %d are not served by this API "
                        "shape and were skipped", dropped, EARLIEST_SEASON)
                return usable
        raise FetchError(
            "could not discover seasons: ESPN would not serve mSettings for "
            f"{this_year} or {this_year - 1}. Check the league id and cookies, "
            "or name the seasons explicitly with --seasons 2018-2024.")


def latest_scoring_period(settings_payload: dict | None) -> int | None:
    """The highest scoring period that has actually been played.

    Requesting a boxscore for a week that has not happened yet does not return
    an empty roster - ESPN returns the *current* roster instead. Fetching all
    18 weeks of an in-progress season therefore writes eighteen identical
    copies of today's lineups into roster_slots as if they were real weekly
    results. Capping at this value is what prevents that.
    """
    status = (settings_payload or {}).get("status") or {}
    candidates = [status.get("latestScoringPeriod"),
                  status.get("finalScoringPeriod")]
    values = [int(c) for c in candidates if isinstance(c, int) and c > 0]
    return min(values) if values else None


def _is_retryable(exc: Exception) -> bool:
    """Timeouts and connection blips always are; HTTP errors depend on code.

    espn_api reports every non-200 as ESPNUnknownError("ESPN returned an HTTP
    <code>"), so the code has to be read back out of the message to tell a
    rate limit apart from a malformed request.
    """
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, ESPNUnknownError):
        digits = "".join(c for c in str(exc) if c.isdigit())
        return not digits or int(digits[:3]) in RETRYABLE_STATUS
    # A ValueError here is a JSON decode failure, which ESPN does return
    # intermittently under load in place of a proper 5xx.
    return isinstance(exc, ValueError)
