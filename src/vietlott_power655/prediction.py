from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from .analytics import frequency_statistics
from .config import NUMBER_MAX, NUMBER_MIN

MODEL_VERSION = 'power655-transparent-blend-v1'


def _minmax(series: pd.Series) -> pd.Series:
    numeric = series.astype(float)
    minimum = numeric.min()
    maximum = numeric.max()
    if pd.isna(minimum) or maximum == minimum:
        return pd.Series(np.zeros(len(numeric)), index=series.index)
    return (numeric - minimum) / (maximum - minimum)


def rank_number_candidates(draws: pd.DataFrame, recent_draws: int = 30, top_k: int = 12) -> pd.DataFrame:
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    if draws.empty:
        return pd.DataFrame(columns=['number', 'model_score', 'recent_frequency', 'long_frequency', 'gap_component'])
    work = draws.copy()
    work['draw_date'] = pd.to_datetime(work['draw_date'])
    work = work.sort_values(['draw_date', 'draw_id'])
    long_stats = frequency_statistics(work, include_bonus=False)
    recent_stats = frequency_statistics(work.tail(recent_draws), include_bonus=False)
    ranked = long_stats[['number', 'observation_rate', 'gap_draws']].rename(columns={'observation_rate': 'long_rate'})
    ranked = ranked.merge(
        recent_stats[['number', 'observation_rate']].rename(columns={'observation_rate': 'recent_rate'}),
        on='number',
        how='left',
        validate='one_to_one',
    )
    ranked['recent_frequency'] = _minmax(ranked['recent_rate'])
    ranked['long_frequency'] = _minmax(ranked['long_rate'])
    ranked['gap_component'] = _minmax(ranked['gap_draws'])
    ranked['model_score'] = 100 * (
        0.50 * ranked['recent_frequency'] + 0.30 * ranked['long_frequency'] + 0.20 * ranked['gap_component']
    )
    ranked = ranked.sort_values(['model_score', 'number'], ascending=[False, True]).head(top_k).copy()
    for column in ['model_score', 'recent_frequency', 'long_frequency', 'gap_component']:
        ranked[column] = (ranked[column] if column == 'model_score' else 100 * ranked[column]).round(1)
    return ranked[['number', 'model_score', 'recent_frequency', 'long_frequency', 'gap_component']].reset_index(
        drop=True
    )


def generate_ticket_candidates(
    draws: pd.DataFrame,
    target_date: date,
    candidate_count: int = 5,
    recent_draws: int = 30,
) -> pd.DataFrame:
    if candidate_count < 1:
        raise ValueError('Số lượng bộ số tham khảo phải lớn hơn 0')
    if draws.empty:
        return pd.DataFrame(columns=['numbers', 'model_score'])
    ranked = rank_number_candidates(draws, recent_draws=recent_draws, top_k=NUMBER_MAX - NUMBER_MIN + 1)
    weights = ranked.set_index('number')['model_score'].clip(lower=1.0)
    universe = [f'{number:02d}' for number in range(NUMBER_MIN, NUMBER_MAX + 1)]
    probability = np.array([float(weights.get(number, 1.0)) for number in universe], dtype=float)
    probability = probability / probability.sum()
    seed = int.from_bytes(hashlib.sha256(f'{MODEL_VERSION}|{target_date.isoformat()}'.encode()).digest()[:8], 'big')
    rng = np.random.default_rng(seed)
    generated: dict[str, float] = {}
    attempts = 0
    while len(generated) < candidate_count and attempts < candidate_count * 100:
        attempts += 1
        selected = sorted(rng.choice(universe, size=6, replace=False, p=probability), key=int)
        key = ' '.join(selected)
        generated[key] = float(sum(weights.get(number, 1.0) for number in selected))
    frame = pd.DataFrame([{'numbers': numbers, 'raw_score': score} for numbers, score in generated.items()])
    if frame.empty:
        return pd.DataFrame(columns=['numbers', 'model_score'])
    frame = frame.sort_values(['raw_score', 'numbers'], ascending=[False, True])
    frame['model_score'] = (100 * frame['raw_score'] / frame['raw_score'].max()).round(1)
    return frame[['numbers', 'model_score']].reset_index(drop=True)
