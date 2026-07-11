from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import DRAW_CUTOFF, DRAW_WEEKDAYS

VIETNAM_TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def is_draw_date(selected_date: date) -> bool:
    return selected_date.weekday() in DRAW_WEEKDAYS


def latest_available_date(now: datetime | None = None) -> date:
    current = now.astimezone(VIETNAM_TZ) if now else datetime.now(VIETNAM_TZ)
    candidate = current.date()
    if current.time() < DRAW_CUTOFF:
        candidate -= timedelta(days=1)
    while not is_draw_date(candidate):
        candidate -= timedelta(days=1)
    return candidate


def next_draw_date(now: datetime | None = None) -> date:
    current = now.astimezone(VIETNAM_TZ) if now else datetime.now(VIETNAM_TZ)
    candidate = current.date()
    if current.time() >= DRAW_CUTOFF:
        candidate += timedelta(days=1)
    while not is_draw_date(candidate):
        candidate += timedelta(days=1)
    return candidate
