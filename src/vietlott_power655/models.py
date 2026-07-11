from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .config import MAIN_NUMBER_COUNT, NUMBER_MAX, NUMBER_MIN, PRIZE_SPECS


def normalize_number(value: int | str) -> str:
    text = str(value).strip()
    if not text.isascii() or not text.isdigit():
        raise ValueError('Số Vietlott phải là chữ số ASCII')
    number = int(text)
    if number < NUMBER_MIN or number > NUMBER_MAX:
        raise ValueError(f'Số Vietlott phải nằm trong {NUMBER_MIN:02d}-{NUMBER_MAX:02d}')
    return f'{number:02d}'


@dataclass(frozen=True, slots=True)
class PrizeTierResult:
    tier_code: str
    winner_count: int | None
    payout_vnd: int | None

    def __post_init__(self) -> None:
        if self.tier_code not in PRIZE_SPECS:
            raise ValueError(f'Mã hạng giải không hợp lệ: {self.tier_code}')
        if self.winner_count is not None and self.winner_count < 0:
            raise ValueError('Số lượng giải không được âm')
        if self.payout_vnd is not None and self.payout_vnd < 0:
            raise ValueError('Giá trị giải không được âm')


@dataclass(frozen=True, slots=True)
class Power655Draw:
    draw_id: str
    draw_date: date
    main_numbers: tuple[str, ...]
    bonus_number: str
    prizes: tuple[PrizeTierResult, ...]
    source_url: str = ''
    fetched_at: datetime | None = None
    parser_version: str = 'vietlott-power655-v1'

    def __post_init__(self) -> None:
        if not self.draw_id.isascii() or not self.draw_id.isdigit():
            raise ValueError('Kỳ quay Vietlott phải là chuỗi số ASCII')
        normalized_main = tuple(normalize_number(value) for value in self.main_numbers)
        if len(normalized_main) != MAIN_NUMBER_COUNT:
            raise ValueError(f'Power 6/55 cần đúng {MAIN_NUMBER_COUNT} số chính')
        if len(set(normalized_main)) != MAIN_NUMBER_COUNT:
            raise ValueError('Bộ số chính Power 6/55 không được trùng nhau')
        if tuple(sorted(normalized_main, key=int)) != normalized_main:
            raise ValueError('Bộ số chính Power 6/55 phải được lưu theo thứ tự tăng dần')
        normalized_bonus = normalize_number(self.bonus_number)
        if normalized_bonus in normalized_main:
            raise ValueError('Số đặc biệt Power 6/55 không được trùng bộ số chính')

        prize_codes = {prize.tier_code for prize in self.prizes}
        missing = set(PRIZE_SPECS) - prize_codes
        if missing:
            raise ValueError('Thiếu hạng giải Power 6/55: ' + ', '.join(sorted(missing)))
        if len(prize_codes) != len(self.prizes):
            raise ValueError('Hạng giải Power 6/55 bị trùng')
