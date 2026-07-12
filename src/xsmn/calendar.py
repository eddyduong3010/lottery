from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import DRAW_CUTOFF, STATIONS, WEEKLY_SCHEDULE

VIETNAM_TZ = ZoneInfo('Asia/Ho_Chi_Minh')

# XSMN was suspended in these periods, so there is no result page to fetch.
DRAW_SUSPENSIONS = (
    (date(2020, 4, 1), date(2020, 4, 28)),
    (date(2021, 7, 9), date(2021, 10, 21)),
)


def stations_for_date(selected_date: date) -> tuple[str, ...]:
    if any(start <= selected_date <= end for start, end in DRAW_SUSPENSIONS):
        return ()
    return WEEKLY_SCHEDULE[selected_date.weekday()]


def latest_available_date(now: datetime | None = None) -> date:
    current = now.astimezone(VIETNAM_TZ) if now else datetime.now(VIETNAM_TZ)
    if current.time() < DRAW_CUTOFF:
        return current.date() - timedelta(days=1)
    return current.date()


def next_regional_draw_date(now: datetime | None = None) -> date:
    current = now.astimezone(VIETNAM_TZ) if now else datetime.now(VIETNAM_TZ)
    return current.date() if current.time() < DRAW_CUTOFF else current.date() + timedelta(days=1)


def next_draw_date(station_code: str, now: datetime | None = None) -> date:
    if station_code not in STATIONS:
        raise ValueError(f'Mã đài không hợp lệ: {station_code}')
    current = now.astimezone(VIETNAM_TZ) if now else datetime.now(VIETNAM_TZ)
    candidate = current.date()
    if current.time() >= DRAW_CUTOFF:
        candidate += timedelta(days=1)
    for offset in range(8):
        selected_date = candidate + timedelta(days=offset)
        if station_code in stations_for_date(selected_date):
            return selected_date
    raise RuntimeError(f'Không tìm thấy lịch quay tiếp theo cho {station_code}')
