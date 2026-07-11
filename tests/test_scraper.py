from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from xsmn.scraper import XSMNParseError, parse_xoso_com_html

FIXTURE = Path(__file__).parent / 'fixtures' / 'xsmn_sample.html'


def test_parse_xoso_com_html_keeps_leading_zeroes() -> None:
    draws = parse_xoso_com_html(date(2026, 7, 10), FIXTURE.read_text(encoding='utf-8'))

    assert [draw.station_code for draw in draws] == ['VL', 'BD', 'TV']
    assert draws[0].special_number == '073565'
    assert draws[0].results_for('g7')[0].number == '095'
    assert len(draws[0].results) == 18


def test_parser_does_not_depend_on_city_column_count_class() -> None:
    soup = BeautifulSoup(FIXTURE.read_text(encoding='utf-8'), 'lxml')
    headings = soup.select('thead th.prize-col3')
    saturday_names = ('TP. Hồ Chí Minh', 'Long An', 'Bình Phước')
    for heading, station_name in zip(headings, saturday_names, strict=True):
        heading['class'] = ['prize-col4']
        heading.select_one('h3').string = station_name
    extra_heading = soup.new_tag('th', attrs={'class': 'prize-col4'})
    title = soup.new_tag('h3')
    title.string = 'Hậu Giang'
    extra_heading.append(title)
    soup.select_one('thead tr').append(extra_heading)
    soup.select_one('link[rel="canonical"]')['href'] = 'https://xoso.com.vn/xsmn-11-07-2026.html'
    for row in soup.select('tbody > tr'):
        row.append(deepcopy(row.find_all('td', recursive=False)[-1]))

    draws = parse_xoso_com_html(date(2026, 7, 11), str(soup))
    assert [draw.station_code for draw in draws] == ['HCM', 'LA', 'BP', 'HG']


def test_parser_rejects_station_schedule_mismatch() -> None:
    html = FIXTURE.read_text(encoding='utf-8').replace('xsmn-10-07-2026', 'xsmn-11-07-2026')
    with pytest.raises(XSMNParseError, match='không khớp lịch'):
        parse_xoso_com_html(date(2026, 7, 11), html)


def test_parser_rejects_canonical_date_mismatch() -> None:
    with pytest.raises(XSMNParseError, match='không khớp ngày yêu cầu'):
        parse_xoso_com_html(date(2026, 7, 17), FIXTURE.read_text(encoding='utf-8'))


def test_parse_rejects_incomplete_prize_structure() -> None:
    html = FIXTURE.read_text(encoding='utf-8').replace('<tr><th>8</th>', '<tr><th>unknown</th>', 1)
    with pytest.raises(XSMNParseError, match='thiếu các giải'):
        parse_xoso_com_html(date(2026, 7, 10), html)
