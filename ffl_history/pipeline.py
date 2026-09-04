"""Fetch, parse and export, wired together.

The fetch phase writes raw payloads and a ledger row per unit of work. The
parse phase reads only from that store. Export writes the parsed rows to every
requested format. Each phase is independently runnable, which is what makes a
re-parse or a new output format free of any further requests to ESPN.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .config import Config
from .errors import FetchError, FflError
from .espn import (
    FATAL_EXCEPTIONS,
    SEASON_VIEWS,
    WEEKLY_VIEWS,
    EspnClient,
    latest_scoring_period,
)
from .parse import Dataset, parse_store
from .sinks import Sink, SinkResult, build_sinks
from .store import RawRow, RawStore

log = logging.getLogger(__name__)


@dataclass
class FetchReport:
    planned: int = 0
    skipped: int = 0
    fetched: int = 0
    failed: int = 0

    @property
    def todo(self) -> int:
        return self.planned - self.skipped


def resolve_seasons(cfg: Config, client: EspnClient | None = None) -> list[int]:
    if cfg.seasons:
        return cfg.seasons
    if client is None:
        raise FetchError("cannot discover seasons without network access; "
                         "name them with --seasons")
    log.info("discovering seasons from ESPN...")
    seasons = client.discover_seasons()
    log.info("found %d season(s): %s", len(seasons),
             ", ".join(map(str, seasons)))
    return seasons


def run_fetch(store: RawStore, cfg: Config, *, force: bool = False) -> FetchReport:
    """Fetch every missing unit of work, one season at a time."""
    cfg.require_espn_credentials()
    client = EspnClient(cfg)
    seasons = resolve_seasons(cfg, client)
    done = set() if force else store.completed_units()
    report = FetchReport()

    for season in seasons:
        # mSettings comes first on purpose: status.latestScoringPeriod is what
        # caps the weekly requests, and without it an in-progress season
        # silently fills roster_slots with copies of today's lineups.
        units: list[tuple[int, str, int | None]] = [
            (season, view, None) for view in SEASON_VIEWS]
        pending = [u for u in units if u not in done]
        report.planned += len(units)
        report.skipped += len(units) - len(pending)
        _fetch_units(store, client, cfg, pending, report)

        weeks = _weeks_for(store, cfg, season)
        weekly = [(season, view, week) for week in weeks for view in WEEKLY_VIEWS]
        pending = [u for u in weekly if u not in done]
        report.planned += len(weekly)
        report.skipped += len(weekly) - len(pending)
        _fetch_units(store, client, cfg, pending, report)

    log.info("fetch complete: %d requested, %d already stored, %d ok, %d failed",
             report.planned, report.skipped, report.fetched, report.failed)
    if report.failed:
        log.warning("re-run to retry only the failed units; successes are skipped")
    return report


def _weeks_for(store: RawStore, cfg: Config, season: int) -> list[int]:
    """Weeks to request for one season, capped at what has actually been played.

    Requesting a boxscore for a week that has not happened yet does not return
    an empty roster - ESPN returns the current roster instead. Without this cap
    an in-progress season writes one identical copy of today's lineups per
    remaining week, as if they were real weekly results.
    """
    wanted = cfg.week_range()
    cap = latest_scoring_period(store.get(season, "mSettings"))
    if cap is None:
        return wanted
    capped = [w for w in wanted if w <= cap]
    if len(capped) < len(wanted):
        log.info("  %d: capping at week %d (ESPN's latest scoring period); "
                 "later weeks have not been played", season, cap)
    return capped


def _fetch_units(store: RawStore, client: EspnClient, cfg: Config,
                 units: list[tuple[int, str, int | None]],
                 report: FetchReport) -> None:
    for index, (season, view, period) in enumerate(units):
        label = f"{season} {view}" + (f" week {period}" if period else "")
        log.info("fetching %s", label)
        try:
            payload, status, url = client.fetch_view(season, view, period)
            store.put(RawRow(season, view, period, url, status, payload))
            store.log_unit(season, view, period, "ok", status)
            store.commit()
            report.fetched += 1
        except FATAL_EXCEPTIONS as exc:
            # Bad cookies or a wrong league id will fail identically for every
            # remaining unit; stopping now beats several hundred retries.
            store.rollback()
            store.log_unit(season, view, period, "error", None,
                           f"{type(exc).__name__}: {exc}")
            store.commit()
            raise FetchError(
                f"ESPN refused the request for {label}: {exc}\n\n"
                "This usually means the cookies have expired or the league id "
                "is wrong.\nRefresh ESPN_S2 and SWID from a logged-in browser "
                "and run again;\nunits already fetched will be skipped."
            ) from exc
        except FflError as exc:
            store.rollback()
            log.warning("  failed %s: %s", label, exc)
            store.log_unit(season, view, period, "error", None, str(exc))
            store.commit()
            report.failed += 1
        # Rate limit: be a good citizen against an API with no published quota.
        if index < len(units) - 1 and cfg.delay > 0:
            time.sleep(cfg.delay)


def run_parse(store: RawStore, cfg: Config) -> Dataset:
    league_id = int(cfg.league_id) if cfg.league_id.isdigit() else 0
    data = parse_store(store, league_id, cfg.seasons)
    if data.is_empty():
        log.warning("nothing to parse - the raw store is empty. "
                    "Run `ffl-history fetch` first.")
    return data


def run_export(data: Dataset, cfg: Config,
               sinks: list[Sink] | None = None) -> list[SinkResult]:
    results = []
    for sink in sinks if sinks is not None else build_sinks(cfg):
        log.info("writing %s", sink.name)
        results.append(sink.write(data))
    return results
