from __future__ import annotations

import hashlib
from datetime import date
from math import comb

import numpy as np
import pandas as pd

from .analytics import frequency_statistics
from .config import NUMBER_MAX, NUMBER_MIN

MODEL_VERSION = 'power655-uniform-baseline-v2'
NUMBER_COUNT = NUMBER_MAX - NUMBER_MIN + 1
PRIOR_DRAWS = 100


def _history_before(draws: pd.DataFrame, target_date: date | None = None) -> pd.DataFrame:
    work = draws.copy()
    if not work.empty:
        work['draw_date'] = pd.to_datetime(work['draw_date'])
        if target_date is not None:
            work = work[work['draw_date'] < pd.Timestamp(target_date)].copy()
        work = work.sort_values(['draw_date', 'draw_id'])
    return work


def rank_number_candidates(
    draws: pd.DataFrame,
    recent_draws: int = 30,
    top_k: int = 12,
    target_date: date | None = None,
) -> pd.DataFrame:
    """Historical inclusion rates shrunk toward 6/55, not win confidence.

    The fixed prior has 100 draw-equivalents. It reduces small-sample extremes
    without claiming that frequency or a long absence predicts future draws.
    """
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    if not 1 <= top_k <= NUMBER_COUNT:
        raise ValueError('Số lượng tham khảo phải từ 1 đến 55')
    columns = [
        'number',
        'model_score',
        'recent_frequency',
        'long_frequency',
        'gap_component',
        'gap_draws',
        'probability',
    ]
    work = _history_before(draws, target_date)
    if work.empty:
        return pd.DataFrame(columns=columns)
    long_stats = frequency_statistics(work, include_bonus=False)
    recent_stats = frequency_statistics(work.tail(recent_draws), include_bonus=False)
    ranked = long_stats[['number', 'count', 'draw_rate', 'gap_draws']].copy()
    ranked['model_score'] = 100 * (ranked['count'] + PRIOR_DRAWS * 6 / NUMBER_COUNT) / (len(work) + PRIOR_DRAWS)
    ranked['recent_frequency'] = 100 * recent_stats['draw_rate'].to_numpy()
    ranked['long_frequency'] = 100 * ranked['draw_rate']
    ranked['gap_component'] = 0.0
    ranked['probability'] = 6 / NUMBER_COUNT
    ranked = ranked.sort_values(['model_score', 'number'], ascending=[False, True]).head(top_k)
    ranked = ranked[columns].reset_index(drop=True)
    ranked.attrs.update(
        model_version=MODEL_VERSION,
        training_cutoff=work['draw_date'].max(),
        score_kind='smoothed_historical_inclusion_percent',
        selection_method='descriptive_only',
        prior_draws=PRIOR_DRAWS,
        probability_basis='uniform_main_number_inclusion',
        disclaimer='Tần suất lịch sử có làm trơn, chưa chứng minh khả năng dự đoán. Mỗi số có xác suất lý thuyết 6/55.',
    )
    return ranked


def _uniform_ticket_numbers(target_date: date, candidate_count: int = 1) -> list[str]:
    seed = int.from_bytes(hashlib.sha256(f'{MODEL_VERSION}|{target_date.isoformat()}'.encode()).digest()[:8], 'big')
    rng = np.random.default_rng(seed)
    generated: list[str] = []
    seen: set[str] = set()
    while len(generated) < candidate_count:
        selected = sorted(rng.choice(np.arange(NUMBER_MIN, NUMBER_MAX + 1), size=6, replace=False))
        key = ' '.join(f'{number:02d}' for number in selected)
        if key not in seen:
            generated.append(key)
            seen.add(key)
    return generated


def generate_ticket_candidates(
    draws: pd.DataFrame,
    target_date: date,
    candidate_count: int = 5,
    recent_draws: int = 30,
) -> pd.DataFrame:
    """Uniform six-number references, without using the target or future draw.

    Every ticket has the same theoretical Jackpot 1 chance; no candidate gets
    an inflated score merely because it ranks first in a generated sample.
    """
    if not 1 <= candidate_count <= comb(NUMBER_COUNT, 6):
        raise ValueError('Số lượng bộ số tham khảo ngoài phạm vi hợp lệ')
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    work = _history_before(draws, target_date)
    if work.empty:
        return pd.DataFrame(columns=['numbers', 'model_score', 'probability'])
    generated = _uniform_ticket_numbers(target_date, candidate_count)
    frame = pd.DataFrame({'numbers': generated, 'model_score': 0.0, 'probability': 1 / comb(NUMBER_COUNT, 6)})
    frame.attrs.update(
        model_version=MODEL_VERSION,
        training_cutoff=work['draw_date'].max(),
        target_date=target_date,
        score_kind='no_verified_edge',
        selection_method='uniform_baseline',
        probability_basis='uniform_jackpot1_single_ticket',
        disclaimer='Mỗi bộ có cùng xác suất Jackpot 1: 1/28.989.675; chưa chứng minh lợi thế dự đoán.',
    )
    return frame
