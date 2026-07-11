from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

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
