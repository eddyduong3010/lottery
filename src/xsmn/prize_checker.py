from __future__ import annotations

from dataclasses import dataclass

from .config import (
    AUXILIARY_SPECIAL_PAYOUT_VND,
    CONSOLATION_PAYOUT_VND,
    PRIZE_CHECK_ORDER,
    PRIZE_RULES_EFFECTIVE_FROM,
    PRIZE_SPECS,
)
from .models import Draw


@dataclass(frozen=True, slots=True)
class PrizeHit:
    code: str
    label: str
    matched_number: str
    payout_vnd: int
    explanation: str


@dataclass(frozen=True, slots=True)
class TicketCheck:
    ticket_number: str
    hits: tuple[PrizeHit, ...]

    @property
    def is_winner(self) -> bool:
        return bool(self.hits)

    @property
    def total_payout_vnd(self) -> int:
        return sum(hit.payout_vnd for hit in self.hits)


def validate_ticket_number(ticket_number: str) -> str:
    number = ticket_number.strip()
    if len(number) != 6 or not number.isascii() or not number.isdigit():
        raise ValueError('Vé XSMN phải gồm đúng 6 chữ số, kể cả số 0 ở đầu')
    return number


def check_ticket(ticket_number: str, draw: Draw) -> TicketCheck:
    number = validate_ticket_number(ticket_number)
    if draw.draw_date < PRIZE_RULES_EFFECTIVE_FROM:
        raise ValueError(
            'Ứng dụng chỉ tính giá trị giải theo cơ cấu có hiệu lực từ 01-01-2017; '
            'không dò tiền thưởng cho kỳ cũ hơn để tránh báo sai.'
        )
    hits: list[PrizeHit] = []

    for prize_code in PRIZE_CHECK_ORDER:
        spec = PRIZE_SPECS[prize_code]
        for result in draw.results_for(prize_code):
            if number.endswith(result.number):
                hits.append(
                    PrizeHit(
                        code=prize_code,
                        label=spec.label,
                        matched_number=result.number,
                        payout_vnd=spec.payout_vnd,
                        explanation=f'Trùng {spec.digits} số cuối với {result.number}',
                    )
                )

    special = draw.special_number
    if number != special and number[1:] == special[1:]:
        hits.append(
            PrizeHit(
                code='phu_db',
                label='Giải phụ đặc biệt',
                matched_number=special,
                payout_vnd=AUXILIARY_SPECIAL_PAYOUT_VND,
                explanation='Trùng 5 số cuối giải đặc biệt, chỉ khác số đầu tiên',
            )
        )
    elif number != special and number[0] == special[0]:
        mismatches = sum(left != right for left, right in zip(number[1:], special[1:], strict=True))
        if mismatches == 1:
            hits.append(
                PrizeHit(
                    code='khuyen_khich',
                    label='Giải khuyến khích',
                    matched_number=special,
                    payout_vnd=CONSOLATION_PAYOUT_VND,
                    explanation='Trùng số đầu và chỉ sai một trong 5 số còn lại của giải đặc biệt',
                )
            )

    return TicketCheck(ticket_number=number, hits=tuple(hits))
