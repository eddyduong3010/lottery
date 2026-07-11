from __future__ import annotations

import re
from dataclasses import dataclass

from .config import PRIZE_SPECS
from .models import Power655Draw, normalize_number


@dataclass(frozen=True, slots=True)
class Power655Hit:
    label: str
    tier_code: str
    matched_main_count: int
    matched_bonus: bool
    payout_vnd: int | None
    explanation: str


@dataclass(frozen=True, slots=True)
class Power655CheckResult:
    ticket_numbers: tuple[str, ...]
    hit: Power655Hit | None

    @property
    def is_winner(self) -> bool:
        return self.hit is not None


def parse_ticket_numbers(raw_ticket: str) -> tuple[str, ...]:
    values = re.findall(r'\d+', raw_ticket)
    if len(values) != 6:
        raise ValueError('Nhập đúng 6 số, ví dụ: 09 17 20 33 41 42')
    numbers = tuple(sorted((normalize_number(value) for value in values), key=int))
    if len(set(numbers)) != 6:
        raise ValueError('6 số dự thưởng không được trùng nhau')
    return numbers


def check_ticket(raw_ticket: str, draw: Power655Draw) -> Power655CheckResult:
    ticket_numbers = parse_ticket_numbers(raw_ticket)
    matched_main = set(ticket_numbers) & set(draw.main_numbers)
    matched_bonus = draw.bonus_number in ticket_numbers
    main_count = len(matched_main)

    if main_count == 6:
        tier_code = 'jackpot1'
    elif main_count == 5 and matched_bonus:
        tier_code = 'jackpot2'
    elif main_count == 5:
        tier_code = 'first'
    elif main_count == 4:
        tier_code = 'second'
    elif main_count == 3:
        tier_code = 'third'
    else:
        return Power655CheckResult(ticket_numbers, None)

    prize = next(item for item in draw.prizes if item.tier_code == tier_code)
    spec = PRIZE_SPECS[tier_code]
    explanation = f'Trùng {main_count} số chính'
    if matched_bonus:
        explanation += ' và trùng số đặc biệt'
    return Power655CheckResult(
        ticket_numbers,
        Power655Hit(spec.label, tier_code, main_count, matched_bonus, prize.payout_vnd, explanation),
    )
