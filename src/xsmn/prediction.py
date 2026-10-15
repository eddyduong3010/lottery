from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from .analytics import frequency_statistics, select_scope
from .config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS

MODEL_VERSION = 'xsmn-uniform-baseline-v2'
FULL_DRAW_MODEL_VERSION = 'full-prize-uniform-baseline-v2'


def _history_before(results: pd.DataFrame, target_date: date | None = None) -> pd.DataFrame:
    work = results.copy()
    if not work.empty:
        work['draw_date'] = pd.to_datetime(work['draw_date'])
        if target_date is not None:
            work = work[work['draw_date'] < pd.Timestamp(target_date)].copy()
    return work


def _recent_draw_slice(results: pd.DataFrame, draw_limit: int) -> pd.DataFrame:
    if results.empty:
        return results.copy()
    work = _history_before(results)
    # Include every station on the boundary date: a station's lexical code must
    # not determine which same-day results enter the reference window.
    draw_keys = work[['draw_date', 'station_code']].drop_duplicates().sort_values('draw_date').tail(draw_limit)
    return work[work['draw_date'] >= draw_keys['draw_date'].min()].copy()


def rank_suffix_candidates(
    results: pd.DataFrame,
    suffix_digits: int = 2,
    prize_scope: str = 'all',
    recent_draws: int = 30,
    top_k: int = 10,
    target_date: date | None = None,
) -> pd.DataFrame:
    """Describe smoothed historical frequency; do not infer a predictive edge.

    A symmetric Dirichlet prior contributes one observation per suffix. Gap is
    displayed only as a diagnostic: a long absence does not make a fair draw
    more likely. ``probability`` is the uniform single-result baseline, whereas
    ``model_score`` is historical frequency in percent, not win confidence.
    """
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    if suffix_digits not in {2, 3}:
        raise ValueError('Ứng dụng hiện hỗ trợ thống kê đuôi 2 hoặc 3 số')
    if not 1 <= top_k <= 10**suffix_digits:
        raise ValueError('Số lượng tham khảo phải nằm trong phạm vi các đuôi số')
    scoped = select_scope(_history_before(results, target_date), prize_scope)
    columns = [
        'number',
        'model_score',
        'recent_frequency',
        'long_frequency',
        'gap_component',
        'gap_draws',
        'probability',
    ]
    if scoped.empty:
        return pd.DataFrame(columns=columns)
    long_stats = frequency_statistics(scoped, suffix_digits=suffix_digits)
    recent_stats = frequency_statistics(_recent_draw_slice(scoped, recent_draws), suffix_digits=suffix_digits)
    universe_size = 10**suffix_digits
    ranked = long_stats[['number', 'count', 'observation_rate', 'gap_draws']].copy()
    ranked['model_score'] = 100 * (ranked['count'] + 1) / (long_stats.attrs['total_observations'] + universe_size)
    ranked['recent_frequency'] = 100 * recent_stats['observation_rate'].to_numpy()
    ranked['long_frequency'] = 100 * ranked['observation_rate']
    ranked['gap_component'] = 0.0
    ranked['probability'] = 1 / universe_size
    ranked = ranked.sort_values(['model_score', 'number'], ascending=[False, True]).head(top_k)
    ranked = ranked[columns].reset_index(drop=True)
    ranked.attrs.update(
        model_version=MODEL_VERSION,
        training_cutoff=pd.to_datetime(scoped['draw_date']).max(),
        recent_draws=recent_draws,
        score_kind='smoothed_historical_frequency_percent',
        selection_method='descriptive_only',
        probability_basis='uniform_single_result',
        disclaimer='Tần suất lịch sử có làm trơn, chưa chứng minh khả năng dự đoán. Xác suất lý thuyết giả định quay đều.',
    )
    return ranked


def _uniform_numbers(digits: int, count: int, seed_material: str) -> list[str]:
    seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], 'big')
    generator = np.random.default_rng(seed)
    # Rejection sampling preserves the first candidate when callers request
    # additional references; every distinct number has equal selection chance.
    values: list[str] = []
    selected: set[str] = set()
    while len(values) < count:
        number = f'{int(generator.integers(10**digits)):0{digits}d}'
        if number not in selected:
            values.append(number)
            selected.add(number)
    return values


def generate_special_number_candidates(
    results: pd.DataFrame,
    station_code: str,
    target_date: date,
    candidate_count: int = 5,
    recent_draws: int = 40,
) -> pd.DataFrame:
    """Reproducible uniform references until an out-of-sample edge is proven."""
    if not 1 <= candidate_count <= 10**6:
        raise ValueError('Số lượng dãy dự đoán phải từ 1 đến 1000000')
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    special = _history_before(results, target_date)
    special = special[(special['prize_code'] == 'db') & (special['station_code'] == station_code)].copy()
    special = special[special['number'].astype(str).str.fullmatch(r'[0-9]{6}')].copy()
    if special.empty:
        return pd.DataFrame(columns=['number', 'model_score', 'probability'])
    frame = pd.DataFrame(
        {
            'number': _uniform_numbers(6, candidate_count, f'{MODEL_VERSION}|{station_code}|{target_date.isoformat()}'),
            'model_score': 0.0,
            'probability': 1 / 10**6,
        }
    )
    frame.attrs.update(
        model_version=MODEL_VERSION,
        training_cutoff=special['draw_date'].max(),
        target_date=target_date,
        score_kind='no_verified_edge',
        selection_method='uniform_baseline',
        probability_basis='uniform_exact_special_prize',
        disclaimer='Các số tham khảo có cùng xác suất lý thuyết 1/1.000.000; chưa chứng minh lợi thế dự đoán.',
    )
    return frame


def generate_full_draw_prediction(
    results: pd.DataFrame,
    station_code: str,
    target_date: date,
    recent_draws: int = 30,
) -> pd.DataFrame:
    """Create fixed uniform references for prospective, reproducible evaluation."""
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    station = _history_before(results, target_date)
    station = station[station['station_code'] == station_code].copy()
    columns = ['prize_code', 'ordinal', 'number', 'model_score', 'probability']
    if station.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for prize_code in PRIZE_DISPLAY_ORDER:
        spec = PRIZE_SPECS[prize_code]
        prize_history = station[station['prize_code'] == prize_code]
        if not prize_history['number'].astype(str).str.fullmatch(rf'[0-9]{{{spec.digits}}}').any():
            continue
        numbers = _uniform_numbers(
            spec.digits,
            spec.result_count,
            f'{FULL_DRAW_MODEL_VERSION}|{station_code}|{target_date.isoformat()}|{prize_code}',
        )
        for ordinal, number in enumerate(numbers, start=1):
            rows.append(
                {
                    'prize_code': prize_code,
                    'ordinal': ordinal,
                    'number': number,
                    'model_score': 0.0,
                    'probability': 1 / 10**spec.digits,
                }
            )
    frame = pd.DataFrame(rows, columns=columns)
    frame.attrs.update(
        model_version=FULL_DRAW_MODEL_VERSION,
        training_cutoff=station['draw_date'].max(),
        target_date=target_date,
        score_kind='no_verified_edge',
        selection_method='uniform_baseline',
        probability_basis='uniform_single_prize_slot',
        disclaimer='Bảng tham khảo cố định để kiểm nghiệm; chưa chứng minh lợi thế so với chọn ngẫu nhiên đều.',
    )
    return frame
