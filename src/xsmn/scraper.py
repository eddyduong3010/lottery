from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup
from cloudscraper import CloudScraper, create_scraper
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .calendar import VIETNAM_TZ, stations_for_date
from .config import (
    PRIZE_DISPLAY_ORDER,
    PRIZE_RULES_EFFECTIVE_FROM,
    PRIZE_SPECS,
    SOURCE_URL_TEMPLATE,
    canonical_prize_code,
    station_from_name,
)
from .models import Draw, PrizeResult


class XSMNSourceError(RuntimeError):
    pass


class XSMNParseError(ValueError):
    pass


def source_url_for_date(draw_date: date) -> str:
    return SOURCE_URL_TEMPLATE.format(date=draw_date)


def parse_xoso_com_html(draw_date: date, html: str, source_url: str = '') -> list[Draw]:
    soup = BeautifulSoup(html, 'lxml')
    canonical = soup.find('link', rel='canonical')
    canonical_url = canonical.get('href', '') if canonical else ''
    canonical_match = re.search(r'xsmn-(\d{2})-(\d{2})-(\d{4})\.html', canonical_url)
    if canonical_match is None:
        raise XSMNParseError('Trang nguồn không có ngày canonical XSMN hợp lệ')
    day, month, year = (int(value) for value in canonical_match.groups())
    canonical_date = date(year, month, day)
    if canonical_date != draw_date:
        raise XSMNParseError(
            f'Ngày trên trang nguồn là {canonical_date:%d-%m-%Y}, không khớp ngày yêu cầu {draw_date:%d-%m-%Y}'
        )
    table = soup.select_one('table.table-result.table-xsmn')
    if table is None:
        raise XSMNParseError('Không tìm thấy bảng kết quả XSMN trong trang nguồn')

    station_cells = table.select('thead th[class*="prize-col"]')
    if not station_cells:
        raise XSMNParseError('Bảng kết quả không có tên đài')
    try:
        stations = [station_from_name(cell.get_text(' ', strip=True)) for cell in station_cells]
    except ValueError as exc:
        raise XSMNParseError(str(exc)) from exc
    expected_station_codes = set(stations_for_date(draw_date))
    actual_station_codes = {station.code for station in stations}
    missing_station_codes = expected_station_codes - actual_station_codes
    unexpected_station_codes = actual_station_codes - expected_station_codes
    if missing_station_codes or (unexpected_station_codes and draw_date >= PRIZE_RULES_EFFECTIVE_FROM):
        missing = ', '.join(sorted(expected_station_codes - actual_station_codes)) or 'không'
        unexpected = ', '.join(sorted(actual_station_codes - expected_station_codes)) or 'không'
        raise XSMNParseError(
            f'Danh sách đài không khớp lịch {draw_date:%d-%m-%Y}; thiếu: {missing}; thừa: {unexpected}'
        )

    station_results: dict[str, list[PrizeResult]] = {station.code: [] for station in stations}
    unavailable_station_codes: set[str] = set()
    seen_prizes: set[str] = set()
    body = table.find('tbody')
    rows = body.find_all('tr', recursive=False) if body else []
    for row in rows:
        heading = row.find('th', recursive=False)
        if heading is None:
            continue
        try:
            prize_code = canonical_prize_code(heading.get_text(' ', strip=True))
        except ValueError:
            continue
        if prize_code in seen_prizes:
            continue
        cells = row.find_all('td', recursive=False)
        if len(cells) != len(stations):
            raise XSMNParseError(f'{prize_code}: số cột kết quả ({len(cells)}) khác số đài ({len(stations)})')
        seen_prizes.add(prize_code)
        for station, cell in zip(stations, cells, strict=True):
            if station.code in unavailable_station_codes:
                continue
            full_number_spans = [
                span
                for span in cell.find_all('span', class_='xs_prize1', recursive=False)
                if 'show' in span.get('class', ()) and 'hide' not in span.get('class', ())
            ]
            numbers = [span.get_text('', strip=True) for span in full_number_spans]
            if not numbers:
                raise XSMNParseError(f'{station.name} {prize_code}: không đọc được kết quả đầy đủ')
            if any(not number.isascii() or not number.isdigit() for number in numbers):
                unavailable_station_codes.add(station.code)
                continue
            expected_count = PRIZE_SPECS[prize_code].result_count
            numbers = numbers[:expected_count]
            for ordinal, number in enumerate(numbers, start=1):
                if draw_date < PRIZE_RULES_EFFECTIVE_FROM and number.isascii() and number.isdigit():
                    expected_digits = PRIZE_SPECS[prize_code].digits
                    if prize_code == 'db' and len(number) == 5:
                        pass
                    elif len(number) < expected_digits:
                        number = number.zfill(expected_digits)
                    elif len(number) > expected_digits:
                        number = number[-expected_digits:]
                try:
                    station_results[station.code].append(PrizeResult(prize_code, ordinal, number))
                except ValueError as exc:
                    raise XSMNParseError(f'{station.name}: {exc}') from exc

    missing_prizes = set(PRIZE_DISPLAY_ORDER) - seen_prizes
    if missing_prizes:
        raise XSMNParseError(f'Trang nguồn thiếu các giải: {", ".join(sorted(missing_prizes))}')

    fetched_at = datetime.now(VIETNAM_TZ)
    draws: list[Draw] = []
    for station in stations:
        if station.code in unavailable_station_codes:
            continue
        try:
            draws.append(
                Draw(
                    draw_date=draw_date,
                    station_code=station.code,
                    station_name=station.name,
                    results=tuple(station_results[station.code]),
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
            )
        except ValueError as exc:
            raise XSMNParseError(f'{station.name}: {exc}') from exc
    if not draws:
        raise XSMNParseError('Trang nguồn không có kết quả đầy đủ cho bất kỳ đài nào')
    return draws


class XosoComClient:
    def __init__(self, scraper: CloudScraper | None = None, timeout_seconds: int = 20) -> None:
        self._http = scraper or create_scraper()
        self._timeout_seconds = timeout_seconds
        self._http.headers.update(
            {
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7',
                'User-Agent': 'Mozilla/5.0 (compatible; XSMNAnalytics/1.0; +local educational app)',
            }
        )

    @retry(
        retry=retry_if_exception_type((OSError, XSMNSourceError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def fetch_date(self, draw_date: date) -> list[Draw]:
        url = source_url_for_date(draw_date)
        try:
            response = self._http.get(url, timeout=self._timeout_seconds)
        except OSError:
            raise
        except Exception as exc:
            raise XSMNSourceError(f'Lỗi kết nối tới {url}: {exc}') from exc
        if response.status_code != 200:
            raise XSMNSourceError(f'Nguồn dữ liệu trả về HTTP {response.status_code}: {url}')
        return parse_xoso_com_html(draw_date, response.text, url)
