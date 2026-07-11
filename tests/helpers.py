from __future__ import annotations

from datetime import date

from xsmn.config import PRIZE_SPECS, STATIONS
from xsmn.models import Draw, PrizeResult

DEFAULT_NUMBERS = {
    'g8': ['99'],
    'g7': ['888'],
    'g6': ['7000', '7001', '7002'],
    'g5': ['6000'],
    'g4': ['50000', '50001', '50002', '50003', '50004', '50005', '50006'],
    'g3': ['40000', '40001'],
    'g2': ['30000'],
    'g1': ['20000'],
    'db': ['012345'],
}


def make_draw(overrides: dict[str, list[str]] | None = None, station_code: str = 'VL') -> Draw:
    numbers = {code: list(values) for code, values in DEFAULT_NUMBERS.items()}
    for code, values in (overrides or {}).items():
        numbers[code] = values
    results = tuple(
        PrizeResult(code, ordinal, number)
        for code in PRIZE_SPECS
        for ordinal, number in enumerate(numbers[code], start=1)
    )
    return Draw(
        draw_date=date(2026, 7, 10),
        station_code=station_code,
        station_name=STATIONS[station_code].name,
        results=results,
        source_url='https://example.test/xsmn',
    )
