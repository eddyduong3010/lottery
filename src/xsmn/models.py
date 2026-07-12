from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .config import PRIZE_SPECS, STATIONS


@dataclass(frozen=True, slots=True)
class PrizeResult:
    prize_code: str
    ordinal: int
    number: str

    def __post_init__(self) -> None:
        if self.prize_code not in PRIZE_SPECS:
            raise ValueError(f'Mã giải không hợp lệ: {self.prize_code}')
        if self.ordinal < 1:
            raise ValueError('Thứ tự kết quả phải bắt đầu từ 1')
        if not isinstance(self.number, str) or not self.number.isascii() or not self.number.isdigit():
            raise ValueError('Kết quả phải là chuỗi chữ số ASCII')
        expected_digits = PRIZE_SPECS[self.prize_code].digits
        allowed_digits = {expected_digits}
        if self.prize_code == 'db':
            allowed_digits.add(5)
        if len(self.number) not in allowed_digits:
            expected_label = ' hoặc '.join(str(value) for value in sorted(allowed_digits))
            raise ValueError(
                f'{PRIZE_SPECS[self.prize_code].label} phải có {expected_label} chữ số, nhận {self.number!r}'
            )


@dataclass(frozen=True, slots=True)
class Draw:
    draw_date: date
    station_code: str
    station_name: str
    results: tuple[PrizeResult, ...]
    source_url: str = ''
    fetched_at: datetime | None = None
    parser_version: str = 'xoso-com-amp-v1'

    def __post_init__(self) -> None:
        if self.station_code not in STATIONS:
            raise ValueError(f'Mã đài không hợp lệ: {self.station_code}')
        if self.station_name != STATIONS[self.station_code].name:
            raise ValueError(
                f'Tên đài {self.station_name!r} không khớp mã {self.station_code}; '
                f'cần {STATIONS[self.station_code].name!r}'
            )
        if not self.results:
            raise ValueError('Kỳ quay chưa có kết quả')

        seen: set[tuple[str, int]] = set()
        counts = {code: 0 for code in PRIZE_SPECS}
        ordinals: dict[str, list[int]] = {code: [] for code in PRIZE_SPECS}
        for result in self.results:
            key = (result.prize_code, result.ordinal)
            if key in seen:
                raise ValueError(f'Kết quả bị trùng: {key}')
            seen.add(key)
            counts[result.prize_code] += 1
            ordinals[result.prize_code].append(result.ordinal)

        problems = [
            f'{spec.label}: cần {spec.result_count}, có {counts[code]}'
            for code, spec in PRIZE_SPECS.items()
            if counts[code] != spec.result_count
        ]
        if problems:
            raise ValueError('Kỳ quay chưa đủ cơ cấu giải: ' + '; '.join(problems))
        ordinal_problems = [
            f'{PRIZE_SPECS[code].label}: thứ tự {sorted(ordinals[code])}'
            for code in PRIZE_SPECS
            if sorted(ordinals[code]) != list(range(1, PRIZE_SPECS[code].result_count + 1))
        ]
        if ordinal_problems:
            raise ValueError('Thứ tự kết quả không liên tục từ 1: ' + '; '.join(ordinal_problems))

    @property
    def special_number(self) -> str:
        return next(result.number for result in self.results if result.prize_code == 'db')

    def results_for(self, prize_code: str) -> tuple[PrizeResult, ...]:
        return tuple(result for result in self.results if result.prize_code == prize_code)
