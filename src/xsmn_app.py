from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth

from startup_sync import StartupSyncReport, sync_all_missing
from vietlott_power655.analytics import frequency_statistics as power655_frequency_statistics
from vietlott_power655.analytics import prize_probabilities as power655_prize_probabilities
from vietlott_power655.analytics import top_frequency_table as power655_top_frequency_table
from vietlott_power655.calendar import next_draw_date as power655_next_draw_date
from vietlott_power655.config import DRAW_WEEKDAYS as POWER655_DRAW_WEEKDAYS
from vietlott_power655.ingestion import ingest_all as ingest_power655_all
from vietlott_power655.ingestion import ingest_latest as ingest_power655_latest
from vietlott_power655.prediction import generate_ticket_candidates, rank_number_candidates
from vietlott_power655.prize_checker import check_ticket as check_power655_ticket
from vietlott_power655.repository import SQLiteRepository as Power655Repository
from vietlott_power655.repository import prize_table as power655_prize_table
from vietlott_power655.scraper import VietlottPower655Client
from xsmn.analytics import frequency_matrix, frequency_statistics, top_frequency_table
from xsmn.calendar import latest_available_date, next_draw_date, next_regional_draw_date, stations_for_date
from xsmn.config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS, STATIONS, WEEKDAY_LABELS, WEEKLY_SCHEDULE
from xsmn.ingestion import ingest_range
from xsmn.prediction import generate_special_number_candidates, rank_suffix_candidates
from xsmn.prize_checker import check_ticket
from xsmn.repository import SQLiteRepository
from xsmn.scraper import XosoComClient

ROOT = Path(__file__).resolve().parents[1]


st.set_page_config(
    page_title='XSMN Analytics',
    page_icon='🎟️',
    layout='wide',
    initial_sidebar_state='expanded',
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 3rem;}
      [data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e2e8f0; padding: 1rem; border-radius: .75rem;}
      .ticket-number {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 1.35rem; letter-spacing: .12em;}
      .muted-card {background:#f8fafc; border-left:4px solid #2563eb; padding:.8rem 1rem; border-radius:.4rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

DATABASE_PATH = Path(os.environ.get('XSMN_DATABASE_PATH', ROOT / 'data' / 'xsmn.sqlite3'))
POWER655_DATABASE_PATH = Path(
    os.environ.get('VIETLOTT_POWER655_DATABASE_PATH', ROOT / 'data' / 'vietlott_power655.sqlite3')
)
AUTO_SYNC_ENABLED = os.environ.get('LOTTERY_AUTO_SYNC_ENABLED', '1').strip().lower() not in {'0', 'false', 'no'}
AUTO_SYNC_TTL_SECONDS = max(int(os.environ.get('LOTTERY_AUTO_SYNC_TTL_SECONDS', '900')), 60)
XSMN_BOOTSTRAP_DAYS = max(int(os.environ.get('XSMN_AUTO_SYNC_BOOTSTRAP_DAYS', '30')), 1)
AUTH_DISABLED = os.environ.get('LOTTERY_AUTH_DISABLED', '0').strip().lower() in {'1', 'true', 'yes'}


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


def format_vnd(value: int) -> str:
    return f'{value:,.0f} ₫'.replace(',', '.')


def format_percent(value: float) -> str:
    return f'{100 * value:.2f}%'


def format_optional_vnd(value: int | None) -> str:
    if value is None:
        return 'Theo jackpot kỳ quay'
    return format_vnd(value)


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
    metric_columns[0].metric('Số kỳ đài', f'{draw_count:,}'.replace(',', '.'))
    metric_columns[1].metric('Kết quả trong mẫu', f'{scoped_observations:,}'.replace(',', '.'))
    metric_columns[2].metric(
        f'Top đuôi {suffix_digits} số',
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

st.title('Xổ số miền Nam — thống kê & dò vé')
st.caption('Dữ liệu nhiều đài theo ngày · giữ nguyên số 0 đầu · múi giờ Asia/Ho_Chi_Minh')

with st.sidebar:
    st.header('Dữ liệu')
    if startup_sync_report is not None:
        xsmn_sync = startup_sync_report.xsmn
        power_sync = startup_sync_report.power655
        if not xsmn_sync.failed_dates and not power_sync.failed_message:
            st.success(
                f'Tự động đồng bộ: XSMN +{xsmn_sync.stored_draws} kỳ đài, Power 6/55 +{power_sync.stored_draws} kỳ.'
            )
        else:
            st.warning(
                f'Đồng bộ chưa hoàn tất: {len(xsmn_sync.failed_dates)} ngày XSMN lỗi; '
                f'Power 6/55: {power_sync.failed_message or "không lỗi"}.'
            )
    update_days = st.number_input('Số ngày cần cập nhật', min_value=1, max_value=3650, value=30, step=1)
    if st.button('Cập nhật từ nguồn công khai', type='primary', width='stretch'):
        end_date = latest_available_date()
        start_date = end_date - timedelta(days=int(update_days) - 1)
        progress = st.progress(0, text='Đang chuẩn bị...')

        def update_progress(index: int, total: int, selected_date: date) -> None:
            progress.progress(index / total, text=f'Đang tải {selected_date:%d-%m-%Y} ({index}/{total})')

        with st.spinner('Đang đọc và kiểm tra cơ cấu giải...'):
            report = ingest_range(repository, XosoComClient(), start_date, end_date, update_progress)
        load_results.clear()
        if report.stored_draws:
            st.success(f'Đã lưu {report.stored_draws} kỳ đài.')
        if report.failed_dates:
            with st.expander(f'{len(report.failed_dates)} ngày chưa tải được'):
                for failed_date, error in report.failed_dates:
                    st.write(f'- {failed_date:%d-%m-%Y}: {error}')
    st.caption('Nguồn mặc định: xoso.com.vn (bản AMP). Không cần API key.')

results = load_results(str(DATABASE_PATH), database_modified_ns(DATABASE_PATH))
power655_draws = load_power655_draws(str(POWER655_DATABASE_PATH), database_modified_ns(POWER655_DATABASE_PATH))
first_date, last_date = repository.date_bounds()
power655_first_date, power655_last_date = power655_repository.date_bounds()

if results.empty:
    st.info(
        'Chưa có dữ liệu XSMN. Chọn số ngày ở thanh bên và bấm **Cập nhật từ nguồn công khai**, '
        'hoặc chạy `uv run src/fetch_xsmn.py --days 365`.'
    )

tabs = st.tabs(['Tổng quan', 'Kết quả theo kỳ', 'Dò vé', 'Lịch mở thưởng', 'Dự đoán tham khảo', 'Vietlott Power 6/55'])

with tabs[0]:
    upcoming_date = next_regional_draw_date()
    upcoming_names = ', '.join(STATIONS[code].name for code in stations_for_date(upcoming_date))
    st.markdown(
        f'<div class="muted-card"><strong>Kỳ mở thưởng tiếp theo:</strong> '
        f'{upcoming_date:%d-%m-%Y} — {upcoming_names}</div>',
        unsafe_allow_html=True,
    )
    st.subheader('Dự đoán tự động cho kỳ quay tiếp theo')
    xsmn_prediction_col, power_prediction_col = st.columns(2)
    with xsmn_prediction_col:
        st.markdown('#### Xổ số kiến thiết miền Nam')
        upcoming_rows: list[dict[str, object]] = []
        for station_code in stations_for_date(upcoming_date) if not results.empty else ():
            station_history = results[
                (results['station_code'] == station_code) & (results['draw_date'].dt.date < upcoming_date)
            ].copy()
            station_draws = station_history[['draw_date', 'station_code']].drop_duplicates().shape[0]
            if station_draws == 0:
                continue
            candidates = generate_special_number_candidates(
                station_history,
                station_code,
                upcoming_date,
                candidate_count=3,
                recent_draws=min(30, station_draws),
            )
            for candidate in candidates.itertuples(index=False):
                upcoming_rows.append(
                    {
                        'Đài': STATIONS[station_code].name,
                        'Dãy 6 số': candidate.number,
                        'Điểm mô hình': candidate.model_score,
                        'XS giải đặc biệt': '0,000100%',
                    }
                )
        if upcoming_rows:
            st.dataframe(pd.DataFrame(upcoming_rows), hide_index=True, width='stretch')
        else:
            st.info('Chưa đủ lịch sử XSMN để sinh dự đoán tự động.')
    with power_prediction_col:
        st.markdown('#### Vietlott Power 6/55')
        if power655_draws.empty:
            st.info('Chưa đủ lịch sử Power 6/55 để sinh dự đoán tự động.')
        else:
            power_target = power655_next_draw_date()
            power_candidates = generate_ticket_candidates(
                power655_draws,
                power_target,
                candidate_count=5,
                recent_draws=min(30, power655_draws['draw_id'].nunique()),
            ).rename(columns={'numbers': 'Bộ 6 số', 'model_score': 'Điểm mô hình'})
            power_candidates['XS Jackpot 1'] = '0,00000345%'
            st.caption(f'Kỳ dự kiến: {power_target:%d-%m-%Y}')
            st.dataframe(power_candidates, hide_index=True, width='stretch')
    st.caption(
        'Tỷ lệ hiển thị là xác suất lý thuyết của một bộ số cụ thể. Điểm mô hình là xếp hạng từ dữ liệu lịch sử, '
        'không phải xác suất trúng đã được hiệu chỉnh.'
    )
    if not results.empty and first_date and last_date:
        with st.sidebar:
            st.divider()
            st.header('Bộ lọc thống kê')
            default_start = max(first_date, last_date - timedelta(days=365))
            selected_range = st.date_input(
                'Khoảng thời gian',
                value=(default_start, last_date),
                min_value=first_date,
                max_value=last_date,
            )
            available_codes = sorted(results['station_code'].unique(), key=lambda code: STATIONS[code].name)
            station_mode = st.radio('Đài', ['Tất cả đài', 'Tùy chọn'], horizontal=True)
            if station_mode == 'Tất cả đài':
                selected_stations = available_codes
            else:
                selected_stations = st.multiselect(
                    'Chọn đài',
                    options=available_codes,
                    default=available_codes[:1],
                    format_func=lambda code: STATIONS[code].name,
                )
            prize_scope_label = st.radio('Phạm vi', ['Tất cả giải', 'Chỉ giải đặc biệt'])
            suffix_digits = st.radio('Độ dài đuôi số', [2, 3], horizontal=True)

        if isinstance(selected_range, (tuple, list)) and len(selected_range) == 2:
            selected_start, selected_end = selected_range
        elif isinstance(selected_range, (tuple, list)) and len(selected_range) == 1:
            selected_start = selected_end = selected_range[0]
        elif isinstance(selected_range, (tuple, list)):
            selected_start, selected_end = default_start, last_date
        else:
            selected_start = selected_end = selected_range
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

with tabs[1]:
    st.subheader('Danh sách đầy đủ các số của từng kỳ')
    if not results.empty:
        draw_options = (
            results[['draw_date', 'station_code', 'station_name']]
            .drop_duplicates()
            .sort_values(['draw_date', 'station_name'], ascending=[False, True])
        )
        dates = draw_options['draw_date'].dt.date.drop_duplicates().tolist()
        selected_date = st.selectbox('Ngày mở thưởng', dates, format_func=lambda value: f'{value:%d-%m-%Y}')
        date_draws = draw_options[draw_options['draw_date'].dt.date == selected_date]
        station_codes = date_draws['station_code'].tolist()
        station_code = st.selectbox('Đài', station_codes, format_func=lambda code: STATIONS[code].name)
        draw = repository.get_draw(selected_date, station_code)
        if draw:
            st.markdown(f'### {draw.station_name} — {draw.draw_date:%d-%m-%Y}')
            st.dataframe(draw_result_table(draw), hide_index=True, width='stretch')
            if draw.source_url:
                st.link_button('Mở trang nguồn', draw.source_url)

with tabs[2]:
    st.subheader('Dò vé số truyền thống miền Nam')
    st.caption('Nhập đủ 6 chữ số trên vé, chọn đúng ngày và đúng đài phát hành.')
    default_check_date = last_date or latest_available_date()
    check_date = st.date_input('Ngày trên vé', value=default_check_date, key='check_date')
    available_on_date = list(stations_for_date(check_date))
    check_station = st.selectbox(
        'Đài phát hành',
        available_on_date,
        format_func=lambda code: STATIONS[code].name,
        key='check_station',
    )
    with st.form('ticket_check_form'):
        ticket_number = st.text_input('Số vé', max_chars=6, placeholder='Ví dụ: 012345')
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

with tabs[3]:
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

with tabs[4]:
    st.subheader('Dự đoán tham khảo cho kỳ kế tiếp')
    st.warning(
        'Xổ số là quá trình ngẫu nhiên. Tần suất lịch sử không làm một số chắc chắn dễ trúng hơn. '
        'Các kết quả dưới đây chỉ là xếp hạng thống kê phục vụ tham khảo và thử nghiệm.'
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
        prediction_station = st.selectbox(
            'Đài cần tham khảo',
            station_choices,
            format_func=lambda code: STATIONS[code].name,
            key='prediction_station',
        )
        target_date = next_draw_date(prediction_station)
        station_results = results[
            (results['station_code'] == prediction_station) & (results['draw_date'].dt.date < target_date)
        ].copy()
        station_draw_count = station_results[['draw_date', 'station_code']].drop_duplicates().shape[0]
        training_latest = pd.to_datetime(station_results['draw_date']).max().date()
        if station_draw_count <= 1:
            recent_window = 1
            st.caption('Mới có 1 kỳ dữ liệu; mô hình đang ở trạng thái khởi đầu lạnh.')
        else:
            recent_window = st.slider(
                'Số kỳ gần đây dùng cho thành phần xu hướng',
                min_value=1,
                max_value=min(100, station_draw_count),
                value=min(30, station_draw_count),
            )
        st.write(f'Kỳ kế tiếp dự kiến: **{target_date:%d-%m-%Y} — {STATIONS[prediction_station].name}**')
        st.caption(f'Dữ liệu huấn luyện: {station_draw_count} kỳ, mới nhất đến {training_latest:%d-%m-%Y}.')
        if station_draw_count < 12:
            st.warning('Mẫu huấn luyện dưới 12 kỳ; điểm xếp hạng có độ ổn định rất thấp.')
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
            display_ranked = ranked.rename(
                columns={
                    'number': 'Đuôi 2 số',
                    'model_score': 'Điểm xếp hạng',
                    'recent_frequency': 'Điểm tần suất gần',
                    'long_frequency': 'Điểm tần suất dài hạn',
                    'gap_component': 'Điểm độ trễ',
                }
            )
            display_ranked.attrs = {}
            chart_col, table_col = st.columns([1.2, 1])
            with chart_col:
                st.markdown('#### Dàn 2 số theo điểm mô hình')
                st.bar_chart(
                    display_ranked.set_index('Đuôi 2 số')[['Điểm xếp hạng']],
                    color='#D97706',
                    x_label='Đuôi 2 số',
                    y_label='Điểm (0–100)',
                    height=360,
                )
            with table_col:
                st.markdown('#### Thành phần điểm')
                st.dataframe(display_ranked, hide_index=True, width='stretch', height=360)

            full_candidates = generate_special_number_candidates(
                station_results,
                prediction_station,
                target_date,
                candidate_count=5,
                recent_draws=recent_window,
            )
            st.markdown('#### Dãy 6 số tham khảo cho giải đặc biệt')
            if full_candidates.empty:
                st.info('Chưa có đủ kết quả giải đặc biệt trước ngày dự đoán để sinh dãy 6 số.')
            else:
                candidate_columns = st.columns(len(full_candidates))
                for column, candidate in zip(candidate_columns, full_candidates.itertuples(index=False), strict=True):
                    column.metric(
                        'Dãy tham khảo',
                        candidate.number,
                        f'Điểm {candidate.model_score:.1f}/100 · XS giải ĐB 0,000100%',
                        delta_color='off',
                    )
            st.caption(
                'Điểm = 50% thành phần tần suất gần + 30% tần suất dài hạn + 20% độ trễ; '
                'mỗi thành phần được chuẩn hóa min–max về thang 0–100. '
                'Dãy 6 số dùng tần suất chữ số theo từng vị trí và làm trơn Laplace; không phải xác suất trúng.'
            )
with tabs[5]:
    st.subheader('Vietlott Power 6/55')
    st.caption('Sản phẩm 6/55 của Vietlott là Power 6/55: quay 6 số chính từ 01-55 và 1 số đặc biệt.')
    update_col, info_col = st.columns([1, 2])
    with update_col:
        power655_limit = st.number_input('Số kỳ Vietlott cần cập nhật', min_value=1, max_value=50, value=8, step=1)
        if st.button('Cập nhật Power 6/55', type='primary', width='stretch'):
            with st.spinner('Đang tải kết quả Power 6/55 từ Vietlott...'):
                report = ingest_power655_latest(
                    power655_repository,
                    VietlottPower655Client(),
                    limit=int(power655_limit),
                    include_details=True,
                )
            load_power655_draws.clear()
            if report.failed_message:
                st.error(report.failed_message)
            else:
                st.success(f'Đã lưu {report.stored_draws} kỳ Power 6/55.')
        if st.button('Tải toàn bộ lịch sử Power 6/55', width='stretch'):
            with st.spinner('Đang tải toàn bộ lịch sử từ kỳ đầu tiên; quá trình này có thể mất vài phút...'):
                report = ingest_power655_all(power655_repository, VietlottPower655Client())
            load_power655_draws.clear()
            if report.failed_message:
                st.error(report.failed_message)
            else:
                st.success(
                    f'Đã lưu {report.stored_draws} kỳ, từ #{report.first_draw_id} đến #{report.last_draw_id}; '
                    f'thiếu {len(report.missing_draw_ids)} ID kỳ.'
                )
                st.rerun()
    with info_col:
        st.markdown(
            f'<div class="muted-card"><strong>Kỳ Power 6/55 tiếp theo:</strong> '
            f'{power655_next_draw_date():%d-%m-%Y} · Thứ 3, Thứ 5, Thứ 7 · 18:00-18:30</div>',
            unsafe_allow_html=True,
        )

    if power655_draws.empty:
        st.info(
            'Chưa có dữ liệu Power 6/55. Bấm **Cập nhật Power 6/55** hoặc chạy '
            '`uv run src/fetch_vietlott_power655.py --limit 8`.'
        )
    else:
        power_tabs = st.tabs(['Thống kê', 'Kết quả theo kỳ', 'Dò vé', 'Lịch mở thưởng', 'Dự đoán tham khảo'])

        with power_tabs[0]:
            default_power_start = power655_first_date
            selected_power_range = st.date_input(
                'Khoảng thời gian Power 6/55',
                value=(default_power_start, power655_last_date),
                min_value=power655_first_date,
                max_value=power655_last_date,
                key='power655_range',
            )
            include_bonus = st.checkbox('Tính cả số đặc biệt vào thống kê tần suất', value=False)
            if isinstance(selected_power_range, (tuple, list)) and len(selected_power_range) == 2:
                power_start, power_end = selected_power_range
            elif isinstance(selected_power_range, (tuple, list)) and len(selected_power_range) == 1:
                power_start = power_end = selected_power_range[0]
            else:
                power_start = power_end = selected_power_range
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
                metric_cols[1].metric('Số quan sát', f'{power_stats.attrs["total_observations"]:,}'.replace(',', '.'))
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

        with power_tabs[1]:
            st.markdown('#### Danh sách đầy đủ các kỳ Power 6/55')
            draw_options = power655_draws.sort_values(['draw_date', 'draw_id'], ascending=[False, False])
            selected_power_draw = st.selectbox(
                'Kỳ quay',
                draw_options['draw_id'].tolist(),
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

        with power_tabs[2]:
            st.markdown('#### Dò vé Power 6/55')
            st.caption('Nhập 6 số trên vé, cách nhau bằng dấu cách, dấu phẩy hoặc gạch ngang.')
            draw_ids = power655_draws.sort_values(['draw_date', 'draw_id'], ascending=[False, False])[
                'draw_id'
            ].tolist()
            check_power_draw_id = st.selectbox('Kỳ cần dò', draw_ids, key='power655_check_draw')
            with st.form('power655_ticket_form'):
                power_ticket = st.text_input('Bộ số dự thưởng', placeholder='Ví dụ: 09 17 20 33 41 42')
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

        with power_tabs[3]:
            st.markdown('#### Lịch mở thưởng Power 6/55')
            schedule_rows = [
                {'Ngày': WEEKDAY_LABELS[weekday], 'Sản phẩm': 'Power 6/55', 'Giờ dự kiến': '18:00-18:30'}
                for weekday in POWER655_DRAW_WEEKDAYS
            ]
            st.dataframe(pd.DataFrame(schedule_rows), hide_index=True, width='stretch')
            st.info(f'Kỳ Power 6/55 gần nhất sắp mở: {power655_next_draw_date():%d-%m-%Y}.')

        with power_tabs[4]:
            st.markdown('#### Dự đoán tham khảo Power 6/55')
            st.warning(
                'Vietlott là trò chơi ngẫu nhiên. Điểm dưới đây chỉ là xếp hạng thống kê theo lịch sử, '
                'không phải xác suất trúng thưởng.'
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
            probability_table['Tỷ lệ 1 trên'] = probability_table['Tỷ lệ 1 trên'].map(
                lambda value: f'{value:,.1f}'.replace(',', '.')
            )
            st.markdown('#### Xác suất lý thuyết cho một bộ 6 số')
            st.dataframe(probability_table, hide_index=True, width='stretch')
            st.caption(
                'Mọi bộ 6 số hợp lệ có cùng xác suất Jackpot 1 là 1/28.989.675. '
                'Thống kê lịch sử chỉ dùng để tạo điểm xếp hạng tham khảo, không làm tăng xác suất toán học của bộ số.'
            )
            draw_count = power655_draws['draw_id'].nunique()
            if draw_count <= 1:
                recent_power_window = 1
            else:
                recent_power_window = st.slider(
                    'Số kỳ gần đây dùng cho thành phần xu hướng Power 6/55',
                    min_value=1,
                    max_value=min(100, draw_count),
                    value=min(30, draw_count),
                )
            target_power_date = power655_next_draw_date()
            training_latest = pd.to_datetime(power655_draws['draw_date']).max().date()
            st.caption(f'Dữ liệu huấn luyện: {draw_count} kỳ, mới nhất đến {training_latest:%d-%m-%Y}.')
            if draw_count < 12:
                st.warning('Mẫu huấn luyện dưới 12 kỳ; điểm xếp hạng có độ ổn định rất thấp.')
            ranked_power = rank_number_candidates(power655_draws, recent_draws=recent_power_window, top_k=12)
            if not ranked_power.empty:
                display_ranked_power = ranked_power.rename(
                    columns={
                        'number': 'Số',
                        'model_score': 'Điểm xếp hạng',
                        'recent_frequency': 'Điểm tần suất gần',
                        'long_frequency': 'Điểm tần suất dài hạn',
                        'gap_component': 'Điểm độ trễ',
                    }
                )
                chart_col, table_col = st.columns([1.2, 1])
                with chart_col:
                    st.bar_chart(
                        display_ranked_power.set_index('Số')[['Điểm xếp hạng']],
                        color='#D97706',
                        x_label='Số',
                        y_label='Điểm (0-100)',
                        height=360,
                    )
                with table_col:
                    st.dataframe(display_ranked_power, hide_index=True, width='stretch', height=360)
                ticket_candidates = generate_ticket_candidates(
                    power655_draws,
                    target_power_date,
                    candidate_count=5,
                    recent_draws=recent_power_window,
                )
                st.markdown('#### Bộ số tham khảo cho kỳ kế tiếp')
                candidate_columns = st.columns(len(ticket_candidates))
                for column, candidate in zip(candidate_columns, ticket_candidates.itertuples(index=False), strict=True):
                    column.metric(
                        'Bộ số',
                        candidate.numbers,
                        f'Điểm {candidate.model_score:.1f}/100 · XS Jackpot 1: 0,00000345%',
                        delta_color='off',
                    )
