from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from math import ceil
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from requests import RequestException, Session

from .calendar import VIETNAM_TZ, is_draw_date, latest_available_date
from .config import (
    AJAX_HISTORY_URL,
    AJAX_RENDER_INFO_URL,
    DETAIL_URL_TEMPLATE,
    HISTORY_URL,
    PRIZE_DISPLAY_ORDER,
    PRIZE_SPECS,
    SITE_ID,
    XOSO_HISTORY_URL,
    XOSO_MORE_URL_TEMPLATE,
    canonical_tier_code,
)
from .models import Power655Draw, PrizeTierResult
from .providers import CVWORK_URL, XSMM_URL, parse_cvwork_html, parse_xsmm_html


class VietlottSourceError(RuntimeError):
    pass


class VietlottParseError(ValueError):
    pass


class VietlottConflictError(VietlottParseError):
    """Observed contradictory facts must never be hidden by fallback."""


def validate_public_draw(draw: Power655Draw) -> Power655Draw:
    if int(draw.draw_id) < 1 or not is_draw_date(draw.draw_date):
        raise VietlottParseError(f'Kỳ {draw.draw_id}: ID hoặc ngày quay không hợp lệ')
    if not date(2017, 8, 1) <= draw.draw_date <= latest_available_date():
        raise VietlottParseError(f'Kỳ {draw.draw_id}: ngày quay chưa công bố hoặc ngoài lịch sử Power 6/55')
    weeks, remainder = divmod((draw.draw_date - date(2017, 8, 1)).days, 7)
    maximum_scheduled_id = weeks * 3 + sum(offset <= remainder for offset in (0, 2, 4))
    if draw.draw_id != f'{int(draw.draw_id):05d}' or int(draw.draw_id) > maximum_scheduled_id:
        raise VietlottParseError(f'Kỳ {draw.draw_id}: ID không thể thuộc ngày quay {draw.draw_date}')
    return draw


@dataclass(frozen=True, slots=True)
class FetchBatch:
    draws: tuple[Power655Draw, ...]
    missing_draw_ids: tuple[str, ...]
    errors: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    latest_draw_id: str | None = None
    request_count: int = 0
    conflict_draw_ids: tuple[str, ...] = ()


def parse_vnd(value: str) -> int | None:
    digits = re.sub(r'\D+', '', value)
    return int(digits) if digits else None


def _default_prizes() -> tuple[PrizeTierResult, ...]:
    return tuple(PrizeTierResult(code, None, PRIZE_SPECS[code].fixed_payout_vnd) for code in PRIZE_DISPLAY_ORDER)


def _merge_draws(target: dict[str, Power655Draw], draws: list[Power655Draw]) -> None:
    """Deduplicate source markup/pages without hiding conflicting outcomes."""
    for draw in draws:
        existing = target.get(draw.draw_id)
        if existing and (existing.draw_date, existing.main_numbers, existing.bonus_number) != (
            draw.draw_date,
            draw.main_numbers,
            draw.bonus_number,
        ):
            raise VietlottConflictError(f'Kỳ {draw.draw_id}: nguồn chứa kết quả mâu thuẫn')
        if existing:
            old_prizes = {p.tier_code: p for p in existing.prizes}
            merged = []
            for prize in draw.prizes:
                old = old_prizes[prize.tier_code]
                for field in ('winner_count', 'payout_vnd'):
                    if (
                        getattr(old, field) is not None
                        and getattr(prize, field) is not None
                        and getattr(old, field) != getattr(prize, field)
                    ):
                        raise VietlottConflictError(f'Kỳ {draw.draw_id}: thông tin giải mâu thuẫn')
                merged.append(
                    PrizeTierResult(
                        prize.tier_code,
                        old.winner_count if old.winner_count is not None else prize.winner_count,
                        old.payout_vnd if old.payout_vnd is not None else prize.payout_vnd,
                    )
                )
            target[draw.draw_id] = replace(existing, prizes=tuple(merged))
        else:
            target[draw.draw_id] = draw


def _unique_draws(draws: list[Power655Draw]) -> list[Power655Draw]:
    unique: dict[str, Power655Draw] = {}
    _merge_draws(unique, draws)
    return list(unique.values())


def parse_history_html(
    html: str,
    source_url: str = HISTORY_URL,
    *,
    warnings: list[str] | None = None,
) -> list[Power655Draw]:
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
        draw_id = draw_link.get_text('', strip=True)
        try:
            day, month, year = (int(value) for value in date_match.groups())
            numbers = [span.get_text('', strip=True) for span in cells[2].select('span.bong_tron')]
            if len(numbers) != 7:
                raise VietlottParseError(f'Kỳ {draw_id}: cần 6 số chính và 1 số đặc biệt, đọc được {len(numbers)} số')
            draw = Power655Draw(
                draw_id=draw_id,
                draw_date=date(year, month, day),
                main_numbers=tuple(numbers[:6]),
                bonus_number=numbers[6],
                prizes=_default_prizes(),
                source_url=source_url,
                fetched_at=fetched_at,
            )
            if warnings is not None:
                validate_public_draw(draw)
            draws.append(draw)
        except ValueError as exc:
            if warnings is None:
                raise
            warnings.append(f'{source_url}: kỳ {draw_id}: {exc}; đã cách ly')
    if not draws:
        raise VietlottParseError('Không đọc được kỳ quay Power 6/55 nào')
    # Strict standalone audits still reject a conflicting document. Automatic callers
    # receive valid rows and quarantine individual duplicate IDs through _observe.
    return _unique_draws(draws) if warnings is None else draws


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


def parse_xoso_history_html(html: str, source_url: str = XOSO_HISTORY_URL) -> list[Power655Draw]:
    soup = BeautifulSoup(html, 'lxml')
    fetched_at = datetime.now(VIETNAM_TZ)
    draws: list[Power655Draw] = []
    for section in soup.select('section.section'):
        heading = section.select_one('h2')
        result_box = section.select_one('.mega-results')
        if heading is None or result_box is None or 'Power 6/55' not in heading.get_text(' ', strip=True):
            continue
        date_match = re.search(r'(\d{2})/(\d{2})/(\d{4})', heading.get_text(' ', strip=True))
        draw_text = result_box.get_text(' ', strip=True)
        draw_match = re.search(r'#(\d+)', draw_text)
        numbers = [span.get_text('', strip=True) for span in result_box.select('span.btn-results')]
        if date_match is None or draw_match is None or len(numbers) != 7:
            continue
        day, month, year = (int(value) for value in date_match.groups())
        prizes: list[PrizeTierResult] = []
        for row in section.select('table tbody tr'):
            cells = row.find_all('td', recursive=False)
            if len(cells) < 4:
                continue
            try:
                tier_code = canonical_tier_code(cells[0].get_text(' ', strip=True))
            except ValueError:
                continue
            prizes.append(
                PrizeTierResult(
                    tier_code,
                    parse_vnd(cells[2].get_text(' ', strip=True)),
                    parse_vnd(cells[3].get_text(' ', strip=True)),
                )
            )
        prize_map = {prize.tier_code: prize for prize in prizes}
        completed_prizes = tuple(
            prize_map.get(code, PrizeTierResult(code, None, PRIZE_SPECS[code].fixed_payout_vnd))
            for code in PRIZE_DISPLAY_ORDER
        )
        detail_link = heading.find('a', href=re.compile(r'power-655-ngay'))
        detail_url = urljoin(source_url, detail_link.get('href', '')) if detail_link else source_url
        draws.append(
            Power655Draw(
                draw_id=draw_match.group(1),
                draw_date=date(year, month, day),
                main_numbers=tuple(numbers[:6]),
                bonus_number=numbers[6],
                prizes=completed_prizes,
                source_url=detail_url,
                fetched_at=fetched_at,
            )
        )
    return _unique_draws(draws)


class VietlottPower655Client:
    def __init__(
        self,
        scraper=None,
        timeout_seconds: float = 4,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        # Ordinary public HTTP only: no challenge solving, proxy or TLS override.
        self._http = scraper or Session()
        self._timeout_seconds = timeout_seconds
        self._clock, self._sleep = clock, sleep
        self._deadline: float | None = None
        self._request_limit: int | None = None
        self.request_count = 0
        self.source_errors: list[str] = []
        self._history_html: str | None = None
        self._history_draws: dict[str, Power655Draw] = {}
        self._render_info: dict[str, object] | None = None
        self._blocked_routes: set[str] = set()
        self._fallback_cache: dict[str, Power655Draw] = {}
        self._quarantined: set[str] = set()
        self._known_latest_id: int | None = None
        self._http.headers.update(
            {
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7',
                'User-Agent': 'LotteryAnalytics/1.1 (public results; educational use)',
            }
        )

    def _request(self, method: str, url: str, **kwargs):
        route = url.split('?', 1)[0]
        if route in self._blocked_routes:
            raise VietlottSourceError(f'Nguồn đã từ chối hoặc yêu cầu hoãn trong lần đồng bộ này: {route}')
        for attempt in range(2):
            current_url = url
            response = None
            try:
                for redirects in range(3):
                    remaining = self._deadline - self._clock() if self._deadline is not None else self._timeout_seconds
                    if remaining <= 0 or (
                        self._request_limit is not None and self.request_count >= self._request_limit
                    ):
                        raise VietlottSourceError('Đã hết ngân sách tải; giữ kỳ còn thiếu để thử lại lần sau')
                    self.request_count += 1
                    response = getattr(self._http, method)(
                        current_url,
                        timeout=min(self._timeout_seconds, remaining),
                        allow_redirects=False,
                        stream=True,
                        **kwargs,
                    )
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = urljoin(current_url, response.headers.get('Location', ''))
                        parsed = urlsplit(target)
                        response.close()
                        if (
                            redirects == 2
                            or parsed.scheme != 'https'
                            or parsed.netloc != urlsplit(url).netloc
                            or parsed.username
                        ):
                            raise VietlottSourceError(f'Chuyển hướng ngoài phạm vi HTTPS hoặc quá giới hạn: {url}')
                        current_url = target
                        continue
                    # Status policy is decided from headers. An error body can stall too;
                    # never read it before a forbidden route has opened its circuit.
                    if response.status_code != 200:
                        if response.status_code in {401, 403}:
                            self._blocked_routes.add(route)
                        response.close()
                        break
                    # Bound body size and check the cooperative deadline during streaming.
                    content = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        content.extend(chunk)
                        if len(content) > 1_048_576 or (self._deadline is not None and self._clock() >= self._deadline):
                            response.close()
                            raise VietlottSourceError(f'Phản hồi vượt giới hạn dung lượng/thời gian: {url}')
                    response._content = bytes(content)
                    response.close()
                    break
            except (OSError, RequestException) as exc:
                if response is not None:
                    response.close()
                if attempt == 0:
                    if self._deadline is None or self._clock() + 0.25 < self._deadline:
                        self._sleep(0.25)
                    continue
                raise VietlottSourceError(f'Lỗi kết nối tới {url}: {exc}') from exc
            if response.status_code == 200:
                return response
            status = response.status_code
            if status in {401, 403}:
                self._blocked_routes.add(route)
            retry_delay = 0.25
            if status == 429:
                raw = response.headers.get('Retry-After', '')
                try:
                    retry_delay = max(0, float(raw))
                except ValueError:
                    try:
                        retry_delay = max(0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds())
                    except ValueError, TypeError, OverflowError:
                        retry_delay = 1
                if retry_delay > 2:
                    self._blocked_routes.add(route)
                    raise VietlottSourceError(f'HTTP 429; Retry-After={raw}; hoãn tới lần đồng bộ sau: {url}')
            if attempt == 0 and (status == 429 or 500 <= status < 600):
                if self._deadline is not None and self._clock() + retry_delay >= self._deadline:
                    break
                self._sleep(retry_delay)
                continue
            break
        raise VietlottSourceError(f'Nguồn dữ liệu trả về HTTP {status}: {url}')

    def _get_text(self, url: str) -> str:
        return self._request('get', url).text

    def _post_ajax(self, url: str, method: str, payload: dict[str, object]) -> dict[str, object]:
        response = self._request(
            'post',
            url,
            json=payload,
            headers={
                'Content-Type': 'text/plain; charset=utf-8',
                'Referer': HISTORY_URL,
                'X-AjaxPro-Method': method,
                'X-Requested-With': 'XMLHttpRequest',
            },
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise VietlottParseError('API Vietlott không trả về JSON hợp lệ') from exc
        if not isinstance(body, dict) or not isinstance(body.get('value'), dict):
            raise VietlottParseError('Phản hồi API Vietlott thiếu đối tượng value')
        return body['value']

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
        game_draw_id: str = '',
    ) -> list[Power655Draw]:
        empty_row = [''] * 18
        value = self._post_ajax(
            AJAX_HISTORY_URL,
            'ServerSideDrawResult',
            {
                'ORenderInfo': render_info,
                'Key': history_key,
                'GameDrawId': game_draw_id,
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
        return parse_history_html(
            fragment,
            AJAX_HISTORY_URL,
            warnings=self.source_errors if game_draw_id else None,
        )

    def _observe(self, target: dict[str, Power655Draw], draws: list[Power655Draw]) -> None:
        for draw in draws:
            try:
                validate_public_draw(draw)
            except ValueError as exc:
                self.source_errors.append(f'{draw.source_url}: {exc}')
                continue
            self._known_latest_id = max(self._known_latest_id or 0, int(draw.draw_id))
            if draw.draw_id in self._quarantined:
                continue
            try:
                _merge_draws(target, [draw])
            except VietlottConflictError as exc:
                self._quarantined.add(draw.draw_id)
                target.pop(draw.draw_id, None)
                self.source_errors.append(str(exc))

    def _fallback_history(self) -> list[Power655Draw]:
        found: dict[str, Power655Draw] = {}
        for url, parser in ((CVWORK_URL, parse_cvwork_html), (XSMM_URL, parse_xsmm_html)):
            try:
                draws = parser(self._get_text(url), url, warnings=self.source_errors)
                self._observe(found, draws)
            except VietlottConflictError:
                raise
            except (VietlottSourceError, ValueError) as exc:
                self.source_errors.append(f'{url}: {exc}')
        self._fallback_cache = found
        if not found:
            raise VietlottSourceError('Không có kết quả hợp lệ từ các nguồn dự phòng công khai')
        return sorted(found.values(), key=lambda d: int(d.draw_id), reverse=True)

    def fetch_history(self) -> list[Power655Draw]:
        official = []
        try:
            self._history_html = self._get_text(HISTORY_URL)
            candidates = parse_history_html(self._history_html, HISTORY_URL, warnings=self.source_errors)
            official_by_id: dict[str, Power655Draw] = {}
            self._observe(official_by_id, candidates)
            official = list(official_by_id.values())
        except VietlottConflictError:
            raise
        except (VietlottSourceError, ValueError) as exc:
            self.source_errors.append(str(exc))
        if not official or max(d.draw_date for d in official) < latest_available_date():
            try:
                fallback = self._fallback_history()
                merged = {draw.draw_id: draw for draw in official}
                self._observe(merged, fallback)
                official = list(merged.values())
            except VietlottConflictError:
                raise
            except (VietlottSourceError, ValueError) as exc:
                self.source_errors.append(str(exc))
        if not official:
            raise VietlottSourceError('Tất cả nguồn Power 6/55 chưa cung cấp kết quả hợp lệ')
        self._history_draws = {draw.draw_id: draw for draw in official if draw.draw_id not in self._quarantined}
        return sorted(self._history_draws.values(), key=lambda d: int(d.draw_id), reverse=True)

    def _fetch_all_official(
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
            _merge_draws(draws_by_id, page_draws)
            if progress:
                progress(page_index + 1, total_pages)
        draws = sorted(draws_by_id.values(), key=lambda draw: (draw.draw_date, int(draw.draw_id)))
        if len(draws) != expected_count:
            raise VietlottSourceError(
                f'Lịch sử không đầy đủ: Vietlott công bố {expected_count} kỳ nhưng chỉ tải được {len(draws)} kỳ'
            )
        return draws

    def _fetch_all_xoso(
        self,
        request_delay_seconds: float = 0.05,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[Power655Draw]:
        first_page = parse_xoso_history_html(self._get_text(XOSO_HISTORY_URL), XOSO_HISTORY_URL)
        if not first_page:
            raise VietlottSourceError('Nguồn dự phòng không có trang lịch sử Power 6/55 đầu tiên')
        draws_by_id = {draw.draw_id: draw for draw in first_page}
        if progress:
            progress(1, 200)
        for page_index in range(1, 200):
            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)
            page_url = XOSO_MORE_URL_TEMPLATE.format(page_index=page_index)
            page_draws = parse_xoso_history_html(self._get_text(page_url), page_url)
            if not page_draws:
                break
            previous_count = len(draws_by_id)
            _merge_draws(draws_by_id, page_draws)
            if len(draws_by_id) == previous_count:
                break
            if progress:
                progress(page_index + 1, 200)
        draws = sorted(draws_by_id.values(), key=lambda draw: (draw.draw_date, int(draw.draw_id)))
        numeric_ids = {int(draw.draw_id) for draw in draws}
        latest_id = max(numeric_ids)
        missing_ids = set(range(1, latest_id + 1)) - numeric_ids
        if missing_ids:
            raise VietlottSourceError(
                f'Nguồn dự phòng thiếu {len(missing_ids)} kỳ Power 6/55 trong chuỗi 1-{latest_id}'
            )
        return draws

    def fetch_all_draws(
        self,
        request_delay_seconds: float = 0.05,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[Power655Draw]:
        try:
            return self._fetch_all_official(request_delay_seconds, progress)
        except VietlottSourceError:
            return self._fetch_all_xoso(request_delay_seconds, progress)

    def _fetch_selected_official(self, draw_id: str) -> Power655Draw:
        if self._history_html is None:
            self._history_html = self._get_text(HISTORY_URL)
        soup = BeautifulSoup(self._history_html, 'lxml')
        option = next(
            (
                item
                for item in soup.select('#drpSelectGameDraw option[value]')
                if re.search(rf'(?<!\d)0*{int(draw_id)}(?!\d)', item.get_text(' ', strip=True))
            ),
            None,
        )
        if option is None:
            raise VietlottParseError(f'Không có ID kỳ {draw_id} trong bộ chọn lịch sử chính thức')
        if self._render_info is None:
            self._render_info = self._create_render_info()
        draws = self.fetch_history_page(
            0, self._render_info, self._history_key(self._history_html), str(option['value'])
        )
        selected: dict[str, Power655Draw] = {}
        self._observe(selected, draws)
        if draw_id in self._quarantined:
            raise VietlottConflictError(f'Kỳ {draw_id}: API lịch sử chứa kết quả mâu thuẫn')
        if draw_id not in selected:
            raise VietlottParseError(f'API lịch sử không trả kỳ yêu cầu {draw_id}')
        return selected[draw_id]

    def fetch_detail(self, draw_id: str) -> Power655Draw:
        draw_id = draw_id.zfill(5)
        url = DETAIL_URL_TEMPLATE.format(draw_id=draw_id)
        try:
            draw = parse_detail_html(self._get_text(url), url)
            if draw.draw_id != draw_id:
                raise VietlottParseError(f'Kỳ nguồn {draw.draw_id} không khớp kỳ yêu cầu {draw_id}')
            return validate_public_draw(draw)
        except VietlottConflictError:
            raise
        except (VietlottSourceError, ValueError) as exc:
            self.source_errors.append(str(exc))
        # The site's ordinary draw selector can work when its detail route is unavailable.
        try:
            return validate_public_draw(self._fetch_selected_official(draw_id))
        except VietlottConflictError:
            raise
        except (VietlottSourceError, ValueError) as exc:
            self.source_errors.append(str(exc))
        # Only return a fallback record with the exact requested ID; never substitute latest.
        return self._fetch_fallback_detail(draw_id)

    def _fetch_fallback_detail(self, draw_id: str) -> Power655Draw:
        if draw_id in self._quarantined:
            raise VietlottConflictError(f'Kỳ {draw_id}: nguồn mâu thuẫn, đã cách ly')
        if draw_id in self._fallback_cache:
            return self._fallback_cache[draw_id]
        found: dict[str, Power655Draw] = {}
        for url, parser in (
            (f'{CVWORK_URL}/{draw_id}', parse_cvwork_html),
            (f'{XSMM_URL}/ky-{draw_id}', parse_xsmm_html),
        ):
            try:
                draws = parser(self._get_text(url), url, warnings=self.source_errors)
                self._observe(found, draws)
                if not any(draw.draw_id == draw_id for draw in draws):
                    raise VietlottParseError(f'Nguồn không trả kỳ yêu cầu {draw_id}')
            except VietlottConflictError:
                raise
            except (VietlottSourceError, ValueError) as exc:
                self.source_errors.append(f'{url}: {exc}')
        if draw_id in self._quarantined:
            raise VietlottConflictError(f'Kỳ {draw_id}: nguồn mâu thuẫn, đã cách ly')
        if draw_id not in found:
            raise VietlottSourceError(f'Các nguồn chưa cung cấp kỳ {draw_id}; sẽ thử lại')
        self._fallback_cache.update(found)
        return found[draw_id]

    def fetch_missing_draws(
        self,
        existing_ids: set[str],
        *,
        max_requests: int = 12,
        max_elapsed_seconds: float = 20,
    ) -> FetchBatch:
        """One bounded incremental attempt. Never download the full archive at startup."""
        self._deadline = self._clock() + max_elapsed_seconds
        self._request_limit = max_requests
        self.request_count = 0
        self.source_errors = []
        self._blocked_routes.clear()
        self._fallback_cache = {}
        self._quarantined.clear()
        self._known_latest_id = None
        self._history_html = None
        self._render_info = None
        self._history_draws = {}
        accepted: dict[str, Power655Draw] = {}
        pending: set[str] = set()
        conflicts: list[str] = []
        latest_id = None
        try:
            history = self.fetch_history()
            for draw in history:
                validate_public_draw(draw)
            latest_id = max(int(draw.draw_id) for draw in history)
            pending = {f'{i:05d}' for i in range(1, latest_id + 1)} - existing_ids
            # Persist validated newest records even if an older gap is inaccessible.
            for draw in history:
                accepted[draw.draw_id] = draw
            for draw_id in sorted(pending - set(accepted), key=int, reverse=True):
                if self._clock() >= self._deadline or self.request_count >= max_requests:
                    self.source_errors.append('Đã hết ngân sách tải; giữ kỳ còn thiếu để thử lại lần sau')
                    break
                try:
                    draw = self.fetch_detail(draw_id)
                    if draw.draw_id != draw_id:
                        raise VietlottParseError(f'Kỳ nguồn {draw.draw_id} không khớp {draw_id}')
                    _merge_draws(accepted, [validate_public_draw(draw)])
                except VietlottConflictError as exc:
                    conflicts.append(str(exc))
                    accepted.pop(draw_id, None)
                except (VietlottSourceError, ValueError) as exc:
                    self.source_errors.append(f'#{draw_id}: {exc}')
            for draw_id in self._quarantined:
                accepted.pop(draw_id, None)
            conflicts.extend(f'Kỳ {draw_id}: nguồn mâu thuẫn, đã cách ly' for draw_id in sorted(self._quarantined))
            unresolved = (pending - set(accepted)) | self._quarantined
        except VietlottConflictError as exc:
            conflicts.append(str(exc))
            unresolved = pending
        except (VietlottSourceError, ValueError) as exc:
            self.source_errors.append(str(exc))
            unresolved = pending
        finally:
            self._deadline, self._request_limit = None, None
        latest_id = max(latest_id or 0, self._known_latest_id or 0) or None
        if latest_id is not None:
            pending = {f'{i:05d}' for i in range(1, latest_id + 1)} - existing_ids
            unresolved = (pending - set(accepted)) | self._quarantined
        return FetchBatch(
            tuple(accepted.values()),
            tuple(sorted(unresolved)),
            tuple(self.source_errors),
            tuple(conflicts),
            f'{latest_id:05d}' if latest_id is not None else None,
            self.request_count,
            tuple(sorted(self._quarantined)),
        )

    def fetch_recent_batch(self, limit: int = 8, include_details: bool = True) -> FetchBatch:
        self._deadline, self._request_limit = self._clock() + 20, 12
        self.request_count = 0
        self.source_errors = []
        self._blocked_routes.clear()
        self._fallback_cache = {}
        self._quarantined.clear()
        self._known_latest_id = None
        accepted, conflicts = {}, []
        latest_id = None
        try:
            history = self.fetch_history()[:limit]
            latest_id = max(draw.draw_id for draw in history)
            for draw in history:
                validate_public_draw(draw)
                accepted[draw.draw_id] = draw
            if include_details:
                for draw in history:
                    try:
                        detail = self.fetch_detail(draw.draw_id)
                        _merge_draws(accepted, [detail])
                    except VietlottConflictError as exc:
                        accepted.pop(draw.draw_id, None)
                        conflicts.append(str(exc))
                    except (VietlottSourceError, ValueError) as exc:
                        self.source_errors.append(str(exc))
        except VietlottConflictError as exc:
            conflicts.append(str(exc))
        except (VietlottSourceError, ValueError) as exc:
            self.source_errors.append(str(exc))
        finally:
            self._deadline, self._request_limit = None, None
        for draw_id in self._quarantined:
            accepted.pop(draw_id, None)
        conflicts.extend(f'Kỳ {draw_id}: nguồn mâu thuẫn, đã cách ly' for draw_id in sorted(self._quarantined))
        return FetchBatch(
            tuple(accepted.values()),
            tuple(sorted(self._quarantined)),
            tuple(self.source_errors),
            tuple(conflicts),
            latest_id,
            self.request_count,
            tuple(sorted(self._quarantined)),
        )

    def fetch_latest_draws(self, limit: int = 8, include_details: bool = True) -> list[Power655Draw]:
        draws = self.fetch_history()[:limit]
        if not include_details:
            return draws
        detailed: list[Power655Draw] = []
        for draw in draws:
            try:
                detailed.append(self.fetch_detail(draw.draw_id))
            except VietlottConflictError:
                raise
            except VietlottSourceError, ValueError:
                detailed.append(draw)
        return detailed
