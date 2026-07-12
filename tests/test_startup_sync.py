from dataclasses import replace
from datetime import date

from helpers import make_draw
from vietlott_power655.models import Power655Draw, PrizeTierResult
from vietlott_power655.ingestion import sync_missing_results as sync_power_missing
from vietlott_power655.repository import SQLiteRepository as PowerRepository
from xsmn.calendar import stations_for_date
from xsmn.ingestion import ingest_missing_range_parallel
from xsmn.ingestion import sync_missing_results as sync_xsmn_missing
from xsmn.repository import SQLiteRepository as XSMNRepository


def make_power_draw(draw_id: str, draw_date: date) -> Power655Draw:
    return Power655Draw(
        draw_id=draw_id,
        draw_date=draw_date,
        main_numbers=('09', '17', '20', '33', '41', '42'),
        bonus_number='40',
        prizes=(
            PrizeTierResult('jackpot1', 0, 100_000_000_000),
            PrizeTierResult('jackpot2', 0, 3_000_000_000),
            PrizeTierResult('first', 0, 40_000_000),
            PrizeTierResult('second', 0, 500_000),
            PrizeTierResult('third', 0, 50_000),
        ),
    )


class FakeXSMNClient:
    def __init__(self, selected_date: date) -> None:
        self.selected_date = selected_date
        self.requested_dates: list[date] = []

    def fetch_date(self, selected_date: date):
        self.requested_dates.append(selected_date)
        return tuple(
            replace(make_draw(station_code=station_code), draw_date=selected_date)
            for station_code in stations_for_date(selected_date)
        )


class FakePowerClient:
    def __init__(self, draws) -> None:
        self.draws = {draw.draw_id: draw for draw in draws}
        self.detail_requests: list[str] = []

    def fetch_history(self):
        return list(reversed(self.draws.values()))

    def fetch_detail(self, draw_id: str):
        self.detail_requests.append(draw_id)
        return self.draws[draw_id]


def test_xsmn_startup_sync_fetches_incomplete_date(tmp_path, monkeypatch) -> None:
    selected_date = date(2026, 7, 10)
    repository = XSMNRepository(tmp_path / 'xsmn.sqlite3')
    first_station = stations_for_date(selected_date)[0]
    repository.upsert_draw(replace(make_draw(station_code=first_station), draw_date=selected_date))
    monkeypatch.setattr('xsmn.ingestion.latest_available_date', lambda: selected_date)
    client = FakeXSMNClient(selected_date)

    report = sync_xsmn_missing(repository, client)

    assert report.requested_days == 1
    assert not report.failed_dates
    assert repository.count_draws() == len(stations_for_date(selected_date))
    assert client.requested_dates == [selected_date]


def test_parallel_xsmn_backfill_skips_complete_dates(tmp_path) -> None:
    first_date = date(2026, 7, 9)
    second_date = date(2026, 7, 10)
    repository = XSMNRepository(tmp_path / 'xsmn-parallel.sqlite3')
    repository.upsert_draws(
        replace(make_draw(station_code=station_code), draw_date=first_date)
        for station_code in stations_for_date(first_date)
    )
    client = FakeXSMNClient(second_date)

    report = ingest_missing_range_parallel(
        repository,
        first_date,
        second_date,
        workers=4,
        client_factory=lambda: client,
        batch_draw_limit=2,
    )

    assert report.requested_days == 1
    assert not report.failed_dates
    assert set(client.requested_dates) == {second_date}
    assert repository.count_draws() == len(stations_for_date(first_date)) + len(stations_for_date(second_date))


def test_parallel_xsmn_backfill_skips_known_incomplete_source_date(tmp_path) -> None:
    selected_date = date(2008, 9, 2)
    repository = XSMNRepository(tmp_path / 'xsmn-source-gap.sqlite3')
    client = FakeXSMNClient(selected_date)

    report = ingest_missing_range_parallel(
        repository,
        selected_date,
        selected_date,
        workers=4,
        client_factory=lambda: client,
    )

    assert report.requested_days == 0
    assert client.requested_dates == []


def test_power_startup_sync_fetches_only_missing_draw_ids(tmp_path) -> None:
    repository = PowerRepository(tmp_path / 'power.sqlite3')
    draws = [
        make_power_draw('00001', date(2017, 8, 1)),
        make_power_draw('00002', date(2017, 8, 3)),
        make_power_draw('00003', date(2017, 8, 5)),
    ]
    repository.upsert_draws(draws[:2])
    client = FakePowerClient(draws)

    report = sync_power_missing(repository, client)

    assert report.requested_draws == 1
    assert report.stored_draws == 1
    assert client.detail_requests == []
    assert repository.draw_ids() == {'00001', '00002', '00003'}
