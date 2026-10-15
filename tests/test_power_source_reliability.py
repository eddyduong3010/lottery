from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from requests import Response
from test_power655 import make_power_draw

from vietlott_power655.calendar import latest_available_date
from vietlott_power655.config import AJAX_HISTORY_URL, AJAX_RENDER_INFO_URL, DETAIL_URL_TEMPLATE, HISTORY_URL
from vietlott_power655.ingestion import ingest_latest, sync_missing_results
from vietlott_power655.providers import CVWORK_URL, XSMM_URL, parse_cvwork_html, parse_xsmm_html
from vietlott_power655.repository import OutcomeConflictError, SQLiteRepository
from vietlott_power655.scraper import (
    FetchBatch,
    VietlottConflictError,
    VietlottPower655Client,
    VietlottSourceError,
    _merge_draws,
    parse_history_html,
)

FIXTURES = Path(__file__).parent / 'fixtures/power_sources'


def fixture(name):
    return (FIXTURES / name).read_text()


def response(status=200, text='', headers=None):
    result = Response()
    result.status_code = status
    result._content = text.encode()
    result._content_consumed = True
    result.encoding = 'utf-8'
    result.headers.update(headers or {})
    return result


class HTTP:
    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(('get', url, kwargs))
        values = self.routes.get(url, [lambda: response(403)])
        value = values.pop(0) if len(values) > 1 else values[0]
        return value()

    def post(self, url, **kwargs):
        self.calls.append(('post', url, kwargs))
        values = self.routes.get(url, [lambda: response(403)])
        value = values.pop(0) if len(values) > 1 else values[0]
        return value()


@pytest.fixture(autouse=True)
def fixed_cutoff(monkeypatch):
    monkeypatch.setattr('vietlott_power655.scraper.latest_available_date', lambda: date(2026, 10, 13))
    monkeypatch.setattr('vietlott_power655.ingestion.latest_available_date', lambda: date(2026, 10, 13))


def test_official_403_recovers_from_two_public_number_only_backups():
    http = HTTP(
        {
            HISTORY_URL: [lambda: response(403)],
            CVWORK_URL: [lambda: response(text=fixture('cvwork-01410.html'))],
            XSMM_URL: [lambda: response(text=fixture('xsmm-01410.html'))],
        }
    )
    batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
    assert len(batch.draws) == 1
    assert batch.draws[0].draw_id == '01410'
    assert batch.draws[0].bonus_number == '26'
    assert batch.draws[0].prizes[0].payout_vnd is None
    assert batch.draws[0].source_url == CVWORK_URL
    assert batch.missing_draw_ids == ()
    assert batch.errors and batch.request_count == 3
    assert sum(url == HISTORY_URL for _, url, _ in http.calls) == 1


def test_bot_html_200_is_not_a_successful_provider():
    http = HTTP(
        {
            HISTORY_URL: [lambda: response(text='<html>Verify you are human</html>')],
            CVWORK_URL: [lambda: response(text=fixture('cvwork-01410.html'))],
        }
    )
    batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
    assert batch.draws[0].draw_id == '01410'
    assert any('Không tìm thấy' in error for error in batch.errors)


def test_official_selector_repairs_forbidden_detail_without_disabling_ajax():
    history = fixture('official-history.html')
    selected = parse_history_html(history)[0]
    http = HTTP(
        {
            DETAIL_URL_TEMPLATE.format(draw_id=selected.draw_id): [lambda: response(403)],
            AJAX_RENDER_INFO_URL: [lambda: response(text=json.dumps({'value': {}}))],
            AJAX_HISTORY_URL: [lambda: response(text=json.dumps({'value': {'HtmlContent': history}}))],
        }
    )
    client = VietlottPower655Client(http)
    client._history_html = history
    result = client.fetch_detail(selected.draw_id)
    assert result.draw_id == selected.draw_id
    assert result.source_url == AJAX_HISTORY_URL
    assert [url for method, url, _ in http.calls if method == 'post'] == [AJAX_RENDER_INFO_URL, AJAX_HISTORY_URL]
    payload = http.calls[-1][2]['json']
    assert payload['GameDrawId']
    assert payload['PageIndex'] == 0


def test_partial_latest_persists_even_when_old_gap_fails_and_resumes(tmp_path):
    repo = SQLiteRepository(tmp_path / 'db.sqlite3')
    first = replace(make_power_draw('00001'), draw_date=date(2017, 8, 1))
    second = replace(first, draw_id='00002', draw_date=date(2017, 8, 3))
    latest = replace(first, draw_id='00003', draw_date=date(2017, 8, 5))
    repo.upsert_draw(first)

    class Client:
        def fetch_missing_draws(self, existing):
            if '00003' not in existing:
                return FetchBatch((latest,), ('00002',), ('detail 403',), latest_draw_id='00003')
            return FetchBatch((second, latest), (), latest_draw_id='00003')

    report = sync_missing_results(repo, Client())
    assert report.stored_draws == 1
    assert report.missing_draw_ids == ('00002',)
    assert report.failed_message and repo.get_draw('00003')
    resumed = sync_missing_results(repo, Client())
    assert resumed.stored_draws == 1 and resumed.missing_draw_ids == ()
    assert len(repo.draw_ids()) == 3
    repeated = sync_missing_results(repo, Client())
    assert repeated.stored_draws == 0
    assert repeated.failed_message  # Historical provider cannot be called current.


def test_complete_latest_survives_incomplete_xsmm_trailing_cards():
    warnings = []
    draws = parse_xsmm_html(fixture('xsmm-latest.html'), warnings=warnings)
    assert draws[0].draw_id == '01410'
    assert {d.draw_id for d in draws} == {'01410', '01409', '01408', '01407'}
    assert len(warnings) == 6
    assert '01401' not in {d.draw_id for d in draws}
    assert all(d.bonus_number.isdigit() for d in draws)


def test_cvwork_redirect_latest_never_fills_requested_older_id():
    target = f'{CVWORK_URL}/01371'
    http = HTTP(
        {
            target: [lambda: response(302, headers={'Location': CVWORK_URL})],
            CVWORK_URL: [lambda: response(text=fixture('cvwork-01371-redirect.html'))],
            f'{XSMM_URL}/ky-01371': [lambda: response(text='<html>unavailable</html>')],
        }
    )
    client = VietlottPower655Client(http)
    with pytest.raises(VietlottSourceError, match='01371'):
        client._fetch_fallback_detail('01371')
    assert [url for _, url, _ in http.calls][:2] == [target, CVWORK_URL]
    assert all(call[2]['allow_redirects'] is False for call in http.calls)


def test_xsmm_targeted_id_can_repair_cvwork_redirect():
    http = HTTP(
        {
            f'{CVWORK_URL}/01371': [lambda: response(text=fixture('cvwork-01371-redirect.html'))],
            f'{XSMM_URL}/ky-01371': [lambda: response(text=fixture('xsmm-01371.html'))],
        }
    )
    result = VietlottPower655Client(http)._fetch_fallback_detail('01371')
    assert result.draw_id == '01371' and result.draw_date == date(2026, 7, 14)
    assert result.source_url == f'{XSMM_URL}/ky-01371'


def test_all_upstreams_outage_bounded_and_latest_unknown(tmp_path):
    http = HTTP({})
    client = VietlottPower655Client(http)
    report = sync_missing_results(SQLiteRepository(tmp_path / 'empty.sqlite3'), client)
    assert report.status == 'unavailable' and report.stored_draws == 0
    assert report.missing_draw_ids == ()  # Do not invent publication IDs from a calendar.
    assert 'chưa biết' in report.failed_message
    assert client.request_count == 3


def test_budget_includes_redirects_and_retries():
    http = HTTP(
        {
            HISTORY_URL: [lambda: response(302, headers={'Location': HISTORY_URL + '?page=1'})],
            HISTORY_URL + '?page=1': [lambda: response(503)],
        }
    )
    client = VietlottPower655Client(http, sleep=lambda _: None)
    batch = client.fetch_missing_draws(set(), max_requests=2)
    assert batch.request_count == 2
    assert not batch.draws
    assert all(kwargs['timeout'] <= 4 for _, _, kwargs in http.calls)


def test_403_route_circuit_does_not_repeat_for_each_draw():
    http = HTTP({})
    client = VietlottPower655Client(http)
    for draw_id in ['01371', '01372', '01373']:
        with pytest.raises(VietlottSourceError):
            client._get_text(DETAIL_URL_TEMPLATE.format(draw_id=draw_id))
    assert client.request_count == 1


def test_long_retry_after_is_deferred_without_early_retry():
    http = HTTP({HISTORY_URL: [lambda: response(429, headers={'Retry-After': '60'})]})
    slept = []
    client = VietlottPower655Client(http, sleep=slept.append)
    with pytest.raises(VietlottSourceError, match='Retry-After=60'):
        client._get_text(HISTORY_URL)
    assert client.request_count == 1 and not slept


def test_short_retry_after_is_respected():
    http = HTTP({HISTORY_URL: [lambda: response(429, headers={'Retry-After': '1'}), lambda: response(text='ok')]})
    slept = []
    assert VietlottPower655Client(http, sleep=slept.append)._get_text(HISTORY_URL) == 'ok'
    assert slept == [1]


def test_redirect_to_other_host_or_http_is_rejected():
    for target in ['http://cvwork.vn/result', 'https://evil.example/result']:
        client = VietlottPower655Client(
            HTTP({CVWORK_URL: [lambda target=target: response(302, headers={'Location': target})]})
        )
        with pytest.raises(VietlottSourceError, match='Chuyển hướng'):
            client._get_text(CVWORK_URL)
        assert client.request_count == 1


def test_large_body_is_rejected():
    client = VietlottPower655Client(HTTP({CVWORK_URL: [lambda: response(text='x' * 1_048_577)]}))
    with pytest.raises(VietlottSourceError, match='dung lượng'):
        client._get_text(CVWORK_URL)


def test_observed_source_conflict_quarantines_before_any_save(tmp_path):
    html = fixture('cvwork-01410.html')
    conflicting = fixture('xsmm-01410.html').replace('>53</div>', '>54</div>')
    http = HTTP(
        {
            HISTORY_URL: [lambda: response(403)],
            CVWORK_URL: [lambda: response(text=html)],
            XSMM_URL: [lambda: response(text=conflicting)],
        }
    )
    repo = SQLiteRepository(tmp_path / 'conflict.sqlite3')
    report = sync_missing_results(repo, VietlottPower655Client(http))
    assert report.stored_draws == 0
    assert report.status == 'conflict'
    assert report.conflict_draw_ids == ('01410',)
    assert repo.get_draw('01410') is None


@pytest.mark.parametrize(
    'change',
    [
        {'draw_date': date(2026, 7, 14)},
        {'bonus_number': '08'},
        {'main_numbers': ('01', '17', '20', '33', '41', '42')},
        {'draw_id': '01371'},
    ],
)
def test_repository_atomically_rejects_existing_outcome_changes(tmp_path, change):
    repo = SQLiteRepository(tmp_path / 'immutable.sqlite3')
    original = make_power_draw()
    repo.upsert_draw(original)
    with pytest.raises(OutcomeConflictError):
        repo.upsert_draw(replace(original, **change))
    assert repo.get_draw(original.draw_id) == replace(original, parser_version='vietlott-power655-v1')
    assert len(repo.draw_ids()) == 1


def test_repository_guard_is_atomic_for_multirecord_batch_and_preserves_unknown_enrichment(tmp_path):
    repo = SQLiteRepository(tmp_path / 'atomic.sqlite3')
    original = make_power_draw()
    fallback = replace(original, prizes=tuple(replace(p, winner_count=None, payout_vnd=None) for p in original.prizes))
    repo.upsert_draw(fallback)
    repo.upsert_draw(original)  # Monotonic unknown -> established values is permitted.
    repo.upsert_draw(fallback)
    assert repo.get_draw(original.draw_id).prizes == original.prizes
    new = replace(original, draw_id='01371', draw_date=date(2026, 7, 14))
    with pytest.raises(OutcomeConflictError):
        repo.upsert_draws([new, replace(original, bonus_number='08')])
    assert repo.get_draw('01371') is None


def test_conflicting_duplicate_page_is_never_last_writer_wins():
    target = {}
    draw = make_power_draw()
    with pytest.raises(VietlottConflictError):
        _merge_draws(target, [draw, replace(draw, bonus_number='08')])


@pytest.mark.parametrize(
    'now,expected',
    [
        (datetime(2026, 10, 15, 11, 29, tzinfo=UTC), date(2026, 10, 13)),
        (datetime(2026, 10, 15, 11, 30, tzinfo=UTC), date(2026, 10, 15)),
        (datetime(2026, 10, 16, 12, 0, tzinfo=UTC), date(2026, 10, 15)),
    ],
)
def test_vietnam_cutoff_remains_1830(now, expected):
    assert latest_available_date(now) == expected


def test_manual_and_cli_path_preserve_valid_data_on_conflict(tmp_path):
    repo = SQLiteRepository(tmp_path / 'partial.sqlite3')
    original = replace(make_power_draw('01409'), draw_date=date(2026, 10, 10))
    newest = parse_cvwork_html(fixture('cvwork-01410.html'))[0]
    repo.upsert_draw(original)

    class Client:
        def fetch_recent_batch(self, *args):
            return FetchBatch((replace(original, bonus_number='08'), newest), (), latest_draw_id='01410')

    report = ingest_latest(repo, Client())
    assert report.stored_draws == 1 and report.status == 'conflict'
    assert repo.get_draw('01409').bonus_number == original.bonus_number
    assert repo.get_draw('01410') is not None


def test_cooperative_deadline_stops_fallback_before_more_io():
    ticks = [0]

    class SlowHTTP(HTTP):
        def get(self, url, **kwargs):
            ticks[0] += 30
            return super().get(url, **kwargs)

    http = SlowHTTP({HISTORY_URL: [lambda: response(text='<html>Slow source</html>')]})
    batch = VietlottPower655Client(http, clock=lambda: ticks[0]).fetch_missing_draws(set())
    assert batch.request_count == 1
    assert not batch.draws and any('thời gian' in e or 'ngân sách' in e for e in batch.errors)


def test_stale_provider_with_zero_internal_gaps_is_not_healthy(tmp_path):
    repo = SQLiteRepository(tmp_path / 'stale.sqlite3')
    old = replace(make_power_draw('00001'), draw_date=date(2017, 8, 1))
    repo.upsert_draw(old)

    class Client:
        def fetch_missing_draws(self, existing):
            return FetchBatch((old,), (), latest_draw_id='00001')

    report = sync_missing_results(repo, Client())
    assert report.stored_draws == 0 and report.missing_draw_ids == ()
    assert report.status == 'stale' and 'dữ liệu cũ' in report.failed_message


def test_future_and_wrong_weekday_are_quarantined(tmp_path):
    repo = SQLiteRepository(tmp_path / 'dates.sqlite3')
    today_future = replace(make_power_draw('01411'), draw_date=date(2026, 10, 15))
    wrong_weekday = replace(make_power_draw('01412'), draw_date=date(2026, 10, 14))

    class Client:
        def fetch_missing_draws(self, existing):
            return FetchBatch((today_future, wrong_weekday), (), latest_draw_id='01412')

    report = sync_missing_results(repo, Client())
    assert report.stored_draws == 0 and not repo.draw_ids()
    assert '01411' in report.missing_draw_ids and '01412' in report.missing_draw_ids
    assert report.failed_message


def test_cli_exits_nonzero_after_partial_valid_write(tmp_path, monkeypatch, capsys):
    import fetch_vietlott_power655 as cli
    from vietlott_power655.ingestion import IngestionReport

    def partial(repository, client):
        repository.upsert_draw(make_power_draw())
        return IngestionReport(2, 1, 'pending 01371', missing_draw_ids=('01371',), status='partial')

    monkeypatch.setattr(cli, 'sync_missing_results', partial)
    monkeypatch.setattr('sys.argv', ['fetch', '--sync-missing', '--database', str(tmp_path / 'cli.sqlite3')])
    assert cli.main() == 1
    assert SQLiteRepository(tmp_path / 'cli.sqlite3').get_draw('01370') is not None
    output = capsys.readouterr().out
    assert '"stored_draws": 1' in output and '"missing_draw_ids": ["01371"]' in output


def test_repeated_identical_observation_preserves_database_bytes(tmp_path):
    import hashlib

    path = tmp_path / 'idempotent.sqlite3'
    repo = SQLiteRepository(path)
    draw = make_power_draw()
    repo.upsert_draw(draw)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    repo.upsert_draw(draw)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_real_client_keeps_newest_history_rows_when_older_detail_is_forbidden(tmp_path):
    repo = SQLiteRepository(tmp_path / 'history-partial.sqlite3')
    existing = {f'{i:05d}' for i in range(1, 1409)} - {'01399'}
    # A stored neighbor checks chronology while the remote IDs model the known historical prefix.
    repo.upsert_draw(replace(make_power_draw('01408'), draw_date=date(2026, 10, 8)))

    class HistoricalPrefix:
        def draw_ids(self):
            return existing | repo.draw_ids()

        def __getattr__(self, name):
            return getattr(repo, name)

    http = HTTP({HISTORY_URL: [lambda: response(text=fixture('official-history.html'))]})
    report = sync_missing_results(HistoricalPrefix(), VietlottPower655Client(http))
    assert report.stored_draws == 2
    assert report.missing_draw_ids == ('01399',)
    assert report.status == 'partial'
    assert repo.get_draw('01410').draw_date == date(2026, 10, 13)
    assert repo.get_draw('01409').draw_date == date(2026, 10, 10)
    assert repo.get_draw('01399') is None
    assert report.request_count <= 12


def test_repository_rejects_wrong_unused_date_against_adjacent_id(tmp_path):
    repo = SQLiteRepository(tmp_path / 'chronology.sqlite3')
    repo.upsert_draw(replace(make_power_draw('01409'), draw_date=date(2026, 10, 10)))
    with pytest.raises(OutcomeConflictError, match='thứ tự'):
        repo.upsert_draw(replace(make_power_draw('01408'), draw_date=date(2026, 10, 13)))


def test_missing_bonus_and_wrong_product_never_produce_observations():
    with pytest.raises(ValueError):
        parse_cvwork_html(fixture('cvwork-01410.html').replace('<span class="xbonus">26</span>', ''))
    with pytest.raises(ValueError):
        parse_xsmm_html(fixture('xsmm-01410.html').replace('Power 6/55', 'Mega 6/45'))


def test_malformed_old_cvwork_card_does_not_discard_thirteen_valid_results():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(fixture('cvwork-latest.html'), 'lxml')
    soup.select('.xcard')[-1].select_one('.xbonus').decompose()
    http = HTTP({CVWORK_URL: [lambda: response(text=str(soup))]})
    batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
    assert len(batch.draws) == 13
    assert batch.draws[0].draw_id == '01410'
    assert batch.latest_draw_id == '01410' and batch.missing_draw_ids == ()
    assert any('01397' in error and 'đã cách ly' in error for error in batch.errors)


def test_conflicting_duplicates_in_one_fallback_page_quarantine_only_that_id():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(fixture('cvwork-latest.html'), 'lxml')
    conflict = BeautifulSoup(str(soup.select('.xcard')[-1]), 'lxml').select_one('.xcard')
    conflict.select_one('.xbonus').string = '55'
    soup.append(conflict)
    # Repeating the original after the conflict must not revive the quarantined ID.
    soup.append(BeautifulSoup(fixture('cvwork-latest.html'), 'lxml').select('.xcard')[-1])
    http = HTTP({CVWORK_URL: [lambda: response(text=str(soup))]})
    batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
    assert len(batch.draws) == 13 and batch.draws[0].draw_id == '01410'
    assert '01397' not in {draw.draw_id for draw in batch.draws}
    assert batch.conflict_draw_ids == ('01397',)
    assert '01397' in batch.missing_draw_ids


def test_forbidden_error_body_is_never_read_or_retried_for_next_id():
    from requests import ReadTimeout

    consumed = []

    def stalled():
        result = response(403)

        def read(*args, **kwargs):
            consumed.append(True)
            raise ReadTimeout('403 error body stalled')
            yield b''

        result.iter_content = read
        return result

    urls = [DETAIL_URL_TEMPLATE.format(draw_id=draw_id) for draw_id in ['01399', '01400']]
    http = HTTP({url: [stalled] for url in urls})
    client = VietlottPower655Client(http)
    for url in urls:
        with pytest.raises(VietlottSourceError):
            client._get_text(url)
    assert not consumed
    assert client.request_count == 1 and len(http.calls) == 1


def test_invalid_older_official_date_keeps_valid_latest_result():
    damaged = fixture('official-history.html').replace('10/10/2026', '11/10/2026')
    http = HTTP(
        {
            HISTORY_URL: [lambda: response(text=damaged)],
            CVWORK_URL: [lambda: response(text=fixture('cvwork-01410.html'))],
        }
    )
    batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
    assert [draw.draw_id for draw in batch.draws] == ['01410']
    assert batch.missing_draw_ids == ()
    assert any('01409' in error and 'đã cách ly' in error for error in batch.errors)


def test_malformed_older_official_card_and_duplicate_conflict_do_not_drop_latest():
    from bs4 import BeautifulSoup

    for mode in ['missing_bonus', 'conflicting_duplicate']:
        soup = BeautifulSoup(fixture('official-history.html'), 'lxml')
        older = soup.select('tbody tr')[1]
        if mode == 'missing_bonus':
            older.select('span.bong_tron')[-1].decompose()
        else:
            duplicate = BeautifulSoup(str(older), 'lxml').select_one('tr')
            duplicate.select('span.bong_tron')[-1].string = '55'
            soup.select_one('tbody').append(duplicate)
            soup.select_one('tbody').append(BeautifulSoup(str(older), 'lxml').select_one('tr'))
        http = HTTP({HISTORY_URL: [lambda soup=soup: response(text=str(soup))]})
        batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
        assert '01410' in {draw.draw_id for draw in batch.draws}
        assert '01409' not in {draw.draw_id for draw in batch.draws}
        if mode == 'conflicting_duplicate':
            assert batch.conflict_draw_ids == ('01409',)


@pytest.mark.parametrize('damage', ['number', 'missing_id', 'date_mismatch'])
def test_xsmm_bad_older_card_keeps_newest_three_valid_cards(damage):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(fixture('xsmm-latest.html'), 'lxml')
    card = soup.select('table.lottery-result-table')[3].parent
    if damage == 'number':
        card.select('div.rounded-full')[0].string = '56'
    elif damage == 'missing_id':
        card.find('a', href=lambda href: href and '/ky-' in href).decompose()
    else:
        card.find('h2').string = 'XS Power - Thứ ba, 05/10/2026'
    http = HTTP({XSMM_URL: [lambda: response(text=str(soup))]})
    batch = VietlottPower655Client(http).fetch_missing_draws({f'{i:05d}' for i in range(1, 1410)})
    assert {draw.draw_id for draw in batch.draws} == {'01410', '01409', '01408'}
    assert batch.latest_draw_id == '01410' and batch.missing_draw_ids == ()
    assert any('đã cách ly' in error for error in batch.errors)


def test_recent_status_cannot_clear_unchecked_gap_or_existing_conflict():
    from vietlott_power655.ingestion import IngestionReport, reconcile_sync_status

    prior = IngestionReport(
        1, 0, 'known conflict', missing_draw_ids=('01399',), conflict_draw_ids=('01399',), checked_at=100
    )
    unrelated = IngestionReport(0, 0, checked_draw_ids=('01410',), checked_at=110)
    merged = reconcile_sync_status(prior, unrelated)
    assert merged.failed_message and merged.conflict_draw_ids == ('01399',)
    # An older cached positive observation cannot resolve a newer conflict.
    cached = IngestionReport(0, 0, checked_draw_ids=('01399',), checked_at=90)
    still_pending = reconcile_sync_status(merged, cached)
    assert still_pending.conflict_draw_ids == ('01399',) and still_pending.checked_at == 110
    confirmed = IngestionReport(0, 0, checked_draw_ids=('01399',), checked_at=120)
    recovered = reconcile_sync_status(still_pending, confirmed)
    assert not recovered.failed_message and not recovered.conflict_draw_ids
