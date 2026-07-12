from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from .calendar import latest_available_date, stations_for_date
from .repository import SQLiteRepository
from .scraper import XosoComClient


@dataclass(frozen=True, slots=True)
class IngestionReport:
    requested_days: int
    stored_draws: int
    failed_dates: tuple[tuple[date, str], ...]


def iter_dates(start_date: date, end_date: date):
    if start_date > end_date:
        raise ValueError('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc')
    selected_date = start_date
    while selected_date <= end_date:
        yield selected_date
        selected_date += timedelta(days=1)


def ingest_range(
    repository: SQLiteRepository,
    client: XosoComClient,
    start_date: date,
    end_date: date,
    on_progress: Callable[[int, int, date], None] | None = None,
) -> IngestionReport:
    dates = list(iter_dates(start_date, end_date))
    stored_draws = 0
    failures: list[tuple[date, str]] = []
    for index, selected_date in enumerate(dates, start=1):
        if on_progress:
            on_progress(index, len(dates), selected_date)
        try:
            stored_draws += repository.upsert_draws(client.fetch_date(selected_date))
        except Exception as exc:
            failures.append((selected_date, str(exc)))
    return IngestionReport(len(dates), stored_draws, tuple(failures))


def sync_missing_results(
    repository: SQLiteRepository,
    client: XosoComClient,
    bootstrap_days: int = 30,
) -> IngestionReport:
    """Fill missing draw dates within the local coverage window through the latest complete day."""
    end_date = latest_available_date()
    first_date, _ = repository.date_bounds()
    coverage_start = end_date - timedelta(days=max(bootstrap_days, 1) - 1)
    start_date = max(first_date, coverage_start) if first_date else coverage_start
    existing = repository.draw_keys(start_date, end_date)
    missing_dates = [
        selected_date
        for selected_date in iter_dates(start_date, end_date)
        if any((selected_date, station_code) not in existing for station_code in stations_for_date(selected_date))
    ]
    stored_draws = 0
    failures: list[tuple[date, str]] = []
    for selected_date in missing_dates:
        try:
            stored_draws += repository.upsert_draws(client.fetch_date(selected_date))
        except Exception as exc:
            failures.append((selected_date, str(exc)))
    return IngestionReport(len(missing_dates), stored_draws, tuple(failures))
