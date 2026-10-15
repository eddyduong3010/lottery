from dataclasses import replace
from datetime import date
from pathlib import Path

import bcrypt
from helpers import make_draw
from streamlit.testing.v1 import AppTest
from test_power655 import make_power_draw

from vietlott_power655.repository import SQLiteRepository as PowerRepository
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
    assert app.title[0].value == 'Xổ số miền Nam — thống kê'


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
    assert app.title[0].value == 'Xổ số miền Nam — thống kê'
    assert len(app.tabs) == 0
    assert not app.text_input
    assert len(app.dataframe) >= 2
    assert len(app.get('vega_lite_chart')) >= 1

    app.radio(key='_xsmn_section').set_value('Dò vé').run()
    next(item for item in app.text_input if item.label == 'Số vé').set_value('012345')
    next(item for item in app.button if item.label == 'Dò vé').click()
    app.run()

    assert not app.exception
    assert any('trúng tổng cộng' in message.value for message in app.success)


def navigation_app(tmp_path, monkeypatch):
    xsmn_path = tmp_path / 'navigation-xsmn.sqlite3'
    power_path = tmp_path / 'navigation-power.sqlite3'
    repository = SQLiteRepository(xsmn_path)
    for selected_date in (date(2026, 6, 26), date(2026, 7, 3), date(2026, 7, 10)):
        repository.upsert_draw(replace(make_draw(), draw_date=selected_date))
    power_repository = PowerRepository(power_path)
    for draw_id, selected_date in [
        ('01368', date(2026, 7, 7)),
        ('01369', date(2026, 7, 9)),
        ('01370', date(2026, 7, 11)),
    ]:
        power_repository.upsert_draw(replace(make_power_draw(draw_id), draw_date=selected_date))
    monkeypatch.setenv('XSMN_DATABASE_PATH', str(xsmn_path))
    monkeypatch.setenv('VIETLOTT_POWER655_DATABASE_PATH', str(power_path))
    monkeypatch.setenv('LOTTERY_PREDICTION_HISTORY_PATH', str(tmp_path / 'history.json'))
    monkeypatch.setenv('LOTTERY_AUTO_SYNC_ENABLED', '0')
    monkeypatch.setenv('LOTTERY_AUTH_DISABLED', '1')
    return AppTest.from_file(str(APP_PATH), default_timeout=30).run()


def test_navigation_preserves_game_filters_and_renders_only_active_section(tmp_path, monkeypatch):
    app = navigation_app(tmp_path, monkeypatch)
    app.radio(key='_xsmn_suffix_digits').set_value(3).run()
    app.date_input(key='_xsmn_range').set_value((date(2026, 7, 3), date(2026, 7, 10))).run()
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    assert not app.exception
    assert not any(item.label == 'Độ dài đuôi số' for item in app.radio)
    assert not any(item.label == 'Ngày trên vé' for item in app.date_input)
    app.checkbox(key='_power655_include_bonus').check().run()
    app.radio(key='_power655_section').set_value('Kết quả theo kỳ').run()
    app.selectbox(key='_power655_result_draw').set_value('01369').run()
    assert not any(item.label == 'Khoảng thời gian Power 6/55' for item in app.date_input)
    app.radio(key='_active_game').set_value('Xổ số miền Nam').run()
    assert app.radio(key='_xsmn_section').value == 'Thống kê'
    assert app.radio(key='_xsmn_suffix_digits').value == 3
    assert app.date_input(key='_xsmn_range').value == (date(2026, 7, 3), date(2026, 7, 10))
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    assert app.radio(key='_power655_section').value == 'Kết quả theo kỳ'
    assert app.selectbox(key='_power655_result_draw').value == '01369'
    app.radio(key='_power655_section').set_value('Thống kê').run()
    assert app.checkbox(key='_power655_include_bonus').value is True
    assert not app.exception


def test_ticket_inputs_survive_navigation_without_mixing_products(tmp_path, monkeypatch):
    app = navigation_app(tmp_path, monkeypatch)
    app.radio(key='_xsmn_section').set_value('Dò vé').run()
    app.text_input(key='_xsmn_ticket').set_value('012345')
    next(item for item in app.button if item.label == 'Dò vé').click().run()
    assert any('trúng tổng cộng' in item.value for item in app.success)
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    app.radio(key='_power655_section').set_value('Dò vé').run()
    assert len(app.text_input) == 1
    assert app.text_input(key='_power655_ticket').value == ''
    app.text_input(key='_power655_ticket').set_value('09 17 20 33 41 42')
    next(item for item in app.button if item.label == 'Dò vé Power 6/55').click().run()
    assert any('Jackpot 1' in item.value for item in app.success)
    app.radio(key='_active_game').set_value('Xổ số miền Nam').run()
    assert app.text_input(key='_xsmn_ticket').value == '012345'
    assert not app.exception


def test_incomplete_date_range_does_not_show_wrong_sample(tmp_path, monkeypatch):
    app = navigation_app(tmp_path, monkeypatch)
    app.date_input(key='_xsmn_range').set_value((date(2026, 7, 3),)).run()
    assert not app.exception
    assert any('Chọn đủ ngày' in item.value for item in app.info)
    assert not app.get('vega_lite_chart')
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    app.date_input(key='_power655_range').set_value(()).run()
    assert not app.exception
    assert any('Chọn đủ ngày' in item.value for item in app.info)


def test_all_sections_and_compatibility_entrypoint_render(tmp_path, monkeypatch):
    app = navigation_app(tmp_path, monkeypatch)
    for game, key in [('Xổ số miền Nam', 'xsmn'), ('Vietlott Power 6/55', 'power655')]:
        app.radio(key='_active_game').set_value(game).run()
        for section in ['Thống kê', 'Kết quả theo kỳ', 'Dò vé', 'Lịch mở thưởng', 'Tham khảo & kiểm chứng']:
            app.radio(key=f'_{key}_section').set_value(section).run()
            assert not app.exception, (game, section)
            assert app.title[0].value == f'{game} — {section.lower()}'
    wrapper = AppTest.from_file(str(APP_PATH.parents[1] / 'xsmn_app.py'), default_timeout=30).run()
    assert not wrapper.exception
    assert wrapper.title[0].value == 'Xổ số miền Nam — thống kê'


def test_hidden_reference_sections_do_not_generate_prediction_history(tmp_path, monkeypatch):
    calls = []

    def record_history(*args, **kwargs):
        calls.append(args)
        return {'schema_version': 1, 'predictions': []}

    monkeypatch.setattr('prediction_history.update_prediction_history', record_history)
    app = navigation_app(tmp_path, monkeypatch)
    app.radio(key='_xsmn_section').set_value('Kết quả theo kỳ').run()
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    assert not calls
    app.radio(key='_power655_section').set_value('Tham khảo & kiểm chứng').run()
    assert len(calls) == 1
    assert not app.exception


def test_power_refresh_shows_new_data_on_the_same_run(tmp_path, monkeypatch):
    from types import SimpleNamespace

    def ingest_new_draw(repository, client, **kwargs):
        repository.upsert_draw(replace(make_power_draw('01371'), draw_date=date(2026, 7, 14)))
        return SimpleNamespace(stored_draws=1, failed_message='')

    monkeypatch.setattr('vietlott_power655.ingestion.ingest_latest', ingest_new_draw)
    app = navigation_app(tmp_path, monkeypatch)
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    next(item for item in app.button if item.label == 'Cập nhật Power 6/55').click().run()
    assert not app.exception
    assert any('→ 14-07-2026' in item.value for item in app.caption)
    assert any('Đã lưu 1 kỳ' in item.value for item in app.success)


def test_performance_defaults_to_current_version_and_verified_timing(tmp_path, monkeypatch):
    from vietlott_power655.prediction import MODEL_VERSION as POWER_VERSION
    from xsmn.prediction import FULL_DRAW_MODEL_VERSION as XSMN_VERSION

    records = []
    for game, current_version in [('xsmn', XSMN_VERSION), ('power655', POWER_VERSION)]:
        for version, created_at, target, winner in [
            (current_version, '2026-07-06T10:00:00+07:00', '2026-07-07', False),
            (current_version, '2026-07-09T20:00:00+07:00', '2026-07-09', True),
            ('legacy-v1', '2026-07-10T10:00:00+07:00', '2026-07-11', True),
        ]:
            candidate = '012345' if game == 'xsmn' else '09 17 20 33 41 42'
            records.append(
                {
                    'game': game,
                    'model_version': version,
                    'target_date': target,
                    'created_at': created_at,
                    'training_cutoff': '2026-07-01',
                    'station_code': 'VL' if game == 'xsmn' else '',
                    'candidates': [{'rank': 1, 'value': candidate, 'model_score': 0}],
                    'actual': {'special_number': candidate}
                    if game == 'xsmn'
                    else {'main_numbers': candidate, 'bonus_number': '40'},
                    'metrics': {
                        'exact_hit': winner,
                        'best_main_matches': 6 if winner else 1,
                        'best_suffix_digits': 6 if winner else 0,
                        'prize_hits': 1 if winner else 0,
                        'prize_total': 18,
                        'best_candidate': candidate,
                    },
                }
            )
    monkeypatch.setattr(
        'prediction_history.update_prediction_history', lambda *args, **kwargs: {'predictions': records}
    )
    app = navigation_app(tmp_path, monkeypatch)
    for game, label, version, exact_label in [
        ('xsmn', 'Xổ số miền Nam', XSMN_VERSION, 'ĐB trùng 6 số'),
        ('power655', 'Vietlott Power 6/55', POWER_VERSION, 'Trúng 6/6'),
    ]:
        app.radio(key='_active_game').set_value(label).run()
        app.radio(key=f'_{game}_section').set_value('Tham khảo & kiểm chứng').run()
        assert not app.exception
        assert app.selectbox(key=f'_{game}_performance_version').value == version
        assert app.selectbox(key=f'_{game}_performance_timing').value == 'Chỉ bản lưu trước kỳ quay'
        assert next(item for item in app.metric if item.label == exact_label).value == '0'
        assert next(item for item in app.metric if item.label == 'Kỳ đã đối chiếu').value == '1'
        performance_table = app.dataframe[-1].value
        assert performance_table['Phiên bản mô hình'].tolist() == [version]
        assert performance_table['Ngày quay'].tolist() == ['07-07-2026']

        app.selectbox(key=f'_{game}_performance_timing').set_value('Tất cả (hồi cứu/chưa xác minh)').run()
        assert next(item for item in app.metric if item.label == exact_label).value == '1'
        assert next(item for item in app.metric if item.label == 'Kỳ đã đối chiếu').value == '2'
        assert any('Chế độ hồi cứu' in item.value for item in app.warning)
        app.selectbox(key=f'_{game}_performance_version').set_value('legacy-v1').run()
        assert app.dataframe[-1].value['Phiên bản mô hình'].tolist() == ['legacy-v1']
        assert not app.exception


def test_full_prize_backfill_does_not_inflate_verified_original_ticket_metrics(tmp_path, monkeypatch):
    from copy import deepcopy

    from xsmn.prediction import FULL_DRAW_MODEL_VERSION

    backfilled = {
        'game': 'xsmn',
        'station_code': 'VL',
        'model_version': FULL_DRAW_MODEL_VERSION,
        'target_date': '2026-10-14',
        'created_at': '2026-10-13T10:00:00+07:00',
        'training_cutoff': '2026-07-01',
        'prize_predictions_created_at': '2026-10-15T10:00:00+07:00',
        'prize_training_cutoff': '2026-07-01',
        'candidates': [{'rank': 1, 'value': '012345', 'model_score': 0}],
        'prize_predictions': [{'prize_code': 'g8', 'ordinal': 1, 'number': '99', 'model_score': 0}],
        'actual': {'special_number': '012345', 'prize_results': {'g8': ['99']}},
        'metrics': {
            'exact_hit': True,
            'best_suffix_digits': 6,
            'best_candidate': '012345',
            'prize_hits': 18,
            'prize_total': 18,
        },
    }
    verified = deepcopy(backfilled)
    verified.update(
        target_date='2026-07-07',
        created_at='2026-07-06T10:00:00+07:00',
        prize_predictions_created_at='2026-07-06T10:00:00+07:00',
    )
    verified['metrics'].update(exact_hit=False, best_suffix_digits=0, prize_hits=0)
    verified['prize_predictions'][0]['number'] = '01'
    records = [backfilled, verified]
    monkeypatch.setattr(
        'prediction_history.update_prediction_history', lambda *args, **kwargs: {'predictions': records}
    )
    app = navigation_app(tmp_path, monkeypatch)
    app.radio(key='_xsmn_section').set_value('Tham khảo & kiểm chứng').run()
    assert not app.exception
    assert next(item for item in app.metric if item.label == 'ĐB trùng 6 số').value == '1'
    assert next(item for item in app.metric if item.label == 'Khớp toàn giải TB').value == '0.000/18'
    assert any('thiếu mốc lưu riêng' in item.value for item in app.warning)
    assert any(
        'Nhật ký đã đối chiếu đến 14-10-2026; kho thống kê hiện có đến 10-07-2026' in item.value for item in app.caption
    )
    table = app.dataframe[-1].value
    backfilled_row = table.loc[table['Ngày quay'] == '14-10-2026'].iloc[0]
    assert 'Toàn giải chưa xác minh' in backfilled_row['Đối chiếu toàn bộ giải']
    assert 'ĐB khớp đuôi 6/6' in backfilled_row['Đối chiếu toàn bộ giải']
    assert backfilled_row['Số trùng chính xác'] == ''
    assert any('Toàn giải chưa xác minh thời điểm lưu' in item.value for item in app.markdown)
    app.selectbox(key='_xsmn_performance_timing').set_value('Tất cả (hồi cứu/chưa xác minh)').run()
    assert not app.exception
    retrospective_metric = next(item for item in app.metric if item.label == 'Toàn giải (hồi cứu)')
    assert retrospective_metric.value == '9.000/18'
    assert retrospective_metric.delta == 'Chỉ đối chiếu hồi cứu'
    assert any('Đối chiếu hồi cứu: Giải tám: 99' in item.value for item in app.markdown)


def test_power_reference_odds_use_vietnamese_number_format(tmp_path, monkeypatch):
    app = navigation_app(tmp_path, monkeypatch)
    app.radio(key='_active_game').set_value('Vietlott Power 6/55').run()
    app.radio(key='_power655_section').set_value('Tham khảo & kiểm chứng').run()
    assert not app.exception
    probabilities = next(item.value for item in app.dataframe if 'Tỷ lệ 1 trên' in item.value.columns)
    assert probabilities['Tỷ lệ 1 trên'].iloc[0] == '28.989.675'
    assert probabilities['Tỷ lệ 1 trên'].iloc[1] == '4.831.612,5'
    assert app.slider(key='_power655_recent_window').label == 'Số kỳ gần đây để thống kê tần suất Power 6/55'
