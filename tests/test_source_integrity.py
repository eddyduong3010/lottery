from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from helpers import make_draw

from vietlott_power655.config import PRIZE_SPECS
from vietlott_power655.ingestion import sync_missing_results
from vietlott_power655.models import Power655Draw, PrizeTierResult
from vietlott_power655.scraper import VietlottParseError, _unique_draws
from xsmn.scraper import XSMNParseError, parse_xoso_com_html


def test_xsmn_rejects_conflicting_extra_number() -> None:
    fixture = Path(__file__).parent / 'fixtures' / 'xsmn_sample.html'
    soup = BeautifulSoup(fixture.read_text(), 'lxml')
    cell = soup.select('tbody > tr')[-1].find_all('td', recursive=False)[0]
    extra = deepcopy(cell.select_one('span.xs_prize1.show'))
    extra.string = '999999'
    cell.append(extra)
    with pytest.raises(XSMNParseError, match='cần 1 kết quả, đọc được 2'):
        parse_xoso_com_html(date(2026, 7, 10), str(soup))


def test_modern_draw_rejects_legacy_special_width() -> None:
    draw = make_draw()
    results = tuple(replace(item, number='12345') if item.prize_code == 'db' else item for item in draw.results)
    with pytest.raises(ValueError, match='cơ cấu giải hiện hành'):
        replace(draw, results=results)


def power_draw() -> Power655Draw:
    return Power655Draw(
        '1',
        date(2017, 8, 1),
        ('1', '2', '3', '4', '5', '6'),
        '7',
        tuple(PrizeTierResult(code, None, None) for code in PRIZE_SPECS),
    )


def test_power_stores_canonical_validated_numbers() -> None:
    draw = power_draw()
    assert draw.draw_id == '00001'
    assert draw.main_numbers == ('01', '02', '03', '04', '05', '06')
    assert draw.bonus_number == '07'


def test_power_deduplicates_exact_draws_but_rejects_conflicts() -> None:
    draw = power_draw()
    assert _unique_draws([draw, draw]) == [draw]
    with pytest.raises(VietlottParseError, match='mâu thuẫn'):
        _unique_draws([draw, replace(draw, bonus_number='08')])


def test_power_small_gap_does_not_download_whole_archive(monkeypatch) -> None:
    from datetime import date, timedelta

    dates = []
    cursor = date(2026, 7, 14)
    while len(dates) < 40:
        if cursor.weekday() in (1, 3, 5):
            dates.append(cursor)
        cursor += timedelta(days=1)
    monkeypatch.setattr('vietlott_power655.ingestion.latest_available_date', lambda: dates[-1])

    def draw_for(draw_id):
        return replace(power_draw(), draw_id=draw_id, draw_date=dates[int(draw_id) - 1371])

    class Repository:
        def draw_ids(self):
            return {f'{i:05d}' for i in range(1, 1371)}

        def upsert_draws(self, draws):
            assert {draw.draw_id for draw in draws} == {f'{i:05d}' for i in range(1371, 1411)}
            return len(draws)

    class Client:
        def __init__(self):
            self.detail_ids = []

        def fetch_history(self):
            return [draw_for(f'{i:05d}') for i in range(1410, 1402, -1)]

        def fetch_detail(self, draw_id):
            self.detail_ids.append(draw_id)
            return draw_for(draw_id)

        def fetch_all_draws(self):
            raise AssertionError('Do not reload 1,410 draws for a 40-draw gap')

    client = Client()
    report = sync_missing_results(Repository(), client)
    assert report.failed_message is None
    assert report.stored_draws == 40
    assert len(client.detail_ids) == 32
