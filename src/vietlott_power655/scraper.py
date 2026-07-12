from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import date, datetime
from math import ceil
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from cloudscraper import CloudScraper, create_scraper
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .calendar import VIETNAM_TZ
from .config import (
    AJAX_HISTORY_URL,
    AJAX_RENDER_INFO_URL,
    DETAIL_URL_TEMPLATE,
    HISTORY_URL,
    PRIZE_DISPLAY_ORDER,
    PRIZE_SPECS,
    SITE_ID,
    canonical_tier_code,
)
from .models import Power655Draw, PrizeTierResult


class VietlottSourceError(RuntimeError):
    pass


class VietlottParseError(ValueError):
    pass


def parse_vnd(value: str) -> int | None:
    digits = re.sub(r'\D+', '', value)
    return int(digits) if digits else None


def _default_prizes() -> tuple[PrizeTierResult, ...]:
    return tuple(PrizeTierResult(code, None, PRIZE_SPECS[code].fixed_payout_vnd) for code in PRIZE_DISPLAY_ORDER)


def parse_history_html(html: str, source_url: str = HISTORY_URL) -> list[Power655Draw]:
    soup = BeautifulSoup(html, 'lxml')
    rows = soup.select('#divResultContent tbody tr') or soup.select('table tbody tr')
    if not rows:
        raise VietlottParseError('Không tìm thấy bảng lịch sử Power 6/55')
    fetched_at = datetime.now(VIETNAM_TZ)
    draws: list[Power655Draw] = []
    for row in rows:
        cells = row.find_all('td', recursive=False)
        if len(cells) < 3:
            continue
        date_match = re.search(r'(\d{2})/(\d{2})/(\d{4})', cells[0].get_text(' ', strip=True))
        draw_link = cells[1].find('a')
        if date_match is None or draw_link is None:
            continue
        day, month, year = (int(value) for value in date_match.groups())
        draw_id = draw_link.get_text('', strip=True)
        numbers = [span.get_text('', strip=True) for span in cells[2].select('span.bong_tron')]
        if len(numbers) != 7:
            raise VietlottParseError(f'Kỳ {draw_id}: cần 6 số chính và 1 số đặc biệt, đọc được {len(numbers)} số')
        detail_url = urljoin(source_url, draw_link.get('href', ''))
        draws.append(
            Power655Draw(
                draw_id=draw_id,
                draw_date=date(year, month, day),
                main_numbers=tuple(numbers[:6]),
                bonus_number=numbers[6],
                prizes=_default_prizes(),
                source_url=detail_url,
                fetched_at=fetched_at,
            )
        )
    if not draws:
        raise VietlottParseError('Không đọc được kỳ quay Power 6/55 nào')
    return draws


def parse_detail_html(html: str, source_url: str = '') -> Power655Draw:
    soup = BeautifulSoup(html, 'lxml')
    title_text = soup.select_one('.chitietketqua_title h5')
    if title_text is None:
        raise VietlottParseError('Không tìm thấy tiêu đề kỳ quay Power 6/55')
    match = re.search(r'#(\d+).*?(\d{2})/(\d{2})/(\d{4})', title_text.get_text(' ', strip=True))
    if match is None:
        raise VietlottParseError('Không đọc được kỳ quay và ngày quay Power 6/55')
    draw_id, day, month, year = match.groups()

    result_box = soup.select_one('.day_so_ket_qua .day_so_ket_qua_v2')
    if result_box is None:
        raise VietlottParseError('Không tìm thấy bộ số Power 6/55')
    numbers = [span.get_text('', strip=True) for span in result_box.select('span.bong_tron')]
    if len(numbers) != 7:
        raise VietlottParseError(f'Kỳ {draw_id}: cần 6 số chính và 1 số đặc biệt, đọc được {len(numbers)} số')

    prizes: list[PrizeTierResult] = []
    for row in soup.select('.chitietketqua_table table tbody tr'):
        cells = row.find_all('td', recursive=False)
        if len(cells) < 4:
            continue
        try:
            tier_code = canonical_tier_code(cells[0].get_text(' ', strip=True))
        except ValueError:
            continue
        winner_count = parse_vnd(cells[2].get_text(' ', strip=True))
        payout = parse_vnd(cells[3].get_text(' ', strip=True))
        prizes.append(PrizeTierResult(tier_code, winner_count, payout))
    prize_map = {prize.tier_code: prize for prize in prizes}
    completed_prizes = tuple(
        prize_map.get(code, PrizeTierResult(code, None, PRIZE_SPECS[code].fixed_payout_vnd))
        for code in PRIZE_DISPLAY_ORDER
    )

    return Power655Draw(
        draw_id=draw_id,
        draw_date=date(int(year), int(month), int(day)),
        main_numbers=tuple(numbers[:6]),
        bonus_number=numbers[6],
        prizes=completed_prizes,
        source_url=source_url,
        fetched_at=datetime.now(VIETNAM_TZ),
    )


class VietlottPower655Client:
    def __init__(self, scraper: CloudScraper | None = None, timeout_seconds: int = 20) -> None:
        self._http = scraper or create_scraper()
        self._timeout_seconds = timeout_seconds
        self._http.headers.update(
            {
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7',
                'User-Agent': 'Mozilla/5.0 (compatible; VietlottPower655Analytics/1.0; +local educational app)',
            }
        )

    @retry(
        retry=retry_if_exception_type((OSError, VietlottSourceError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _get_text(self, url: str) -> str:
        try:
            response = self._http.get(url, timeout=self._timeout_seconds)
        except OSError:
            raise
        except Exception as exc:
            raise VietlottSourceError(f'Lỗi kết nối tới {url}: {exc}') from exc
        if response.status_code != 200:
            raise VietlottSourceError(f'Nguồn dữ liệu trả về HTTP {response.status_code}: {url}')
        return response.text

    @retry(
        retry=retry_if_exception_type((OSError, VietlottSourceError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _post_ajax(self, url: str, method: str, payload: dict[str, object]) -> dict[str, object]:
        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Referer': HISTORY_URL,
            'X-AjaxPro-Method': method,
            'X-Requested-With': 'XMLHttpRequest',
        }
        try:
            response = self._http.post(url, json=payload, headers=headers, timeout=self._timeout_seconds)
        except OSError:
            raise
        except Exception as exc:
            raise VietlottSourceError(f'Lỗi kết nối tới API Vietlott: {exc}') from exc
        if response.status_code != 200:
            raise VietlottSourceError(f'API Vietlott trả về HTTP {response.status_code}: {url}')
        try:
            body = response.json()
        except ValueError as exc:
            raise VietlottSourceError('API Vietlott không trả về JSON hợp lệ') from exc
        if not isinstance(body, dict) or 'value' not in body:
            raise VietlottSourceError('Phản hồi API Vietlott thiếu trường value')
        value = body['value']
        if not isinstance(value, dict):
            raise VietlottSourceError('Phản hồi API Vietlott có cấu trúc không hợp lệ')
        return value

    def _create_render_info(self) -> dict[str, object]:
        value = self._post_ajax(
            AJAX_RENDER_INFO_URL,
            'ServerSideFrontEndCreateRenderInfo',
            {'SiteId': SITE_ID},
        )
        value['SiteLang'] = 'vi'
        return value

    @staticmethod
    def _history_key(html: str) -> str:
        match = re.search(r"ServerSideDrawResult\(RenderInfo,\s*'([^']+)'", html)
        if match is None:
            raise VietlottParseError('Không tìm thấy khóa phân trang lịch sử Power 6/55')
        return match.group(1)

    @staticmethod
    def _history_draw_count(html: str) -> int:
        soup = BeautifulSoup(html, 'lxml')
        return max(0, len(soup.select('#drpSelectGameDraw option[value]')) - 1)

    def fetch_history_page(
        self,
        page_index: int,
        render_info: dict[str, object],
        history_key: str,
    ) -> list[Power655Draw]:
        empty_row = [''] * 18
        value = self._post_ajax(
            AJAX_HISTORY_URL,
            'ServerSideDrawResult',
            {
                'ORenderInfo': render_info,
                'Key': history_key,
                'GameDrawId': '',
                'ArrayNumbers': [empty_row.copy() for _ in range(5)],
                'CheckMulti': False,
                'PageIndex': page_index,
            },
        )
        if value.get('Error'):
            raise VietlottSourceError(str(value.get('InfoMessage') or 'API phân trang Vietlott báo lỗi'))
        fragment = value.get('HtmlContent')
        if not isinstance(fragment, str) or not fragment.strip():
            return []
        return parse_history_html(fragment, HISTORY_URL)

    def fetch_history(self) -> list[Power655Draw]:
        return parse_history_html(self._get_text(HISTORY_URL), HISTORY_URL)

    def fetch_all_draws(
        self,
        request_delay_seconds: float = 0.05,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[Power655Draw]:
        """Fetch every public Power 6/55 draw using Vietlott's Ajax pagination."""
        history_html = self._get_text(HISTORY_URL)
        first_page = parse_history_html(history_html, HISTORY_URL)
        expected_count = max(self._history_draw_count(history_html), len(first_page))
        page_size = len(first_page)
        total_pages = ceil(expected_count / page_size)
        history_key = self._history_key(history_html)
        render_info = self._create_render_info()
        draws_by_id = {draw.draw_id: draw for draw in first_page}
        if progress:
            progress(1, total_pages)
        for page_index in range(1, total_pages):
            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)
            page_draws = self.fetch_history_page(page_index, render_info, history_key)
            if not page_draws:
                raise VietlottSourceError(f'Trang lịch sử {page_index + 1}/{total_pages} không có dữ liệu')
            for draw in page_draws:
                draws_by_id[draw.draw_id] = draw
            if progress:
                progress(page_index + 1, total_pages)
        draws = sorted(draws_by_id.values(), key=lambda draw: (draw.draw_date, int(draw.draw_id)))
        if len(draws) != expected_count:
            raise VietlottSourceError(
                f'Lịch sử không đầy đủ: Vietlott công bố {expected_count} kỳ nhưng chỉ tải được {len(draws)} kỳ'
            )
        return draws

    def fetch_detail(self, draw_id: str) -> Power655Draw:
        url = DETAIL_URL_TEMPLATE.format(draw_id=draw_id)
        return parse_detail_html(self._get_text(url), url)

    def fetch_latest_draws(self, limit: int = 8, include_details: bool = True) -> list[Power655Draw]:
        draws = self.fetch_history()[:limit]
        if not include_details:
            return draws
        detailed: list[Power655Draw] = []
        for draw in draws:
            try:
                detailed.append(self.fetch_detail(draw.draw_id))
            except Exception:
                detailed.append(draw)
        return detailed
