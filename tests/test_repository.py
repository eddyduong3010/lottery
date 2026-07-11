import sqlite3

import pytest
from helpers import make_draw

from xsmn.repository import SQLiteRepository


def test_sqlite_roundtrip_and_idempotent_upsert(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / 'xsmn.sqlite3')
    draw = make_draw({'g7': ['007'], 'db': ['003405']})

    repository.upsert_draw(draw)
    repository.upsert_draw(draw)

    loaded = repository.get_draw(draw.draw_date, draw.station_code)
    assert loaded is not None
    assert loaded.special_number == '003405'
    assert loaded.results_for('g7')[0].number == '007'
    assert repository.count_draws() == 1
    assert len(repository.load_results()) == 18


def test_multi_station_upsert_is_atomic(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / 'xsmn.sqlite3')
    repository.initialize()
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_bd BEFORE INSERT ON draws
            WHEN NEW.station_code = 'BD'
            BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match='forced failure'):
        repository.upsert_draws((make_draw(station_code='VL'), make_draw(station_code='BD')))

    assert repository.count_draws() == 0
