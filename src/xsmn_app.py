from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth

from prediction_history import (
    prediction_comparison_rows,
    prediction_performance_frame,
    prediction_performance_summary,
    prediction_prize_timing_verified,
    prediction_timing_verified,
    saved_prediction_candidates,
    saved_xsmn_prize_predictions,
    update_prediction_history,
)
from startup_sync import StartupSyncReport, sync_all_missing
from vietlott_power655.analytics import frequency_statistics as power655_frequency_statistics
from vietlott_power655.analytics import prize_probabilities as power655_prize_probabilities
from vietlott_power655.analytics import top_frequency_table as power655_top_frequency_table
from vietlott_power655.calendar import latest_available_date as power655_latest_available_date
from vietlott_power655.calendar import next_draw_date as power655_next_draw_date
from vietlott_power655.config import DRAW_WEEKDAYS as POWER655_DRAW_WEEKDAYS
from vietlott_power655.ingestion import ingest_all as ingest_power655_all
from vietlott_power655.ingestion import ingest_latest as ingest_power655_latest
from vietlott_power655.ingestion import reconcile_sync_status
from vietlott_power655.prediction import MODEL_VERSION as POWER655_MODEL_VERSION
from vietlott_power655.prediction import rank_number_candidates
from vietlott_power655.prize_checker import check_ticket as check_power655_ticket
from vietlott_power655.repository import SQLiteRepository as Power655Repository
from vietlott_power655.repository import prize_table as power655_prize_table
from vietlott_power655.scraper import VietlottPower655Client
from xsmn.analytics import frequency_matrix, frequency_statistics, top_frequency_table
from xsmn.calendar import latest_available_date, next_draw_date, next_regional_draw_date, stations_for_date
from xsmn.config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS, STATIONS, WEEKDAY_LABELS, WEEKLY_SCHEDULE
from xsmn.ingestion import ingest_missing_range_parallel
from xsmn.prediction import FULL_DRAW_MODEL_VERSION, rank_suffix_candidates
from xsmn.prize_checker import check_ticket
from xsmn.repository import SQLiteRepository

ROOT = Path(__file__).resolve().parents[1]


st.set_page_config(
    page_title='Xổ số · thống kê & dò vé',
    page_icon='🎟️',
    layout='wide',
    initial_sidebar_state='expanded',
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 3rem;}
      /* Streamlit 1.59 marks previous-run elements with data-stale; keep geometry while replacing them. */
      [data-testid="stMain"] [data-testid="stElementContainer"][data-stale="true"] {
        opacity: 0 !important;
        transition: none !important;
        pointer-events: none;
      }
      [data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e2e8f0; padding: 1rem; border-radius: .75rem;}
      .ticket-number {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 1.35rem; letter-spacing: .12em;}
      .muted-card {background:#f8fafc; border-left:4px solid #2563eb; padding:.8rem 1rem; border-radius:.4rem;}
      .prediction-comparison {display:grid; gap:.65rem; margin:.4rem 0 1rem;}
      .prediction-card {border:1px solid #e2e8f0; border-radius:.75rem; padding:.75rem .9rem; background:#fff;}
      .prediction-card-title {font-weight:650; margin-bottom:.5rem;}
      .prediction-line {display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; margin:.3rem 0;}
      .prediction-label {min-width:8.5rem; color:#64748b; font-size:.88rem;}
      .number-chip, .lottery-number {font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-weight:650; letter-spacing:.06em;}
      .number-chip {display:inline-flex; padding:.2rem .42rem; border-radius:.38rem; background:#f1f5f9; border:1px solid #cbd5e1;}
      .number-match {background:#dcfce7; border-color:#22c55e; color:#166534; border-radius:.28rem; padding:.08rem .16rem;}
      .number-bonus {background:#fef3c7; border-color:#f59e0b; color:#92400e;}
      .prize-match {margin-top:.45rem; color:#166534; font-size:.9rem;}
      @media (max-width: 1100px) {
        .block-container {padding: 1.15rem 1.25rem 2rem;}
        .block-container h1 {font-size: 1.85rem; line-height: 1.25;}
        [data-testid="stMetricValue"] {font-size: 1.6rem;}
        [data-testid="stMetricLabel"] p {font-size: .85rem;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

DATABASE_PATH = Path(os.environ.get('XSMN_DATABASE_PATH', ROOT / 'data' / 'xsmn.sqlite3'))
POWER655_DATABASE_PATH = Path(
    os.environ.get('VIETLOTT_POWER655_DATABASE_PATH', ROOT / 'data' / 'vietlott_power655.sqlite3')
)
PREDICTION_HISTORY_PATH = Path(
    os.environ.get('LOTTERY_PREDICTION_HISTORY_PATH', ROOT / 'data' / 'prediction_history.json')
)
AUTO_SYNC_ENABLED = os.environ.get('LOTTERY_AUTO_SYNC_ENABLED', '1').strip().lower() not in {'0', 'false', 'no'}
AUTO_SYNC_TTL_SECONDS = max(int(os.environ.get('LOTTERY_AUTO_SYNC_TTL_SECONDS', '900')), 60)
XSMN_BOOTSTRAP_DAYS = max(int(os.environ.get('XSMN_AUTO_SYNC_BOOTSTRAP_DAYS', '30')), 1)
# Temporarily public by default. Set LOTTERY_AUTH_DISABLED=0 to restore the login gate.
AUTH_DISABLED = os.environ.get('LOTTERY_AUTH_DISABLED', '1').strip().lower() in {'1', 'true', 'yes'}


@st.cache_resource
def get_repository(path: str) -> SQLiteRepository:
    repository = SQLiteRepository(path)
    repository.initialize()
    return repository


@st.cache_resource
def get_power655_repository(path: str) -> Power655Repository:
    repository = Power655Repository(path)
    repository.initialize()
    return repository


@st.cache_data(show_spinner=False)
def load_results(path: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    return SQLiteRepository(path).load_results()


@st.cache_data(show_spinner=False)
def load_power655_draws(path: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    return Power655Repository(path).load_draws()


@st.cache_data(ttl=AUTO_SYNC_TTL_SECONDS, show_spinner=False)
def run_startup_sync(xsmn_path: str, power655_path: str, bootstrap_days: int) -> StartupSyncReport:
    return sync_all_missing(xsmn_path, power655_path, bootstrap_days)


def render_power_sync_status(report, latest_date: date | None) -> None:
    latest_label = latest_date.strftime('%d-%m-%Y') if latest_date else 'chưa có dữ liệu'
    sources = getattr(report, 'sources', ())
    providers = sorted({urlsplit(source).hostname or source for source in sources})
    backup = any(provider != 'vietlott.vn' for provider in providers)
    pending = getattr(report, 'missing_draw_ids', ())
    if report.failed_message:
        pending_label = f'còn {len(pending)} kỳ cần kiểm tra' if pending else 'chưa xác minh đầy đủ kết quả mới nhất'
        st.warning(f'Power 6/55: dữ liệu đến {latest_label}; {pending_label}. Xem chi tiết nguồn bên dưới.')
    else:
        source_label = 'đã dùng nguồn dự phòng' if backup else 'đã kiểm tra nguồn chính'
        st.success(f'Đã lưu {report.stored_draws} kỳ Power 6/55. Dữ liệu đến {latest_label}; {source_label}.')
        if getattr(report, 'status', '') == 'degraded':
            st.caption('Một phần nguồn chưa phản hồi đầy đủ; kết quả hợp lệ đã được giữ lại.')
    if providers:
        st.caption('Nguồn Power: ' + ', '.join(providers))
    if getattr(report, 'checked_at', None):
        st.caption('Kiểm tra Power: ' + datetime.fromtimestamp(report.checked_at, ZoneInfo('Asia/Ho_Chi_Minh')).isoformat(timespec='seconds'))
    errors = getattr(report, 'source_errors', ())
    if report.failed_message or errors or sources:
        with st.expander('Chi tiết nguồn và kỳ Power còn thiếu'):
            for source in sources:
                st.caption(source)
            if report.failed_message:
                st.write(report.failed_message)
            for error in errors:
                st.caption(error)
            if pending:
                st.write('Kỳ cần kiểm tra: ' + ', '.join(pending))


def database_modified_ns(path: Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


def auth_config() -> tuple[str, str, str]:
    secret_username = ''
    secret_password_hash = ''
    secret_cookie_key = ''
    try:
        auth_secrets = st.secrets.get('auth', {})
        secret_username = str(auth_secrets.get('username', ''))
        secret_password_hash = str(auth_secrets.get('password_hash', ''))
        secret_cookie_key = str(auth_secrets.get('cookie_key', ''))
    except FileNotFoundError:
        pass
    return (
        os.environ.get('LOTTERY_AUTH_USERNAME', secret_username),
        os.environ.get('LOTTERY_AUTH_PASSWORD_HASH', secret_password_hash),
        os.environ.get('LOTTERY_AUTH_COOKIE_KEY', secret_cookie_key),
    )


def require_authentication() -> stauth.Authenticate | None:
    if AUTH_DISABLED:
        return None
    expected_username, password_hash, cookie_key = auth_config()

    login_header = st.empty()
    login_header.title('Đăng nhập hệ thống xổ số')
    if not expected_username or not password_hash or not cookie_key:
        st.error('Máy chủ chưa cấu hình thông tin đăng nhập.')
        st.stop()
    credentials = {
        'usernames': {
            expected_username: {
                'name': expected_username,
                'password': password_hash,
            }
        }
    }
    authenticator = stauth.Authenticate(
        credentials,
        cookie_name='minh_lottery_auth',
        cookie_key=cookie_key,
        cookie_expiry_days=7,
        auto_hash=False,
    )
    if not st.session_state.get('authentication_status'):
        try:
            native_cookie = st.context.cookies.get('minh_lottery_auth')
            if native_cookie:
                authenticator.authentication_controller.login(token=native_cookie)
        except Exception:
            # Fall back to the component cookie reader used internally by streamlit-authenticator.
            pass
    authenticator.login(
        location='main',
        max_login_attempts=5,
        clear_on_submit=True,
        fields={
            'Form name': 'Đăng nhập để tiếp tục',
            'Username': 'Tài khoản',
            'Password': 'Mật khẩu',
            'Login': 'Đăng nhập',
        },
    )
    authentication_status = st.session_state.get('authentication_status')
    if authentication_status is False:
        st.error('Tài khoản hoặc mật khẩu không đúng.')
    if authentication_status is not True:
        st.caption('Sau khi đăng nhập, trình duyệt sẽ được ghi nhớ trong 7 ngày.')
        st.stop()
    login_header.empty()
    return authenticator


def persistent_widget(kind: str, label: str, *, key: str, default=None, options=None, **kwargs):
    """Keep each page's inputs when Streamlit cleans up widgets on another page."""
    widget_key = f'_{key}'
    value = st.session_state.get(widget_key, st.session_state.get(key, default))
    if options is not None:
        options = list(options)
        if kind == 'multiselect':
            value = [item for item in (value or []) if item in options]
        elif value not in options:
            value = options[0] if options else None
    if kind == 'slider':
        value = max(kwargs['min_value'], min(value, kwargs['max_value']))
    if kind == 'date_input':

        def clamp_date(item):
            return min(max(item, kwargs.get('min_value', item)), kwargs.get('max_value', item))

        value = tuple(clamp_date(item) for item in value) if isinstance(value, (tuple, list)) else clamp_date(value)
    st.session_state[widget_key] = value
    widget = getattr(st, kind)
    if options is None:
        value = widget(label, key=widget_key, **kwargs)
    else:
        value = widget(label, options=options, key=widget_key, **kwargs)
    st.session_state[key] = value
    return value


def selected_date_range(value) -> tuple[date, date] | None:
    """An unfinished range must not silently become a different statistical sample."""
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            return None
        return value[0], value[1]
    return value, value


def format_vnd(value: int) -> str:
    return f'{value:,.0f} ₫'.replace(',', '.')


def format_percent(value: float) -> str:
    return f'{100 * value:.2f}%'


def format_odds(value: float) -> str:
    precision = 0 if float(value).is_integer() else 1
    return f'{value:,.{precision}f}'.translate(str.maketrans({',': '.', '.': ','}))


def format_optional_vnd(value: int | None) -> str:
    if value is None:
        return 'Theo jackpot kỳ quay'
    return format_vnd(value)


def _format_prediction_date(value: str) -> str:
    return pd.to_datetime(value).strftime('%d-%m-%Y')


def _suffix_number_html(number: str, matching_suffix_digits: int) -> str:
    safe_number = escape(number)
    if matching_suffix_digits <= 0 or matching_suffix_digits > len(number):
        return f'<span class="lottery-number">{safe_number}</span>'
    prefix = escape(number[:-matching_suffix_digits])
    suffix = escape(number[-matching_suffix_digits:])
    return f'<span class="lottery-number">{prefix}<span class="number-match">{suffix}</span></span>'


def _number_chips_html(
    numbers: list[str],
    matched_main_numbers: set[str],
    bonus_number: str = '',
    matched_bonus: bool = False,
) -> str:
    chips = []
    for number in numbers:
        css_class = 'number-chip'
        if number in matched_main_numbers:
            css_class += ' number-match'
        elif matched_bonus and number == bonus_number:
            css_class += ' number-bonus'
        chips.append(f'<span class="{css_class}">{escape(number)}</span>')
    return ' '.join(chips)


def render_prediction_comparisons(history: dict, game: str, *, retrospective: bool = False) -> None:
    rows = prediction_comparison_rows(history, game, limit=8)
    if not rows:
        return
    cards = []
    for row in rows:
        if game == 'xsmn':
            station_code = str(row['station_code'])
            station_name = STATIONS[station_code].name if station_code in STATIONS else station_code
            predicted_number = row['predicted_numbers'][0] if row['predicted_numbers'] else ''
            actual_number = row['actual_main_numbers'][0] if row['actual_main_numbers'] else ''
            suffix_digits = int(row['matching_suffix_digits'])
            prize_matches = row['prize_matches']
            matching_records = [
                record
                for record in history.get('predictions', [])
                if record.get('game') == 'xsmn'
                and str(record.get('target_date', '')) == str(row['target_date'])
                and str(record.get('station_code', '')) == station_code
                and str(record.get('model_version', '')) == str(row['model_version'])
            ]
            prize_verified = bool(matching_records) and all(
                prediction_prize_timing_verified(record) for record in matching_records
            )
            if not prize_verified and not retrospective:
                prize_html = (
                    '<div class="prize-match" style="color:#64748b">Toàn giải chưa xác minh thời điểm lưu.</div>'
                )
            elif prize_matches:
                matched_text = ' · '.join(
                    f'{PRIZE_SPECS[match["prize_code"]].label}: {escape(match["number"])}' for match in prize_matches
                )
                prize_label = 'Đối chiếu hồi cứu' if retrospective else 'Trùng chính xác toàn giải'
                prize_html = f'<div class="prize-match">✓ {prize_label}: {matched_text}</div>'
            else:
                prize_html = (
                    '<div class="prize-match" style="color:#64748b">Chưa có số trùng chính xác toàn giải.</div>'
                )
            cards.append(
                '<div class="prediction-card">'
                f'<div class="prediction-card-title">{_format_prediction_date(str(row["target_date"]))} · '
                f'{escape(station_name)}</div>'
                '<div class="prediction-line"><span class="prediction-label">ĐB dự đoán</span>'
                f'{_suffix_number_html(predicted_number, suffix_digits)}</div>'
                '<div class="prediction-line"><span class="prediction-label">ĐB thực tế</span>'
                f'{_suffix_number_html(actual_number, suffix_digits)}</div>'
                f'{prize_html}</div>'
            )
            continue

        matched_main = set(row['matched_main_numbers'])
        bonus_number = str(row['actual_bonus_number'])
        matched_bonus = bool(row['matched_bonus'])
        cards.append(
            '<div class="prediction-card">'
            f'<div class="prediction-card-title">{_format_prediction_date(str(row["target_date"]))}</div>'
            '<div class="prediction-line"><span class="prediction-label">Bộ dự đoán</span>'
            f'{_number_chips_html(row["predicted_numbers"], matched_main, bonus_number, matched_bonus)}</div>'
            '<div class="prediction-line"><span class="prediction-label">6 số chính</span>'
            f'{_number_chips_html(row["actual_main_numbers"], matched_main)}</div>'
            '<div class="prediction-line"><span class="prediction-label">Số đặc biệt</span>'
            f'{_number_chips_html([bonus_number], set(), bonus_number, matched_bonus)}</div>'
            '</div>'
        )
    st.markdown('##### Đối chiếu trực quan các kỳ gần nhất')
    st.markdown(f'<div class="prediction-comparison">{"".join(cards)}</div>', unsafe_allow_html=True)
    if game == 'xsmn':
        st.caption(
            'Nền xanh đánh dấu phần đuôi trùng nhau. Toàn giải chỉ được kiểm chứng khi có mốc lưu riêng trước kỳ quay; chế độ hồi cứu không chứng minh năng lực dự báo.'
        )
    else:
        st.caption('Nền xanh: số chính trùng. Nền vàng: vé có số đặc biệt của kỳ quay.')


def render_prediction_performance(history: dict, game: str, *, database_latest: date | None = None) -> None:
    title = 'XSMN' if game == 'xsmn' else 'Power 6/55'
    st.markdown(f'#### Nhật ký và hiệu suất dự đoán {title}')
    records = [record for record in history.get('predictions', []) if record.get('game') == game]
    if not records:
        st.info('Nhật ký sẽ bắt đầu lưu từ kỳ dự đoán kế tiếp.')
        return

    evaluated_dates = [
        str(record['target_date']) for record in records if record.get('actual') and record.get('target_date')
    ]
    journal_latest = _format_prediction_date(max(evaluated_dates)) if evaluated_dates else 'chưa có kỳ đối chiếu'
    database_label = f'{database_latest:%d-%m-%Y}' if database_latest else 'chưa có dữ liệu'
    st.caption(
        f'Nhật ký đã đối chiếu đến {journal_latest}; kho thống kê hiện có đến {database_label}. '
        'Nhật ký và kho kết quả được lưu riêng.'
    )

    # Scope every metric, chart, comparison and export to one model and one timing policy.
    newest_first = sorted(
        records,
        key=lambda record: (str(record.get('target_date', '')), str(record.get('created_at', ''))),
        reverse=True,
    )
    versions = list(dict.fromkeys(str(record.get('model_version') or 'unknown') for record in newest_first))
    current_version = FULL_DRAW_MODEL_VERSION if game == 'xsmn' else POWER655_MODEL_VERSION
    default_version = current_version if current_version in versions else versions[0]
    version_col, timing_col = st.columns(2)
    with version_col:
        selected_version = persistent_widget(
            'selectbox',
            'Phiên bản cần đánh giá',
            key=f'{game}_performance_version',
            options=versions,
            default=default_version,
        )
    with timing_col:
        timing_scope = persistent_widget(
            'selectbox',
            'Điều kiện thời điểm lưu',
            key=f'{game}_performance_timing',
            options=['Chỉ bản lưu trước kỳ quay', 'Tất cả (hồi cứu/chưa xác minh)'],
            default='Chỉ bản lưu trước kỳ quay',
        )
    version_records = [
        record for record in records if str(record.get('model_version') or 'unknown') == selected_version
    ]
    scoped_records = (
        [record for record in version_records if prediction_timing_verified(record)]
        if timing_scope == 'Chỉ bản lưu trước kỳ quay'
        else version_records
    )
    history = {**history, 'predictions': scoped_records}
    st.caption(
        f'Đang xem {len(scoped_records)}/{len(version_records)} bản lưu của {selected_version}. '
        'Mốc xác minh: ngày lưu có múi giờ và ngày cuối dữ liệu đều trước ngày quay; '
        'bản lưu cùng ngày quay chưa được xác minh.'
    )
    if timing_scope != 'Chỉ bản lưu trước kỳ quay':
        st.warning(
            'Chế độ hồi cứu có thể gồm bản lưu sau kỳ quay hoặc thiếu bằng chứng thời điểm; không dùng để khẳng định độ chính xác dự báo.'
        )
    retrospective = timing_scope != 'Chỉ bản lưu trước kỳ quay'
    summary = prediction_performance_summary(history, game)
    display_history = history
    if game == 'xsmn':
        prize_records = [record for record in scoped_records if prediction_prize_timing_verified(record)]
        prize_summary = prediction_performance_summary({**history, 'predictions': prize_records}, game)
        if not retrospective:
            # Preserve the original ticket comparison, but never certify a later full-prize backfill.
            display_history = {
                **history,
                'predictions': [
                    record
                    if prediction_prize_timing_verified(record)
                    else {
                        **record,
                        'prize_predictions': [],
                        'metrics': {
                            **(record.get('metrics') or {}),
                            'prize_hits': None,
                            'prize_total': None,
                            'prize_hit_rate': None,
                        },
                    }
                    for record in scoped_records
                ],
            }
        missing_prize_evidence = sum(
            bool(record.get('actual')) and not prediction_prize_timing_verified(record) for record in scoped_records
        )
        if missing_prize_evidence:
            st.warning(
                f'{missing_prize_evidence} bản đối chiếu toàn giải thiếu mốc lưu riêng trước kỳ quay. '
                'Mốc lưu vé đặc biệt không xác nhận thời điểm tạo đủ 18 số; phần này chỉ xem được trong chế độ hồi cứu.'
            )
    frame = prediction_performance_frame(display_history, game)
    if frame.empty:
        st.info('Chưa có bản lưu đáp ứng bộ lọc này. Chờ kỳ tiếp theo hoặc chọn chế độ hồi cứu để kiểm tra nhật ký cũ.')
        return

    evaluated_count = int(summary['evaluated_count'])
    baseline_valid = summary.get('baseline_comparison_valid', True) and evaluated_count > 0
    metric_columns = st.columns(4)
    metric_columns[0].metric(
        'Kỳ đã đối chiếu', int(summary['evaluated_count']), f'{int(summary["saved_count"])} kỳ đã lưu'
    )
    metric_columns[1].metric(
        'ĐB trùng 6 số' if game == 'xsmn' else 'Trúng 6/6',
        int(summary['exact_hits']) if evaluated_count else '—',
        f'{float(summary["exact_rate"]):.2%}' if evaluated_count else 'Chờ kết quả',
        delta_color='off',
    )
    if game == 'xsmn':
        metric_columns[2].metric(
            'ĐB khớp đuôi ≥2',
            f'{float(summary["suffix_two_rate"]):.2%}' if evaluated_count else '—',
            f'Ngẫu nhiên {float(summary["random_suffix_two_rate"]):.2%}'
            if baseline_valid
            else 'Chưa đủ điều kiện so sánh',
            delta_color='off',
        )
        full_prize_summary = summary if retrospective else prize_summary
        full_prize_count = int(full_prize_summary['evaluated_count'])
        metric_columns[3].metric(
            'Toàn giải (hồi cứu)' if retrospective else 'Khớp toàn giải TB',
            f'{float(full_prize_summary["average_prize_hits"]):.3f}/18' if full_prize_count else '—',
            'Chỉ đối chiếu hồi cứu'
            if retrospective
            else (
                f'Ngẫu nhiên {float(prize_summary["random_expected_prize_hits"]):.4f}'
                if full_prize_count and prize_summary.get('baseline_comparison_valid', False)
                else 'Chưa đủ mốc lưu toàn giải'
            ),
            delta_color='off',
        )
        if not retrospective:
            st.caption(f'Thống kê toàn giải: {full_prize_count} kỳ có mốc lưu riêng đã xác minh.')
        display = frame.assign(
            station=frame['station_code'].map(lambda code: STATIONS[code].name if code in STATIONS else code),
            score=frame.apply(
                lambda row: (
                    ''
                    if not row['evaluated']
                    else (
                        (
                            'Toàn giải chưa xác minh'
                            if pd.isna(row['prize_hits'])
                            else f'{int(row["prize_hits"])}/{int(row["prize_total"])} kết quả'
                        )
                        + (
                            ''
                            if pd.isna(row['best_suffix_digits'])
                            else f' · ĐB khớp đuôi {int(row["best_suffix_digits"])}/6'
                        )
                    )
                ),
                axis=1,
            ),
        )[
            [
                'target_date',
                'station',
                'candidates',
                'actual',
                'best_candidate',
                'matched_prizes',
                'score',
                'model_version',
            ]
        ].rename(
            columns={
                'target_date': 'Ngày quay',
                'station': 'Đài',
                'candidates': 'Dãy đặc biệt đã dự đoán',
                'actual': 'Giải đặc biệt thật',
                'best_candidate': 'Dãy đặc biệt đối chiếu',
                'matched_prizes': 'Số trùng chính xác',
                'score': 'Đối chiếu toàn bộ giải',
                'model_version': 'Phiên bản mô hình',
            }
        )
    else:
        metric_columns[2].metric(
            'Số chính khớp TB',
            f'{float(summary["average_main_matches"]):.2f}/6' if evaluated_count else '—',
            f'Ngẫu nhiên {float(summary["random_expected_main_matches"]):.2f}'
            if baseline_valid
            else 'Chưa đủ điều kiện so sánh',
            delta_color='off',
        )
        metric_columns[3].metric(
            'Kỳ khớp ≥2 số chính',
            f'{float(summary["at_least_two_rate"]):.2%}' if evaluated_count else '—',
            f'Ngẫu nhiên {float(summary["random_at_least_two_rate"]):.2%}'
            if baseline_valid
            else 'Chưa đủ điều kiện so sánh',
            delta_color='off',
        )
        display = frame.assign(
            score=frame['best_main_matches'].map(lambda value: '' if pd.isna(value) else f'{int(value)}/6 số')
        )[
            ['target_date', 'candidates', 'actual', 'best_candidate', 'matched_numbers', 'score', 'model_version']
        ].rename(
            columns={
                'target_date': 'Ngày quay',
                'candidates': 'Các bộ đã dự đoán',
                'actual': 'Kết quả thật và số đặc biệt',
                'best_candidate': 'Bộ gần nhất',
                'matched_numbers': 'Số chính trùng',
                'score': 'Số chính khớp',
                'model_version': 'Phiên bản mô hình',
            }
        )
    if summary.get('timing_unverified_count', 0):
        st.warning(
            f'{summary["timing_unverified_count"]} bản lưu chưa chứng minh đủ mốc thời gian trước kỳ quay; '
            'không dùng các bản lưu này để khẳng định độ chính xác dự báo.'
        )
    if len(summary.get('model_versions', [])) > 1:
        st.caption('Nhật ký gồm nhiều phiên bản. Chỉ số gộp không đại diện riêng cho công thức hiện tại.')
    if summary.get('baseline_comparison_valid') is False and summary['evaluated_count']:
        st.caption('Chưa đủ điều kiện đối chiếu ngang bằng với ngẫu nhiên trên toàn bộ nhật ký này.')
    display['Ngày quay'] = pd.to_datetime(display['Ngày quay']).dt.strftime('%d-%m-%Y')
    render_prediction_comparisons(history, game, retrospective=retrospective)
    st.dataframe(display.head(50), hide_index=True, width='stretch')
    st.download_button(
        'Tải nhật ký CSV',
        data=display.to_csv(index=False).encode('utf-8-sig'),
        file_name=f'prediction-history-{game}.csv',
        mime='text/csv',
        key=f'download_prediction_history_{game}',
    )
    st.caption(
        'Các chỉ số mô tả bản lưu đã đối chiếu; thời điểm lưu cần được xác minh trước khi kết luận hiệu quả dự đoán. '
        'Baseline ngẫu nhiên được tính từ cơ cấu giải, không lấy từ một lần quay giả lập. '
        'Nhật ký giúp so sánh các phiên bản mô hình; không làm thay đổi xác suất toán học của xổ số.'
    )


def draw_result_table(draw) -> pd.DataFrame:
    rows = []
    for prize_code in PRIZE_DISPLAY_ORDER:
        spec = PRIZE_SPECS[prize_code]
        values = [result.number for result in draw.results_for(prize_code)]
        rows.append(
            {
                'Giải': spec.label,
                'Kết quả đầy đủ': '  •  '.join(values),
                'Giá trị mỗi giải': format_vnd(spec.payout_vnd),
            }
        )
    return pd.DataFrame(rows)


def render_statistical_overview(
    statistics: pd.DataFrame,
    draw_count: int,
    scoped_observations: int,
    suffix_digits: int,
    latest_data_date: date,
) -> None:
    top_numbers = top_frequency_table(statistics, limit=20)
    hottest = top_numbers.iloc[0]

    st.subheader('Tóm tắt dữ liệu')
    st.caption(f'Dữ liệu mới nhất trong bộ lọc: {latest_data_date:%d-%m-%Y}')
    metric_columns = st.columns(3)
    metric_columns[0].metric('Kỳ đài', f'{draw_count:,}'.replace(',', '.'))
    metric_columns[1].metric('Kết quả', f'{scoped_observations:,}'.replace(',', '.'))
    metric_columns[2].metric(
        f'Top đuôi {suffix_digits}',
        hottest['number'],
        f'{int(hottest["count"])} lần',
    )

    chart_col, table_col = st.columns([1.4, 1])
    with chart_col:
        st.subheader(f'Top đuôi {suffix_digits} số theo số lần xuất hiện')
        chart_data = top_numbers.set_index('number')[['count']]
        st.bar_chart(
            chart_data,
            color='#2563EB',
            x_label=f'Đuôi {suffix_digits} số',
            y_label='Số lần xuất hiện',
            height=420,
        )
    with table_col:
        st.subheader('Tỷ lệ và độ trễ')
        display = top_numbers[
            ['number', 'count', 'observation_rate', 'draws_with_number', 'draw_rate', 'gap_draws']
        ].rename(
            columns={
                'number': 'Số',
                'count': 'Số lần',
                'observation_rate': 'Tỷ lệ kết quả',
                'draws_with_number': 'Số kỳ có',
                'draw_rate': 'Tỷ lệ kỳ',
                'gap_draws': 'Ngày quay chưa về',
            }
        )
        display['Tỷ lệ kết quả'] = display['Tỷ lệ kết quả'].map(format_percent)
        display['Tỷ lệ kỳ'] = display['Tỷ lệ kỳ'].map(format_percent)
        st.dataframe(display, hide_index=True, width='stretch', height=420)

    if suffix_digits == 2:
        st.subheader('Ma trận tần suất 00–99')
        matrix = frequency_matrix(statistics)
        st.dataframe(
            matrix.style.background_gradient(cmap='Blues', axis=None).format('{:d}'),
            width='stretch',
        )
    st.caption(
        'Tỷ lệ kết quả = số lần xuất hiện / tổng số kết quả giải trong mẫu. '
        'Tỷ lệ kỳ = số kỳ đài có xuất hiện ít nhất một lần / tổng số kỳ đài.'
    )


authenticator = require_authentication()
if authenticator is not None:
    authenticator.logout('Đăng xuất', location='sidebar', use_container_width=True)

repository = get_repository(str(DATABASE_PATH))
power655_repository = get_power655_repository(str(POWER655_DATABASE_PATH))

startup_sync_report: StartupSyncReport | None = None
if AUTO_SYNC_ENABLED:
    with st.spinner('Đang tự động kiểm tra và tải các kết quả còn thiếu...'):
        startup_sync_report = run_startup_sync(
            str(DATABASE_PATH),
            str(POWER655_DATABASE_PATH),
            XSMN_BOOTSTRAP_DAYS,
        )

with st.sidebar:
    st.header('Xổ số')
    active_game = persistent_widget(
        'radio',
        'Sản phẩm',
        key='active_game',
        default='Xổ số miền Nam',
        options=['Xổ số miền Nam', 'Vietlott Power 6/55'],
    )
    game_key = 'xsmn' if active_game == 'Xổ số miền Nam' else 'power655'
    active_section = persistent_widget(
        'radio',
        'Chức năng',
        key=f'{game_key}_section',
        default='Thống kê',
        options=['Thống kê', 'Kết quả theo kỳ', 'Dò vé', 'Lịch mở thưởng', 'Tham khảo & kiểm chứng'],
    )

# A single replaceable pane prevents old and new sections mixing during reruns.
page_content = st.empty()
page_content.empty()
with page_content.container():
    st.title(f'{active_game} — {active_section.lower()}')

    with st.sidebar:
        st.header('Dữ liệu')
        manual_power_status = st.session_state.get('power_sync_report_override')
        power_sync = startup_sync_report.power655 if startup_sync_report is not None else None
        if (
            manual_power_status
            and time.time() - manual_power_status[0] < AUTO_SYNC_TTL_SECONDS
            and (power_sync is None or manual_power_status[0] >= getattr(power_sync, 'checked_at', 0))
        ):
            power_sync = manual_power_status[1]
        if startup_sync_report is not None:
            xsmn_sync = startup_sync_report.xsmn
            if xsmn_sync.failed_dates:
                st.warning(f'XSMN: {len(xsmn_sync.failed_dates)} ngày chưa cập nhật được.')
            else:
                st.caption(f'Kiểm tra XSMN: đã lưu thêm {xsmn_sync.stored_draws} kỳ đài.')
        if power_sync is not None:
            power_sync = reconcile_sync_status(st.session_state.get('power_sync_issue_report'), power_sync)
            st.session_state['power_sync_issue_report'] = (
                power_sync
                if getattr(power_sync, 'missing_draw_ids', ()) or getattr(power_sync, 'conflict_draw_ids', ())
                else None
            )
            render_power_sync_status(power_sync, power655_repository.date_bounds()[1])
        if game_key == 'xsmn':
            with st.expander('Cập nhật dữ liệu XSMN'):
                update_days = st.number_input('Số ngày cần cập nhật', min_value=1, max_value=10_000, value=30, step=1)
                update_workers = st.slider('Số luồng tải song song', min_value=1, max_value=24, value=12, step=1)
                if st.button('Cập nhật từ nguồn công khai', type='primary', width='stretch'):
                    end_date = latest_available_date()
                    start_date = end_date - timedelta(days=int(update_days) - 1)
                    progress = st.progress(0, text='Đang chuẩn bị...')

                    def update_progress(index: int, total: int, selected_date: date) -> None:
                        progress.progress(index / total, text=f'Đang tải {selected_date:%d-%m-%Y} ({index}/{total})')

                    with st.spinner('Đang đọc và kiểm tra cơ cấu giải...'):
                        report = ingest_missing_range_parallel(
                            repository,
                            start_date,
                            end_date,
                            workers=int(update_workers),
                            on_progress=update_progress,
                        )
                    load_results.clear()
                    if report.stored_draws:
                        st.success(f'Đã lưu {report.stored_draws} kỳ đài.')
                    elif not report.failed_dates:
                        st.info('Không có ngày hoặc đài nào còn thiếu trong khoảng đã chọn.')
                    if report.failed_dates:
                        with st.expander(f'{len(report.failed_dates)} ngày chưa tải được'):
                            for failed_date, error in report.failed_dates:
                                st.write(f'- {failed_date:%d-%m-%Y}: {error}')
                st.caption('Nguồn mặc định: xoso.com.vn (bản AMP). Không cần API key.')
        if game_key == 'power655':
            with st.expander('Cập nhật dữ liệu Power 6/55'):
                power655_limit = st.number_input(
                    'Số kỳ Vietlott cần cập nhật', min_value=1, max_value=50, value=8, step=1
                )
                if st.button('Cập nhật Power 6/55', type='primary', width='stretch'):
                    with st.spinner('Đang tải kết quả Power 6/55 từ Vietlott...'):
                        report = ingest_power655_latest(
                            power655_repository,
                            VietlottPower655Client(),
                            limit=int(power655_limit),
                            include_details=True,
                        )
                    load_power655_draws.clear()
                    report = reconcile_sync_status(power_sync, report)
                    st.session_state['power_sync_report_override'] = (time.time(), report)
                    st.rerun()
                if st.button('Tải toàn bộ lịch sử Power 6/55', width='stretch'):
                    with st.spinner('Đang tải toàn bộ lịch sử từ kỳ đầu tiên; quá trình này có thể mất vài phút...'):
                        report = ingest_power655_all(power655_repository, VietlottPower655Client())
                    load_power655_draws.clear()
                    report = reconcile_sync_status(power_sync, report)
                    st.session_state['power_sync_report_override'] = (time.time(), report)
                    st.rerun()

    results = load_results(str(DATABASE_PATH), database_modified_ns(DATABASE_PATH))
    power655_draws = load_power655_draws(str(POWER655_DATABASE_PATH), database_modified_ns(POWER655_DATABASE_PATH))
    prediction_history = {'schema_version': 1, 'predictions': []}
    prediction_history_error = ''
    if active_section == 'Tham khảo & kiểm chứng':
        try:
            prediction_history = update_prediction_history(
                PREDICTION_HISTORY_PATH,
                results,
                power655_draws,
            )
            prediction_history_error = ''
        except (OSError, ValueError) as exc:
            prediction_history = {'schema_version': 1, 'predictions': []}
            prediction_history_error = str(exc)
    first_date, last_date = repository.date_bounds()
    power655_first_date, power655_last_date = power655_repository.date_bounds()

    if game_key == 'xsmn' and results.empty:
        st.info(
            'Chưa có dữ liệu XSMN. Chọn số ngày ở thanh bên và bấm **Cập nhật từ nguồn công khai**, '
            'hoặc chạy `uv run src/fetch_xsmn.py --days 365`.'
        )

    data_first, data_last = (first_date, last_date) if game_key == 'xsmn' else (power655_first_date, power655_last_date)
    if data_first and data_last:
        st.caption(f'Dữ liệu: {data_first:%d-%m-%Y} → {data_last:%d-%m-%Y} · Bộ lọc lưu riêng · Giờ Việt Nam')
        expected_latest = latest_available_date() if game_key == 'xsmn' else power655_latest_available_date()
        if data_last < expected_latest:
            st.warning(
                f'Kết quả mới nhất: {data_last:%d-%m-%Y}; kỳ gần nhất dự kiến: {expected_latest:%d-%m-%Y}. '
                'Mở Cập nhật dữ liệu ở thanh bên để bổ sung.'
            )
        with st.sidebar.expander('Phạm vi dữ liệu'):
            st.caption(
                'Hiện hỗ trợ XSMN và Power 6/55. Ngày mới nhất không xác nhận lịch sử đã đầy đủ hoặc đã đối chiếu độc lập giữa các nguồn.'
            )

    if game_key == 'xsmn' and active_section == 'Thống kê':
        upcoming_date = next_regional_draw_date()
        upcoming_names = ', '.join(STATIONS[code].name for code in stations_for_date(upcoming_date))
        st.markdown(
            f'<div class="muted-card"><strong>Kỳ mở thưởng tiếp theo:</strong> '
            f'{upcoming_date:%d-%m-%Y} — {upcoming_names}</div>',
            unsafe_allow_html=True,
        )
        if not results.empty and first_date and last_date:
            with st.sidebar:
                st.divider()
                st.header('Bộ lọc thống kê')
                default_start = max(first_date, last_date - timedelta(days=365))
                selected_range = persistent_widget(
                    'date_input',
                    'Khoảng thời gian XSMN',
                    key='xsmn_range',
                    default=(default_start, last_date),
                    min_value=first_date,
                    max_value=last_date,
                )
                available_codes = sorted(results['station_code'].unique(), key=lambda code: STATIONS[code].name)
                station_mode = persistent_widget(
                    'radio',
                    'Đài thống kê',
                    key='xsmn_station_mode',
                    default='Tất cả đài',
                    options=['Tất cả đài', 'Tùy chọn'],
                    horizontal=True,
                )
                if station_mode == 'Tất cả đài':
                    selected_stations = available_codes
                else:
                    selected_stations = persistent_widget(
                        'multiselect',
                        'Chọn đài',
                        key='xsmn_stations',
                        options=available_codes,
                        default=available_codes[:1],
                        format_func=lambda code: STATIONS[code].name,
                    )
                prize_scope_label = persistent_widget(
                    'radio',
                    'Phạm vi',
                    key='xsmn_prize_scope',
                    default='Tất cả giải',
                    options=['Tất cả giải', 'Chỉ giải đặc biệt'],
                )
                suffix_digits = persistent_widget(
                    'radio', 'Độ dài đuôi số', key='xsmn_suffix_digits', default=2, options=[2, 3], horizontal=True
                )

            selected_dates = selected_date_range(selected_range)
            if selected_dates is None:
                st.info('Chọn đủ ngày bắt đầu và ngày kết thúc để xem thống kê.')
                st.stop()
            selected_start, selected_end = selected_dates
            filtered = results[
                (results['draw_date'].dt.date >= selected_start)
                & (results['draw_date'].dt.date <= selected_end)
                & results['station_code'].isin(selected_stations)
            ].copy()
            scope = 'special' if prize_scope_label == 'Chỉ giải đặc biệt' else 'all'
            statistics = frequency_statistics(filtered, suffix_digits=suffix_digits, prize_scope=scope)
            draw_count = int(filtered[['draw_date', 'station_code']].drop_duplicates().shape[0])
            scoped_observations = len(filtered[filtered['prize_code'] == 'db']) if scope == 'special' else len(filtered)

            if scoped_observations == 0:
                st.warning('Bộ lọc hiện tại không có kết quả để thống kê. Hãy chọn thêm đài hoặc đổi khoảng ngày.')
            else:
                latest_data_date = pd.to_datetime(filtered['draw_date']).max().date()
                render_statistical_overview(
                    statistics,
                    draw_count,
                    scoped_observations,
                    suffix_digits,
                    latest_data_date,
                )

    if game_key == 'xsmn' and active_section == 'Kết quả theo kỳ':
        st.subheader('Danh sách đầy đủ các số của từng kỳ')
        if not results.empty:
            draw_options = (
                results[['draw_date', 'station_code', 'station_name']]
                .drop_duplicates()
                .sort_values(['draw_date', 'station_name'], ascending=[False, True])
            )
            dates = draw_options['draw_date'].dt.date.drop_duplicates().tolist()
            selected_date = persistent_widget(
                'selectbox',
                'Ngày mở thưởng',
                key='xsmn_result_date',
                options=dates,
                format_func=lambda value: f'{value:%d-%m-%Y}',
            )
            date_draws = draw_options[draw_options['draw_date'].dt.date == selected_date]
            station_codes = date_draws['station_code'].tolist()
            station_code = persistent_widget(
                'selectbox',
                'Đài',
                key='xsmn_result_station',
                options=station_codes,
                format_func=lambda code: STATIONS[code].name,
            )
            draw = repository.get_draw(selected_date, station_code)
            if draw:
                st.markdown(f'### {draw.station_name} — {draw.draw_date:%d-%m-%Y}')
                st.dataframe(draw_result_table(draw), hide_index=True, width='stretch')
                if draw.source_url:
                    st.link_button('Mở trang nguồn', draw.source_url)

    if game_key == 'xsmn' and active_section == 'Dò vé':
        st.subheader('Dò vé số truyền thống miền Nam')
        st.caption('Nhập đủ 6 chữ số trên vé, chọn đúng ngày và đúng đài phát hành.')
        default_check_date = last_date or latest_available_date()
        check_date = persistent_widget('date_input', 'Ngày trên vé', default=default_check_date, key='xsmn_check_date')
        available_on_date = list(stations_for_date(check_date))
        check_station = persistent_widget(
            'selectbox',
            'Đài phát hành',
            options=available_on_date,
            format_func=lambda code: STATIONS[code].name,
            key='xsmn_check_station',
        )
        with st.form('ticket_check_form'):
            ticket_number = persistent_widget(
                'text_input', 'Số vé', key='xsmn_ticket', default='', max_chars=6, placeholder='Ví dụ: 012345'
            )
            check_submitted = st.form_submit_button('Dò vé', type='primary')
        if check_submitted:
            draw = repository.get_draw(check_date, check_station)
            if draw is None:
                st.warning('Chưa có kết quả của ngày/đài này trong dữ liệu. Hãy cập nhật dữ liệu trước.')
            else:
                try:
                    checked = check_ticket(ticket_number, draw)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if checked.is_winner:
                        st.success(f'Vé {checked.ticket_number} trúng tổng cộng {format_vnd(checked.total_payout_vnd)}')
                        hit_table = pd.DataFrame(
                            [
                                {
                                    'Giải': hit.label,
                                    'Kết quả đối chiếu': hit.matched_number,
                                    'Giá trị': format_vnd(hit.payout_vnd),
                                    'Chi tiết': hit.explanation,
                                }
                                for hit in checked.hits
                            ]
                        )
                        st.dataframe(hit_table, hide_index=True, width='stretch')
                    else:
                        st.info(f'Vé {checked.ticket_number} không trúng giải trong kỳ đã chọn.')
        st.caption(
            'Giá trị hiển thị là mức giải danh nghĩa cho vé 10.000₫, chưa tính thuế hoặc quy định lĩnh thưởng. '
            'Vé trùng nhiều giải được cộng đủ các giải tìm thấy.'
        )

    if game_key == 'xsmn' and active_section == 'Lịch mở thưởng':
        st.subheader('Lịch mở thưởng XSMN trong tuần')
        schedule_rows = [
            {
                'Ngày': WEEKDAY_LABELS[weekday],
                'Các đài': ' • '.join(STATIONS[code].name for code in WEEKLY_SCHEDULE[weekday]),
                'Giờ dự kiến': '16:15–16:35',
            }
            for weekday in range(7)
        ]
        st.dataframe(pd.DataFrame(schedule_rows), hide_index=True, width='stretch')
        upcoming_date = next_regional_draw_date()
        st.info(
            f'Kỳ gần nhất sắp mở: {WEEKDAY_LABELS[upcoming_date.weekday()]} {upcoming_date:%d-%m-%Y} — '
            + ', '.join(STATIONS[code].name for code in stations_for_date(upcoming_date))
        )
        st.caption('Lịch được tách thành cấu hình để có thể cập nhật khi cơ quan quản lý công bố thay đổi.')

    if game_key == 'xsmn' and active_section == 'Tham khảo & kiểm chứng':
        st.subheader('Bộ số tham khảo và kiểm chứng sau kỳ quay')
        st.warning(
            'Xổ số là quá trình ngẫu nhiên. Tần suất lịch sử không làm một số chắc chắn dễ trúng hơn. '
            'Chưa có bằng chứng dự đoán vượt lựa chọn ngẫu nhiên trên dữ liệu ngoài mẫu. '
        )
        if not results.empty:
            available_station_codes = set(results['station_code'].unique())
            upcoming_station_codes = [
                code for code in stations_for_date(next_regional_draw_date()) if code in available_station_codes
            ]
            remaining_station_codes = sorted(
                available_station_codes - set(upcoming_station_codes), key=lambda code: STATIONS[code].name
            )
            station_choices = upcoming_station_codes + remaining_station_codes
            prediction_station = persistent_widget(
                'selectbox',
                'Đài cần tham khảo',
                options=station_choices,
                format_func=lambda code: STATIONS[code].name,
                key='xsmn_prediction_station',
            )
            target_date = next_draw_date(prediction_station)
            station_results = results[
                (results['station_code'] == prediction_station) & (results['draw_date'].dt.date < target_date)
            ].copy()
            if station_results.empty:
                st.info('Chưa có dữ liệu trước kỳ tham khảo cho đài này.')
                st.stop()
            station_draw_count = station_results[['draw_date', 'station_code']].drop_duplicates().shape[0]
            training_latest = pd.to_datetime(station_results['draw_date']).max().date()
            if station_draw_count <= 1:
                recent_window = 1
                st.caption('Mới có 1 kỳ dữ liệu; mô hình đang ở trạng thái khởi đầu lạnh.')
            else:
                recent_window = persistent_widget(
                    'slider',
                    'Số kỳ gần đây để thống kê tần suất',
                    key='xsmn_recent_window',
                    min_value=1,
                    max_value=min(100, station_draw_count),
                    default=min(30, station_draw_count),
                )
            st.write(f'Kỳ kế tiếp dự kiến: **{target_date:%d-%m-%Y} — {STATIONS[prediction_station].name}**')
            st.caption(f'Dữ liệu lịch sử: {station_draw_count} kỳ, mới nhất đến {training_latest:%d-%m-%Y}.')
            if station_draw_count < 12:
                st.warning('Mẫu dưới 12 kỳ; thống kê tần suất có độ ổn định rất thấp.')
            if (latest_available_date() - training_latest).days > 8:
                st.warning('Dữ liệu của đài đã cũ hơn một chu kỳ tuần. Hãy cập nhật trước khi tham khảo.')
            ranked = rank_suffix_candidates(
                station_results,
                suffix_digits=2,
                prize_scope='all',
                recent_draws=recent_window,
                top_k=10,
            )
            if not ranked.empty:
                display_ranked = ranked.drop(columns=['gap_component', 'probability'], errors='ignore').rename(
                    columns={
                        'number': 'Đuôi 2 số',
                        'model_score': 'Tần suất làm trơn (%)',
                        'recent_frequency': 'Tần suất gần (%)',
                        'long_frequency': 'Tần suất dài hạn (%)',
                        'gap_draws': 'Số kỳ chưa xuất hiện',
                    }
                )
                display_ranked.attrs = {}
                chart_col, table_col = st.columns([1.2, 1])
                with chart_col:
                    st.markdown('#### Tần suất đuôi 2 số trong lịch sử')
                    st.bar_chart(
                        display_ranked.set_index('Đuôi 2 số')[['Tần suất làm trơn (%)']],
                        color='#D97706',
                        x_label='Đuôi 2 số',
                        y_label='Tần suất lịch sử (%)',
                        height=360,
                    )
                with table_col:
                    st.markdown('#### Chi tiết tần suất')
                    st.dataframe(display_ranked, hide_index=True, width='stretch', height=360)

                full_draw_prediction = saved_xsmn_prize_predictions(prediction_history, target_date, prediction_station)
                st.markdown('#### Bảng số tham khảo cố định theo cơ cấu giải')
                if full_draw_prediction.empty:
                    st.info('Chưa có đủ lịch sử trước ngày quay để sinh bảng dự đoán đầy đủ.')
                else:
                    prediction_rows = []
                    for prize_code in PRIZE_DISPLAY_ORDER:
                        prize_values = full_draw_prediction[full_draw_prediction['prize_code'] == prize_code]
                        if prize_values.empty:
                            continue
                        prediction_rows.append(
                            {
                                'Giải': PRIZE_SPECS[prize_code].label,
                                'Số kết quả': len(prize_values),
                                'Dự đoán cố định': '  •  '.join(prize_values['number'].astype(str)),
                            }
                        )
                    st.dataframe(pd.DataFrame(prediction_rows), hide_index=True, width='stretch')
                    st.caption(
                        'Đúng cơ cấu một kỳ đài: 1 giải tám, 1 giải bảy, 3 giải sáu, 1 giải năm, '
                        '7 giải tư, 2 giải ba, 1 giải nhì, 1 giải nhất và 1 giải đặc biệt — tổng 18 kết quả.'
                    )
                if not full_draw_prediction.empty:
                    st.caption(f'Phiên bản đã lưu: {full_draw_prediction.attrs.get("model_version", "chưa xác định")}.')
                st.caption(
                    'Bảng tần suất mô tả dữ liệu quá khứ; không phải xác suất trúng. '
                    'Số lâu chưa xuất hiện không có nghĩa là sắp xuất hiện. '
                    'Phiên bản mới lấy mẫu đồng đều; bản lưu cũ giữ nguyên để đối chiếu sau kỳ quay. '
                    'Mọi dãy 6 số có cùng xác suất giải đặc biệt 1/1.000.000.'
                )
        st.divider()
        if prediction_history_error:
            st.warning(f'Chưa ghi được nhật ký dự đoán: {prediction_history_error}')
        render_prediction_performance(prediction_history, 'xsmn', database_latest=last_date)
    if game_key == 'power655':
        if power655_draws.empty and active_section != 'Lịch mở thưởng':
            st.info(
                'Chưa có dữ liệu Power 6/55. Bấm **Cập nhật Power 6/55** hoặc chạy '
                '`uv run src/fetch_vietlott_power655.py --limit 8`.'
            )
        else:
            if active_section == 'Thống kê':
                default_power_start = power655_first_date
                selected_power_range = persistent_widget(
                    'date_input',
                    'Khoảng thời gian Power 6/55',
                    default=(default_power_start, power655_last_date),
                    min_value=power655_first_date,
                    max_value=power655_last_date,
                    key='power655_range',
                )
                include_bonus = persistent_widget(
                    'checkbox', 'Tính cả số đặc biệt vào thống kê tần suất', key='power655_include_bonus', default=False
                )
                selected_dates = selected_date_range(selected_power_range)
                if selected_dates is None:
                    st.info('Chọn đủ ngày bắt đầu và ngày kết thúc để xem thống kê.')
                    st.stop()
                power_start, power_end = selected_dates
                filtered_power = power655_draws[
                    (power655_draws['draw_date'].dt.date >= power_start)
                    & (power655_draws['draw_date'].dt.date <= power_end)
                ].copy()
                if filtered_power.empty:
                    st.warning('Khoảng thời gian hiện tại không có kỳ Power 6/55 nào.')
                else:
                    power_stats = power655_frequency_statistics(filtered_power, include_bonus=include_bonus)
                    top_power = power655_top_frequency_table(power_stats, limit=20)
                    metric_cols = st.columns(3)
                    metric_cols[0].metric('Số kỳ', f'{filtered_power["draw_id"].nunique():,}'.replace(',', '.'))
                    metric_cols[1].metric(
                        'Số quan sát', f'{power_stats.attrs["total_observations"]:,}'.replace(',', '.')
                    )
                    metric_cols[2].metric(
                        'Số xuất hiện nhiều nhất',
                        top_power.iloc[0]['number'],
                        f'{int(top_power.iloc[0]["count"])} lần',
                    )
                    chart_col, table_col = st.columns([1.3, 1])
                    with chart_col:
                        st.markdown('#### Top số 01-55 theo số lần xuất hiện')
                        st.bar_chart(
                            top_power.set_index('number')[['count']],
                            color='#DC2626',
                            x_label='Số',
                            y_label='Số lần xuất hiện',
                            height=380,
                        )
                    with table_col:
                        display_power = top_power.rename(
                            columns={
                                'number': 'Số',
                                'count': 'Số lần',
                                'observation_rate': 'Tỷ lệ quan sát',
                                'draws_with_number': 'Số kỳ có',
                                'draw_rate': 'Tỷ lệ kỳ',
                                'gap_draws': 'Số kỳ chưa về',
                            }
                        )
                        display_power['Tỷ lệ quan sát'] = display_power['Tỷ lệ quan sát'].map(format_percent)
                        display_power['Tỷ lệ kỳ'] = display_power['Tỷ lệ kỳ'].map(format_percent)
                        st.dataframe(display_power, hide_index=True, width='stretch', height=380)
                    st.caption(
                        'Tỷ lệ quan sát = số lần xuất hiện / tổng số bóng trong mẫu. '
                        'Tỷ lệ kỳ = số kỳ có số đó / tổng số kỳ trong bộ lọc.'
                    )

            if active_section == 'Kết quả theo kỳ':
                st.markdown('#### Danh sách đầy đủ các kỳ Power 6/55')
                draw_options = power655_draws.sort_values(['draw_date', 'draw_id'], ascending=[False, False])
                selected_power_draw = persistent_widget(
                    'selectbox',
                    'Kỳ quay',
                    key='power655_result_draw',
                    options=draw_options['draw_id'].tolist(),
                    format_func=lambda draw_id: (
                        f'#{draw_id} · '
                        f'{draw_options.loc[draw_options["draw_id"] == draw_id, "draw_date"].iloc[0].date():%d-%m-%Y}'
                    ),
                )
                power_draw = power655_repository.get_draw(selected_power_draw)
                if power_draw:
                    st.markdown(
                        f'### #{power_draw.draw_id} · {power_draw.draw_date:%d-%m-%Y} · '
                        f'{" ".join(power_draw.main_numbers)} | {power_draw.bonus_number}'
                    )
                    prize_display = power655_prize_table(power_draw)
                    prize_display['Giá trị giải'] = prize_display['Giá trị giải'].map(format_optional_vnd)
                    st.dataframe(prize_display, hide_index=True, width='stretch')
                    if power_draw.source_url:
                        st.link_button('Mở trang Vietlott', power_draw.source_url)

            if active_section == 'Dò vé':
                st.markdown('#### Dò vé Power 6/55')
                st.caption('Nhập 6 số trên vé, cách nhau bằng dấu cách, dấu phẩy hoặc gạch ngang.')
                draw_ids = power655_draws.sort_values(['draw_date', 'draw_id'], ascending=[False, False])[
                    'draw_id'
                ].tolist()
                check_power_draw_id = persistent_widget(
                    'selectbox', 'Kỳ cần dò', options=draw_ids, key='power655_check_draw'
                )
                with st.form('power655_ticket_form'):
                    power_ticket = persistent_widget(
                        'text_input',
                        'Bộ số dự thưởng',
                        key='power655_ticket',
                        default='',
                        placeholder='Ví dụ: 09 17 20 33 41 42',
                    )
                    power_submitted = st.form_submit_button('Dò vé Power 6/55', type='primary')
                if power_submitted:
                    power_draw = power655_repository.get_draw(check_power_draw_id)
                    if power_draw is None:
                        st.warning('Chưa có kỳ quay này trong dữ liệu.')
                    else:
                        try:
                            checked = check_power655_ticket(power_ticket, power_draw)
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            if checked.is_winner and checked.hit:
                                st.success(
                                    f'Bộ số {" ".join(checked.ticket_numbers)} trúng {checked.hit.label}: '
                                    f'{format_optional_vnd(checked.hit.payout_vnd)}'
                                )
                                st.write(checked.hit.explanation)
                            else:
                                st.info(f'Bộ số {" ".join(checked.ticket_numbers)} không trúng giải trong kỳ đã chọn.')
                st.caption(
                    'Giá trị giải hiển thị là mức danh nghĩa theo bảng Vietlott từng kỳ, chưa tính thuế hoặc điều kiện lĩnh thưởng.'
                )

            if active_section == 'Lịch mở thưởng':
                st.markdown('#### Lịch mở thưởng Power 6/55')
                schedule_rows = [
                    {'Ngày': WEEKDAY_LABELS[weekday], 'Sản phẩm': 'Power 6/55', 'Giờ dự kiến': '18:00-18:30'}
                    for weekday in POWER655_DRAW_WEEKDAYS
                ]
                st.dataframe(pd.DataFrame(schedule_rows), hide_index=True, width='stretch')
                st.info(f'Kỳ Power 6/55 gần nhất sắp mở: {power655_next_draw_date():%d-%m-%Y}.')

            if active_section == 'Tham khảo & kiểm chứng':
                st.markdown('#### Dự đoán tham khảo Power 6/55')
                st.warning(
                    'Vietlott là trò chơi ngẫu nhiên. Tần suất dưới đây mô tả dữ liệu quá khứ, '
                    'không phải xác suất trúng thưởng. Phiên bản mới lấy mẫu đồng đều; bản lưu cũ giữ nguyên để đối chiếu.'
                )
                probability_table = power655_prize_probabilities().rename(
                    columns={
                        'prize': 'Hạng giải',
                        'winning_combinations': 'Số tổ hợp trúng',
                        'probability': 'Xác suất một vé',
                        'odds_one_in': 'Tỷ lệ 1 trên',
                    }
                )
                probability_table['Xác suất một vé'] = probability_table['Xác suất một vé'].map(
                    lambda value: f'{value:.8%}'
                )
                probability_table['Tỷ lệ 1 trên'] = probability_table['Tỷ lệ 1 trên'].map(format_odds)
                st.markdown('#### Xác suất lý thuyết cho một bộ 6 số')
                st.dataframe(probability_table, hide_index=True, width='stretch')
                st.caption(
                    'Mọi bộ 6 số hợp lệ có cùng xác suất Jackpot 1 là 1/28.989.675. '
                    'Chưa có bằng chứng mô hình vượt lựa chọn ngẫu nhiên trên dữ liệu ngoài mẫu. '
                )
                target_power_date = power655_next_draw_date()
                power_reference_draws = power655_draws[power655_draws['draw_date'].dt.date < target_power_date].copy()
                if power_reference_draws.empty:
                    st.info('Chưa có dữ liệu trước kỳ tham khảo Power 6/55.')
                    st.stop()
                draw_count = power_reference_draws['draw_id'].nunique()
                if draw_count <= 1:
                    recent_power_window = 1
                else:
                    recent_power_window = persistent_widget(
                        'slider',
                        'Số kỳ gần đây để thống kê tần suất Power 6/55',
                        key='power655_recent_window',
                        min_value=1,
                        max_value=min(100, draw_count),
                        default=min(30, draw_count),
                    )
                training_latest = pd.to_datetime(power_reference_draws['draw_date']).max().date()
                st.caption(f'Dữ liệu lịch sử: {draw_count} kỳ, mới nhất đến {training_latest:%d-%m-%Y}.')
                if draw_count < 12:
                    st.warning('Mẫu dưới 12 kỳ; thống kê tần suất có độ ổn định rất thấp.')
                ranked_power = rank_number_candidates(power_reference_draws, recent_draws=recent_power_window, top_k=12)
                if not ranked_power.empty:
                    display_ranked_power = ranked_power.drop(
                        columns=['gap_component', 'probability'], errors='ignore'
                    ).rename(
                        columns={
                            'number': 'Số',
                            'model_score': 'Tần suất làm trơn (%)',
                            'recent_frequency': 'Tần suất gần (%)',
                            'long_frequency': 'Tần suất dài hạn (%)',
                            'gap_draws': 'Số kỳ chưa xuất hiện',
                        }
                    )
                    display_ranked_power.attrs = {}
                    chart_col, table_col = st.columns([1.2, 1])
                    with chart_col:
                        st.bar_chart(
                            display_ranked_power.set_index('Số')[['Tần suất làm trơn (%)']],
                            color='#D97706',
                            x_label='Số',
                            y_label='Tần suất lịch sử (%)',
                            height=360,
                        )
                    with table_col:
                        st.dataframe(display_ranked_power, hide_index=True, width='stretch', height=360)
                    ticket_candidates = saved_prediction_candidates(prediction_history, 'power655', target_power_date)
                    st.markdown('#### Bộ số tham khảo cho kỳ kế tiếp')
                    if not ticket_candidates.empty:
                        st.caption(
                            f'Phiên bản đã lưu: {ticket_candidates.attrs.get("model_version", "chưa xác định")}.'
                        )
                    if ticket_candidates.empty:
                        st.info('Chưa có bộ số được lưu cho kỳ kế tiếp. Hãy kiểm tra dữ liệu và nhật ký.')
                    candidate_columns = st.columns(max(1, len(ticket_candidates)))
                    for column, candidate in zip(candidate_columns, ticket_candidates.itertuples(index=False)):
                        column.metric(
                            'Bộ số',
                            candidate.value,
                            'XS Jackpot 1 mỗi bộ: 0,00000345%',
                            delta_color='off',
                        )
                st.divider()
                if prediction_history_error:
                    st.warning(f'Chưa ghi được nhật ký dự đoán: {prediction_history_error}')
                render_prediction_performance(prediction_history, 'power655', database_latest=power655_last_date)
