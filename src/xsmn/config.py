from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time


@dataclass(frozen=True, slots=True)
class Station:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class PrizeSpec:
    code: str
    label: str
    digits: int
    result_count: int
    payout_vnd: int


STATIONS = {
    station.code: station
    for station in (
        Station('HCM', 'TP. Hồ Chí Minh'),
        Station('DT', 'Đồng Tháp'),
        Station('CM', 'Cà Mau'),
        Station('BTR', 'Bến Tre'),
        Station('VT', 'Vũng Tàu'),
        Station('BL', 'Bạc Liêu'),
        Station('DN', 'Đồng Nai'),
        Station('CT', 'Cần Thơ'),
        Station('ST', 'Sóc Trăng'),
        Station('TN', 'Tây Ninh'),
        Station('AG', 'An Giang'),
        Station('BTH', 'Bình Thuận'),
        Station('VL', 'Vĩnh Long'),
        Station('BD', 'Bình Dương'),
        Station('TV', 'Trà Vinh'),
        Station('LA', 'Long An'),
        Station('BP', 'Bình Phước'),
        Station('HG', 'Hậu Giang'),
        Station('TG', 'Tiền Giang'),
        Station('KG', 'Kiên Giang'),
        Station('DL', 'Đà Lạt'),
    )
}

# Python weekday: Monday=0, Sunday=6. Kept in one configuration so a future
# official schedule change does not affect the rest of the application.
WEEKLY_SCHEDULE: dict[int, tuple[str, ...]] = {
    0: ('HCM', 'DT', 'CM'),
    1: ('BTR', 'VT', 'BL'),
    2: ('DN', 'CT', 'ST'),
    3: ('TN', 'AG', 'BTH'),
    4: ('VL', 'BD', 'TV'),
    5: ('HCM', 'LA', 'BP', 'HG'),
    6: ('TG', 'KG', 'DL'),
}

WEEKDAY_LABELS = {
    0: 'Thứ Hai',
    1: 'Thứ Ba',
    2: 'Thứ Tư',
    3: 'Thứ Năm',
    4: 'Thứ Sáu',
    5: 'Thứ Bảy',
    6: 'Chủ Nhật',
}

DRAW_CUTOFF = time(16, 35)
PRIZE_RULES_EFFECTIVE_FROM = date(2017, 1, 1)
HISTORICAL_SOURCE_GAPS = frozenset(
    {
        date(2008, 9, 2),
        date(2008, 10, 26),
        date(2011, 2, 3),
    }
)
SOURCE_URL_TEMPLATE = 'https://xoso.com.vn/xsmn-{date:%d-%m-%Y}.html?amp=1'

PRIZE_SPECS = {
    spec.code: spec
    for spec in (
        PrizeSpec('g8', 'Giải tám', 2, 1, 100_000),
        PrizeSpec('g7', 'Giải bảy', 3, 1, 200_000),
        PrizeSpec('g6', 'Giải sáu', 4, 3, 400_000),
        PrizeSpec('g5', 'Giải năm', 4, 1, 1_000_000),
        PrizeSpec('g4', 'Giải tư', 5, 7, 3_000_000),
        PrizeSpec('g3', 'Giải ba', 5, 2, 10_000_000),
        PrizeSpec('g2', 'Giải nhì', 5, 1, 15_000_000),
        PrizeSpec('g1', 'Giải nhất', 5, 1, 30_000_000),
        PrizeSpec('db', 'Giải đặc biệt', 6, 1, 2_000_000_000),
    )
}

PRIZE_DISPLAY_ORDER = tuple(PRIZE_SPECS)
PRIZE_CHECK_ORDER = tuple(reversed(PRIZE_DISPLAY_ORDER))
AUXILIARY_SPECIAL_PAYOUT_VND = 50_000_000
CONSOLATION_PAYOUT_VND = 6_000_000


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize('NFD', value.strip().lower().replace('đ', 'd'))
    ascii_text = ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', ascii_text).strip()


_STATION_ALIASES = {
    normalize_text(alias): code
    for code, aliases in {
        'HCM': ('TP. Hồ Chí Minh', 'Hồ Chí Minh', 'TP HCM', 'TPHCM'),
        'DT': ('Đồng Tháp',),
        'CM': ('Cà Mau',),
        'BTR': ('Bến Tre',),
        'VT': ('Vũng Tàu',),
        'BL': ('Bạc Liêu',),
        'DN': ('Đồng Nai',),
        'CT': ('Cần Thơ',),
        'ST': ('Sóc Trăng',),
        'TN': ('Tây Ninh',),
        'AG': ('An Giang',),
        'BTH': ('Bình Thuận',),
        'VL': ('Vĩnh Long',),
        'BD': ('Bình Dương',),
        'TV': ('Trà Vinh',),
        'LA': ('Long An',),
        'BP': ('Bình Phước',),
        'HG': ('Hậu Giang',),
        'TG': ('Tiền Giang',),
        'KG': ('Kiên Giang',),
        'DL': ('Đà Lạt', 'Lâm Đồng', 'Đà Lạt (Lâm Đồng)'),
    }.items()
    for alias in aliases
}


def station_from_name(name: str) -> Station:
    code = _STATION_ALIASES.get(normalize_text(name))
    if code is None:
        raise ValueError(f'Không nhận diện được đài xổ số: {name!r}')
    return STATIONS[code]


def canonical_prize_code(label: str) -> str:
    compact = normalize_text(label).replace('giai', '').replace(' ', '')
    if compact in {'db', 'dacbiet'}:
        return 'db'
    if compact in {str(number) for number in range(1, 9)}:
        return f'g{compact}'
    raise ValueError(f'Không nhận diện được giải thưởng: {label!r}')
