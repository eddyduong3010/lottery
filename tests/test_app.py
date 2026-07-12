from dataclasses import replace
from datetime import date
from pathlib import Path

import bcrypt

from helpers import make_draw
from streamlit.testing.v1 import AppTest

from xsmn.repository import SQLiteRepository

APP_PATH = Path(__file__).resolve().parents[1] / 'src' / 'xsmn_app.py'


def test_streamlit_app_requires_valid_login(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('XSMN_DATABASE_PATH', str(tmp_path / 'xsmn.sqlite3'))
    monkeypatch.setenv('VIETLOTT_POWER655_DATABASE_PATH', str(tmp_path / 'power.sqlite3'))
    monkeypatch.setenv('LOTTERY_PREDICTION_HISTORY_PATH', str(tmp_path / 'prediction-history.json'))
    monkeypatch.setenv('LOTTERY_AUTO_SYNC_ENABLED', '0')
    monkeypatch.setenv('LOTTERY_AUTH_DISABLED', '0')
    monkeypatch.setenv('LOTTERY_AUTH_USERNAME', 'minh')
    monkeypatch.setenv(
        'LOTTERY_AUTH_PASSWORD_HASH',
        bcrypt.hashpw(b'test-password', bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv('LOTTERY_AUTH_COOKIE_KEY', 'test-cookie-key-with-at-least-thirty-two-characters')
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()

    assert app.title[0].value == 'Đăng nhập hệ thống xổ số'
    assert not app.exception
    next(item for item in app.text_input if item.label == 'Tài khoản').set_value('minh')
    next(item for item in app.text_input if item.label == 'Mật khẩu').set_value('test-password')
    next(item for item in app.button if item.label == 'Đăng nhập').click()
    app.run()

    assert not app.exception
    assert app.title[0].value == 'Xổ số miền Nam — thống kê & dò vé'


def test_streamlit_app_handles_empty_database(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / 'empty-xsmn-app.sqlite3'
    monkeypatch.setenv('XSMN_DATABASE_PATH', str(database_path))
    monkeypatch.setenv('VIETLOTT_POWER655_DATABASE_PATH', str(tmp_path / 'empty-power655-app.sqlite3'))
    monkeypatch.setenv('LOTTERY_PREDICTION_HISTORY_PATH', str(tmp_path / 'prediction-history.json'))
    monkeypatch.setenv('LOTTERY_AUTO_SYNC_ENABLED', '0')
    monkeypatch.setenv('LOTTERY_AUTH_DISABLED', '1')

    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()

    assert not app.exception
    assert any('Chưa có dữ liệu XSMN' in message.value for message in app.info)


def test_streamlit_app_renders_chart_and_tables(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / 'xsmn-app-test.sqlite3'
    repository = SQLiteRepository(database_path)
    for selected_date in (date(2026, 6, 26), date(2026, 7, 3), date(2026, 7, 10)):
        repository.upsert_draw(replace(make_draw(), draw_date=selected_date))
    monkeypatch.setenv('XSMN_DATABASE_PATH', str(database_path))
    monkeypatch.setenv('VIETLOTT_POWER655_DATABASE_PATH', str(tmp_path / 'power655-app-test.sqlite3'))
    monkeypatch.setenv('LOTTERY_PREDICTION_HISTORY_PATH', str(tmp_path / 'prediction-history.json'))
    monkeypatch.setenv('LOTTERY_AUTO_SYNC_ENABLED', '0')
    monkeypatch.setenv('LOTTERY_AUTH_DISABLED', '1')

    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()

    assert not app.exception
    assert app.title[0].value == 'Xổ số miền Nam — thống kê & dò vé'
    assert len(app.tabs) == 6
    assert len(app.dataframe) >= 2
    assert len(app.get('vega_lite_chart')) >= 1

    next(item for item in app.text_input if item.label == 'Số vé').set_value('012345')
    next(item for item in app.button if item.label == 'Dò vé').click()
    app.run()

    assert not app.exception
    assert any('trúng tổng cộng' in message.value for message in app.success)
