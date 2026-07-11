from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from .analytics import frequency_statistics, select_scope

MODEL_VERSION = 'transparent-blend-v1'


def _minmax(series: pd.Series) -> pd.Series:
    numeric = series.astype(float)
    minimum = numeric.min()
    maximum = numeric.max()
    if pd.isna(minimum) or maximum == minimum:
        return pd.Series(np.zeros(len(numeric)), index=series.index)
    return (numeric - minimum) / (maximum - minimum)


def _recent_draw_slice(results: pd.DataFrame, draw_limit: int) -> pd.DataFrame:
    if results.empty:
        return results.copy()
    work = results.copy()
    work['draw_date'] = pd.to_datetime(work['draw_date'])
    draw_keys = (
        work[['draw_date', 'station_code']]
        .drop_duplicates()
        .sort_values(['draw_date', 'station_code'])
        .tail(draw_limit)
    )
    return work.merge(draw_keys, on=['draw_date', 'station_code'], how='inner', validate='many_to_one')


def rank_suffix_candidates(
    results: pd.DataFrame,
    suffix_digits: int = 2,
    prize_scope: str = 'all',
    recent_draws: int = 30,
    top_k: int = 10,
) -> pd.DataFrame:
    """Rank suffixes by a transparent heuristic, not a calibrated probability."""

    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')
    scoped = select_scope(results, prize_scope)
    if scoped.empty:
        return pd.DataFrame(columns=['number', 'model_score', 'recent_frequency', 'long_frequency', 'gap_component'])
    long_stats = frequency_statistics(scoped, suffix_digits=suffix_digits, prize_scope='all')
    recent = _recent_draw_slice(scoped, recent_draws)
    recent_stats = frequency_statistics(recent, suffix_digits=suffix_digits, prize_scope='all')

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
    ranked['model_score'] = ranked['model_score'].round(1)
    for column in ['recent_frequency', 'long_frequency', 'gap_component']:
        ranked[column] = (100 * ranked[column]).round(1)
    ranked.attrs.update(
        model_version=MODEL_VERSION,
        training_cutoff=pd.to_datetime(scoped['draw_date']).max(),
        recent_draws=recent_draws,
        disclaimer='Điểm xếp hạng không phải xác suất trúng thưởng.',
    )
    return ranked[['number', 'model_score', 'recent_frequency', 'long_frequency', 'gap_component']].reset_index(
        drop=True
    )


def generate_special_number_candidates(
    results: pd.DataFrame,
    station_code: str,
    target_date: date,
    candidate_count: int = 5,
    recent_draws: int = 40,
) -> pd.DataFrame:
    """Generate reproducible six-digit candidates from positional frequencies.

    Laplace smoothing ensures every digit remains possible. The output is a
    deterministic entertainment-oriented ranking and is not a win probability.
    """

    if candidate_count < 1:
        raise ValueError('Số lượng dãy dự đoán phải lớn hơn 0')
    if recent_draws < 1:
        raise ValueError('Số kỳ gần đây phải lớn hơn 0')

    special = results[(results['prize_code'] == 'db') & (results['station_code'] == station_code)].copy()
    if special.empty:
        return pd.DataFrame(columns=['number', 'model_score'])
    special['draw_date'] = pd.to_datetime(special['draw_date'])
    special = special[special['draw_date'] < pd.Timestamp(target_date)].copy()
    if special.empty:
        return pd.DataFrame(columns=['number', 'model_score'])
    special = special.sort_values(['draw_date', 'station_code'])
    recent = special.tail(recent_draws)

    position_probabilities: list[np.ndarray] = []
    for position in range(6):
        counts = np.ones(10, dtype=float)
        for value in special['number'].astype(str):
            counts[int(value[position])] += 1
        for value in recent['number'].astype(str):
            counts[int(value[position])] += 1.5
        position_probabilities.append(counts / counts.sum())

    seed_material = f'{MODEL_VERSION}|{station_code}|{target_date.isoformat()}'.encode()
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], 'big')
    rng = np.random.default_rng(seed)
    generated: dict[str, float] = {}
    attempts = 0
    while len(generated) < candidate_count and attempts < candidate_count * 100:
        attempts += 1
        digits: list[str] = []
        likelihood = 1.0
        for probabilities in position_probabilities:
            digit = int(rng.choice(10, p=probabilities))
            digits.append(str(digit))
            likelihood *= float(probabilities[digit])
        generated[''.join(digits)] = likelihood

    frame = pd.DataFrame([{'number': number, 'raw_score': score} for number, score in generated.items()]).sort_values(
        ['raw_score', 'number'], ascending=[False, True]
    )
    if frame.empty:
        return pd.DataFrame(columns=['number', 'model_score'])
    maximum = frame['raw_score'].max()
    frame['model_score'] = (100 * frame['raw_score'] / maximum).round(1)
    frame.attrs.update(
        model_version=MODEL_VERSION,
        training_cutoff=special['draw_date'].max(),
        target_date=target_date,
        disclaimer='Dãy số tham khảo, không phải xác suất trúng thưởng.',
    )
    return frame[['number', 'model_score']].reset_index(drop=True)
