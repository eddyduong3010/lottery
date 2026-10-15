"""Public, unofficial number-only fallback adapters. Never infer missing bonus balls or prizes."""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .calendar import VIETNAM_TZ
from .config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS
from .models import Power655Draw, PrizeTierResult

CVWORK_URL = 'https://cvwork.vn/ket-qua-xo-so/vietlott/power-655'
XSMM_URL = 'https://xsmm.vn/xo-so-vietlott-power-655'


def _draw(identity: str, numbers: list[str], url: str) -> Power655Draw:
    match = re.search(r'#(\d+)\b.*?(\d{2})[/-](\d{2})[/-](\d{4})', identity)
    if match is None or len(numbers) != 7:
        raise ValueError('Nguồn dự phòng thiếu danh tính, ngày hoặc đủ 6 bóng chính và 1 bóng đặc biệt')
    draw_id, day, month, year = match.groups()
    return Power655Draw(
        draw_id=draw_id,
        draw_date=date(int(year), int(month), int(day)),
        main_numbers=tuple(numbers[:6]),
        bonus_number=numbers[6],
        # Secondary prize tables are not established facts. Keep variable amounts/counts unknown.
        prizes=tuple(PrizeTierResult(code, None, PRIZE_SPECS[code].fixed_payout_vnd) for code in PRIZE_DISPLAY_ORDER),
        source_url=url,
        fetched_at=datetime.now(VIETNAM_TZ),
        parser_version='power655-public-fallback-v1',
    )


def parse_cvwork_html(
    html: str, source_url: str = CVWORK_URL, *, warnings: list[str] | None = None
) -> list[Power655Draw]:
    soup = BeautifulSoup(html, 'lxml')
    draws = []
    for card in soup.select('.xcard'):
        title, balls = card.select_one('.xsub'), card.select_one('.xballs')
        if title is None or balls is None:
            continue  # Non-result cards such as navigation are not observations.
        try:
            if len(balls.select('span.xbonus')) != 1:
                raise ValueError('CVWork thiếu hoặc lặp bóng đặc biệt')
            draws.append(
                _draw(
                    title.get_text(' ', strip=True),
                    [s.get_text('', strip=True) for s in balls.select('span')],
                    source_url,
                )
            )
        except ValueError as exc:
            if warnings is not None:
                warnings.append(f'{source_url}: {title.get_text(" ", strip=True)}: {exc}; đã cách ly')
    if not draws:
        raise ValueError('CVWork không có kết quả Power 6/55 hợp lệ')
    return draws


def _xsmm_card(table, source_url: str) -> Power655Draw | None:
    card = table.parent
    heading = card.find('h2')
    if heading is None:
        raise ValueError('XSMM thiếu tiêu đề kết quả')
    if not re.search(r'Power.*6/55|XS Power', heading.get_text(' ', strip=True)):
        return None
    link = card.find('a', href=re.compile(r'/xo-so-vietlott-power-655/ky-\d+'))
    if link is None:
        raise ValueError('XSMM thiếu ID kỳ quay')
    balls = [s for s in card.select('div.rounded-full') if s.get_text('', strip=True).isdigit()]
    if len(balls) != 7 or 'border-yellow-500' not in balls[-1].get('class', []):
        raise ValueError(f'{link.get_text(" ", strip=True)} thiếu bóng đặc biệt hợp lệ')
    date_link = card.find('a', string=re.compile(r'Power \d{2}-\d{2}-\d{4}'))
    if date_link is None:
        raise ValueError('XSMM thiếu ngày kết quả Power')
    title = link.get_text(' ', strip=True) + ' ' + date_link.get_text(' ', strip=True)
    heading_date = re.search(r'(\d{2})[/-](\d{2})[/-](\d{4})', heading.get_text(' ', strip=True))
    link_date = re.search(r'(\d{2})-(\d{2})-(\d{4})', date_link.get_text(' ', strip=True))
    if heading_date and link_date and heading_date.groups() != link_date.groups():
        raise ValueError('XSMM ngày trong tiêu đề mâu thuẫn với ngày kết quả')
    return _draw(title, [s.get_text('', strip=True) for s in balls], source_url)


def parse_xsmm_html(html: str, source_url: str = XSMM_URL, *, warnings: list[str] | None = None) -> list[Power655Draw]:
    soup = BeautifulSoup(html, 'lxml')
    draws = []
    for table in soup.select('table.lottery-result-table'):
        try:
            draw = _xsmm_card(table, source_url)
            if draw is not None:
                draws.append(draw)
        except ValueError as exc:
            if warnings is not None:
                warnings.append(f'{source_url}: {exc}; đã cách ly')
    if not draws:
        raise ValueError('XSMM không có kết quả Power 6/55 hợp lệ')
    return draws
