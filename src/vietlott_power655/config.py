from __future__ import annotations

from dataclasses import dataclass
from datetime import time

PRODUCT_NAME = 'Power 6/55'
NUMBER_MIN = 1
NUMBER_MAX = 55
MAIN_NUMBER_COUNT = 6
DRAW_WEEKDAYS = (1, 3, 5)
DRAW_CUTOFF = time(18, 30)
HISTORY_URL = 'https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-655'
DETAIL_URL_TEMPLATE = 'https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/655?id={draw_id}&nocatche=1'
AJAX_RENDER_INFO_URL = 'https://vietlott.vn/ajaxpro/Vietlott.Utility.WebEnvironments,Vietlott.Utility.ashx'
AJAX_HISTORY_URL = (
    'https://vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Game655CompareWebPart,Vietlott.PlugIn.WebParts.ashx'
)
SITE_ID = 'main.frontend.vi'
XOSO_HISTORY_URL = 'https://xoso.com.vn/xo-so-power-655.html'
XOSO_MORE_URL_TEMPLATE = 'https://xoso.com.vn/XSDienToan/GetMorePower655?pageIndex={page_index}'


@dataclass(frozen=True, slots=True)
class PrizeSpec:
    tier_code: str
    label: str
    match_description: str
    fixed_payout_vnd: int | None


PRIZE_SPECS: dict[str, PrizeSpec] = {
    'jackpot1': PrizeSpec('jackpot1', 'Jackpot 1', 'Trùng 6 số chính', None),
    'jackpot2': PrizeSpec('jackpot2', 'Jackpot 2', 'Trùng 5 số chính và số đặc biệt', None),
    'first': PrizeSpec('first', 'Giải Nhất', 'Trùng 5 số chính', 40_000_000),
    'second': PrizeSpec('second', 'Giải Nhì', 'Trùng 4 số chính', 500_000),
    'third': PrizeSpec('third', 'Giải Ba', 'Trùng 3 số chính', 50_000),
}
PRIZE_DISPLAY_ORDER = tuple(PRIZE_SPECS)


def canonical_tier_code(label: str) -> str:
    normalized = ' '.join(label.lower().split())
    mapping = {
        'jackpot 1': 'jackpot1',
        'jackpot 2': 'jackpot2',
        'giải nhất': 'first',
        'giai nhat': 'first',
        'giải nhì': 'second',
        'giai nhi': 'second',
        'giải ba': 'third',
        'giai ba': 'third',
    }
    if normalized not in mapping:
        raise ValueError(f'Hạng giải Vietlott không hợp lệ: {label}')
    return mapping[normalized]
