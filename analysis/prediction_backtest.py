from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Also support direct execution via python analysis/prediction_backtest.py.
SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vietlott_power655.prediction import PRIOR_DRAWS, _uniform_ticket_numbers
from xsmn.prediction import FULL_DRAW_MODEL_VERSION as XSMN_UNIFORM_VERSION
from xsmn.prediction import _uniform_numbers

POWER_MODEL_VERSION = 'power655-transparent-blend-v1'
XSMN_FULL_MODEL_VERSION = 'full-prize-positional-v1'
XSMN_PRIZE_SPECS = {
    'g8': (2, 1),
    'g7': (3, 1),
    'g6': (4, 3),
    'g5': (4, 1),
    'g4': (5, 7),
    'g3': (5, 2),
    'g2': (5, 1),
    'g1': (5, 1),
    'db': (6, 1),
}


def _minmax(values: np.ndarray) -> np.ndarray:
    numeric = np.asarray(values, dtype=float)
    span = numeric.max() - numeric.min()
    return np.zeros_like(numeric) if span == 0 else (numeric - numeric.min()) / span


def _bootstrap_mean_interval(values: list[float] | np.ndarray, seed: int = 20260722) -> list[float | None]:
    numeric = np.asarray(values, dtype=float)
    if len(numeric) == 0:
        return [None, None]
    generator = np.random.default_rng(seed)
    # Batch the bootstrap to avoid allocating thousands of full-history copies.
    means = np.concatenate(
        [generator.choice(numeric, size=(100, len(numeric)), replace=True).mean(axis=1) for _ in range(20)]
    )
    return [round(float(value), 6) for value in np.quantile(means, [0.025, 0.975])]


def _longest_matching_suffix(candidate: str, actual: str) -> int:
    for digits in range(min(len(candidate), len(actual)), 0, -1):
        if candidate[-digits:] == actual[-digits:]:
            return digits
    return 0


def _load_power_draws(database_path: Path) -> pd.DataFrame:
    with sqlite3.connect(f'{database_path.resolve().as_uri()}?mode=ro', uri=True) as connection:
        return pd.read_sql_query(
            'SELECT draw_id, draw_date, main_numbers, bonus_number FROM power655_draws ORDER BY draw_date, draw_id',
            connection,
        )


def _load_xsmn_results(database_path: Path) -> pd.DataFrame:
    with sqlite3.connect(f'{database_path.resolve().as_uri()}?mode=ro', uri=True) as connection:
        return pd.read_sql_query(
            'SELECT d.draw_date, d.station_code, p.prize_code, p.ordinal, p.number '
            'FROM draws d JOIN prize_results p ON p.draw_id = d.id '
            'ORDER BY d.station_code, d.draw_date, p.prize_code, p.ordinal',
            connection,
        )


def data_quality_profile(xsmn_database: Path, power_database: Path) -> dict[str, object]:
    with sqlite3.connect(f'{xsmn_database.resolve().as_uri()}?mode=ro', uri=True) as connection:
        xsmn = connection.execute(
            'SELECT COUNT(*), MIN(draw_date), MAX(draw_date), COUNT(DISTINCT station_code) FROM draws'
        ).fetchone()
        xsmn_prize_rows = int(connection.execute('SELECT COUNT(*) FROM prize_results').fetchone()[0])
        incomplete_xsmn = int(
            connection.execute(
                'SELECT COUNT(*) FROM ('
                'SELECT d.id FROM draws d LEFT JOIN prize_results p ON p.draw_id = d.id '
                'GROUP BY d.id HAVING COUNT(p.id) <> 18)'
            ).fetchone()[0]
        )
    with sqlite3.connect(f'{power_database.resolve().as_uri()}?mode=ro', uri=True) as connection:
        power = connection.execute(
            'SELECT COUNT(*), MIN(draw_date), MAX(draw_date), '
            'MAX(CAST(draw_id AS INTEGER)) - MIN(CAST(draw_id AS INTEGER)) + 1 - COUNT(*) '
            'FROM power655_draws'
        ).fetchone()
    return {
        'xsmn': {
            'draws': int(xsmn[0]),
            'prize_rows': xsmn_prize_rows,
            'first_date': xsmn[1],
            'last_date': xsmn[2],
            'stations': int(xsmn[3]),
            'incomplete_draws': incomplete_xsmn,
        },
        'power655': {
            'draws': int(power[0]),
            'first_date': power[1],
            'last_date': power[2],
            'missing_draw_ids': int(power[3]),
        },
    }


def _current_power_ticket(blend_score: np.ndarray, target_date: str) -> np.ndarray:
    weights = np.clip(blend_score, 1, None)
    probability = weights / weights.sum()
    seed = int.from_bytes(
        hashlib.sha256(f'{POWER_MODEL_VERSION}|{target_date}'.encode()).digest()[:8],
        'big',
    )
    generator = np.random.default_rng(seed)
    generated: dict[tuple[int, ...], float] = {}
    attempts = 0
    while len(generated) < 5 and attempts < 500:
        attempts += 1
        selected = tuple(sorted(generator.choice(np.arange(55), size=6, replace=False, p=probability)))
        generated[selected] = float(weights[list(selected)].sum())
    best = min(generated.items(), key=lambda item: (-item[1], item[0]))[0]
    return np.asarray(best)


def power_walk_forward(draws: pd.DataFrame, minimum_training_draws: int = 100) -> dict[str, object]:
    if minimum_training_draws < 1:
        raise ValueError('minimum_training_draws must be positive')
    if len(draws) <= minimum_training_draws:
        return {'status': 'insufficient_history', 'models': [], 'minimum_training_draws': minimum_training_draws}
    draws = draws.sort_values(['draw_date', 'draw_id']).reset_index(drop=True)
    results = {'current_v1': [], 'uniform_v2': [], 'top6_blend': [], 'top6_without_gap': [], 'top6_long_term': []}
    all_counts = np.zeros(55, dtype=float)
    last_seen = np.full(55, -1, dtype=int)
    prior_draws: list[np.ndarray] = []
    for draw_index, row in enumerate(draws.itertuples(index=False)):
        actual = np.asarray([int(value) - 1 for value in row.main_numbers.split()], dtype=int)
        if draw_index >= minimum_training_draws:
            recent_counts = np.zeros(55, dtype=float)
            for prior in prior_draws[-30:]:
                recent_counts[prior] += 1
            long_rate = all_counts / draw_index
            recent_rate = recent_counts / min(30, draw_index)
            gap = np.where(last_seen >= 0, draw_index - 1 - last_seen, draw_index)
            blend = 100 * (0.50 * _minmax(recent_rate) + 0.30 * _minmax(long_rate) + 0.20 * _minmax(gap))
            without_gap = 100 * (0.625 * _minmax(recent_rate) + 0.375 * _minmax(long_rate))
            candidates = {
                'current_v1': _current_power_ticket(blend, str(row.draw_date)[:10]),
                'uniform_v2': np.asarray(
                    [
                        int(value) - 1
                        for value in _uniform_ticket_numbers(date.fromisoformat(str(row.draw_date)[:10]))[0].split()
                    ]
                ),
                'top6_blend': np.argsort(-blend, kind='stable')[:6],
                'top6_without_gap': np.argsort(-without_gap, kind='stable')[:6],
                'top6_long_term': np.argsort(-long_rate, kind='stable')[:6],
            }
            actual_set = set(actual)
            for model_name, selected in candidates.items():
                results[model_name].append(len(set(selected) & actual_set))
        prior_draws.append(actual)
        all_counts[actual] += 1
        last_seen[actual] = draw_index

    theoretical_mean = 36 / 55
    models = []
    for model_name, values in results.items():
        numeric = np.asarray(values)
        models.append(
            {
                'model': model_name,
                'evaluated_draws': len(numeric),
                'mean_main_matches': round(float(numeric.mean()), 6),
                'mean_ci95': _bootstrap_mean_interval(numeric),
                'draws_with_at_least_two': round(float((numeric >= 2).mean()), 6),
                'draws_with_at_least_three': round(float((numeric >= 3).mean()), 6),
                'lift_vs_theoretical_random': round(float(numeric.mean() / theoretical_mean - 1), 6),
            }
        )
    return {
        'minimum_training_draws': minimum_training_draws,
        'recent_window': 30,
        'theoretical_random_mean': theoretical_mean,
        'models': models,
    }


def xsmn_special_walk_forward(results: pd.DataFrame, minimum_training_draws: int = 100) -> dict[str, object]:
    suffix_results = {'current_v1': [], 'uniform_v2': [], 'positional_map': [], 'suffix2_map': [], 'recent_map': []}
    results = results.sort_values(['station_code', 'draw_date'])
    special = results[(results['prize_code'] == 'db') & (results['number'].str.len() == 6)].copy()
    for station_code, station_frame in special.groupby('station_code', sort=True):
        numbers = station_frame['number'].astype(str).tolist()
        dates = station_frame['draw_date'].astype(str).tolist()
        position_counts = np.zeros((6, 10), dtype=float)
        prior_numbers: list[str] = []
        suffix_counts: Counter[str] = Counter()
        prior_suffixes: list[str] = []
        for draw_index, (actual, target_date) in enumerate(zip(numbers, dates, strict=True)):
            if draw_index >= minimum_training_draws:
                recent_counts = np.zeros((6, 10), dtype=float)
                for value in prior_numbers[-30:]:
                    for position, digit in enumerate(value):
                        recent_counts[position, int(digit)] += 1
                probabilities = 1 + position_counts + 1.5 * recent_counts
                probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
                seed = int.from_bytes(
                    hashlib.sha256(f'{XSMN_FULL_MODEL_VERSION}|{station_code}|{target_date}|db'.encode()).digest()[:8],
                    'big',
                )
                generator = np.random.default_rng(seed)
                current = ''.join(str(int(generator.choice(10, p=probabilities[position]))) for position in range(6))
                positional_map = ''.join(str(int(np.argmax(probabilities[position]))) for position in range(6))
                recent_map = ''.join(str(int(np.argmax(1 + recent_counts[position]))) for position in range(6))
                recent_suffix_counts = Counter(prior_suffixes[-30:])
                selected_suffix = max(
                    (f'{number:02d}' for number in range(100)),
                    key=lambda suffix: (suffix_counts[suffix] + 1.5 * recent_suffix_counts[suffix], -int(suffix)),
                )
                candidates = {
                    'current_v1': current,
                    'uniform_v2': _uniform_numbers(
                        6, 1, f'{XSMN_UNIFORM_VERSION}|{station_code}|{target_date[:10]}|db'
                    )[0],
                    'positional_map': positional_map,
                    'suffix2_map': positional_map[:4] + selected_suffix,
                    'recent_map': recent_map,
                }
                for model_name, candidate in candidates.items():
                    suffix_results[model_name].append(_longest_matching_suffix(candidate, actual))
            for position, digit in enumerate(actual):
                position_counts[position, int(digit)] += 1
            suffix_counts[actual[-2:]] += 1
            prior_numbers.append(actual)
            prior_suffixes.append(actual[-2:])

    if not suffix_results['current_v1']:
        return {
            'status': 'insufficient_history',
            'models': [],
            'minimum_training_draws_per_station': minimum_training_draws,
        }
    models = []
    theoretical_mean_suffix = sum(10**-digits for digits in range(1, 7))
    for model_name, values in suffix_results.items():
        numeric = np.asarray(values)
        models.append(
            {
                'model': model_name,
                'evaluated_draws': len(numeric),
                'mean_matching_suffix_digits': round(float(numeric.mean()), 6),
                'mean_ci95': _bootstrap_mean_interval(numeric),
                'suffix_at_least_one_rate': round(float((numeric >= 1).mean()), 6),
                'suffix_at_least_two_rate': round(float((numeric >= 2).mean()), 6),
                'suffix_at_least_three_rate': round(float((numeric >= 3).mean()), 6),
                'exact_rate': round(float((numeric == 6).mean()), 8),
            }
        )
    return {
        'minimum_training_draws_per_station': minimum_training_draws,
        'recent_window': 30,
        'random_mean_matching_suffix_digits': theoretical_mean_suffix,
        'random_suffix_at_least_two_rate': 0.01,
        'models': models,
    }


def _sample_numbers(probabilities: np.ndarray, count: int, seed: int) -> set[str]:
    generator = np.random.default_rng(seed)
    generated: set[str] = set()
    attempts = 0
    while len(generated) < count and attempts < count * 100:
        attempts += 1
        generated.add(
            ''.join(str(int(generator.choice(10, p=probabilities[position]))) for position in range(len(probabilities)))
        )
    return generated


def _top_combinations(probabilities: np.ndarray, count: int) -> set[str]:
    partial = [('', 1.0)]
    for position_probabilities in probabilities:
        expanded = [
            (prefix + str(digit), score * float(position_probabilities[digit]))
            for prefix, score in partial
            for digit in range(10)
        ]
        partial = sorted(expanded, key=lambda item: (-item[1], item[0]))[:count]
    return {number for number, _ in partial}


def xsmn_full_prize_walk_forward(results: pd.DataFrame, minimum_training_draws: int = 100) -> dict[str, object]:
    per_draw: list[dict[str, int]] = []
    per_prize = defaultdict(lambda: defaultdict(int))
    eligible_draws = defaultdict(int)
    for station_code, station_frame in results.groupby('station_code', sort=True):
        counts = {
            prize_code: np.zeros((digits, 10), dtype=float) for prize_code, (digits, _) in XSMN_PRIZE_SPECS.items()
        }
        recent = {prize_code: deque(maxlen=30) for prize_code in XSMN_PRIZE_SPECS}
        for draw_index, (target_date, draw_frame) in enumerate(station_frame.groupby('draw_date', sort=True)):
            by_prize = {
                prize_code: draw_frame[draw_frame['prize_code'] == prize_code]['number'].astype(str).tolist()
                for prize_code in XSMN_PRIZE_SPECS
            }
            if draw_index >= minimum_training_draws:
                totals = {'current_v1': 0, 'uniform_v2': 0, 'positional_map': 0}
                for prize_code, (digits, result_count) in XSMN_PRIZE_SPECS.items():
                    actual = {value for value in by_prize[prize_code] if len(value) == digits}
                    if not actual:
                        continue
                    eligible_draws[prize_code] += 1
                    recent_counts = np.zeros((digits, 10), dtype=float)
                    for prior_draw in recent[prize_code]:
                        for value in prior_draw:
                            for position, digit in enumerate(value):
                                recent_counts[position, int(digit)] += 1
                    probabilities = 1 + counts[prize_code] + 1.5 * recent_counts
                    probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
                    seed = int.from_bytes(
                        hashlib.sha256(
                            f'{XSMN_FULL_MODEL_VERSION}|{station_code}|{target_date}|{prize_code}'.encode()
                        ).digest()[:8],
                        'big',
                    )
                    predictions = {
                        'current_v1': _sample_numbers(probabilities, result_count, seed),
                        'uniform_v2': set(
                            _uniform_numbers(
                                digits,
                                result_count,
                                f'{XSMN_UNIFORM_VERSION}|{station_code}|{str(target_date)[:10]}|{prize_code}',
                            )
                        ),
                        'positional_map': _top_combinations(probabilities, result_count),
                    }
                    for model_name, predicted in predictions.items():
                        hit_count = len(predicted & actual)
                        totals[model_name] += hit_count
                        per_prize[prize_code][model_name] += hit_count
                per_draw.append(totals)
            for prize_code, (digits, _) in XSMN_PRIZE_SPECS.items():
                values = [value for value in by_prize[prize_code] if len(value) == digits]
                for value in values:
                    for position, digit in enumerate(value):
                        counts[prize_code][position, int(digit)] += 1
                recent[prize_code].append(values)

    frame = pd.DataFrame(per_draw)
    if frame.empty:
        return {
            'status': 'insufficient_history',
            'models': [],
            'by_prize': [],
            'minimum_training_draws_per_station': minimum_training_draws,
        }
    random_expected_per_draw = sum(
        result_count * result_count / (10**digits) for digits, result_count in XSMN_PRIZE_SPECS.values()
    )
    models = []
    for model_name in ('current_v1', 'uniform_v2', 'positional_map'):
        values = frame[model_name].to_numpy()
        models.append(
            {
                'model': model_name,
                'evaluated_draws': len(values),
                'mean_exact_prize_hits': round(float(values.mean()), 6),
                'mean_ci95': _bootstrap_mean_interval(values),
                'draws_with_any_exact_hit': round(float((values >= 1).mean()), 6),
                'total_exact_hits': int(values.sum()),
                'lift_vs_theoretical_random': round(float(values.mean() / random_expected_per_draw - 1), 6),
            }
        )
    prize_rows = []
    for prize_code, (digits, result_count) in XSMN_PRIZE_SPECS.items():
        prize_rows.append(
            {
                'prize_code': prize_code,
                'eligible_draws': eligible_draws[prize_code],
                'random_expected_total_hits': round(
                    eligible_draws[prize_code] * result_count * result_count / (10**digits),
                    6,
                ),
                'current_v1_total_hits': per_prize[prize_code]['current_v1'],
                'uniform_v2_total_hits': per_prize[prize_code]['uniform_v2'],
                'positional_map_total_hits': per_prize[prize_code]['positional_map'],
            }
        )
    return {
        'minimum_training_draws_per_station': minimum_training_draws,
        'recent_window': 30,
        'random_expected_exact_hits_per_draw': random_expected_per_draw,
        'models': models,
        'by_prize': prize_rows,
    }


def _paired_block_interval(
    values: np.ndarray, block_size: int = 14, weights: np.ndarray | None = None
) -> list[float | None]:
    """Moving-block interval on chronological date aggregates, not individual balls."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return [None, None]
    if np.all(values == 0):
        return [0.0, 0.0]
    # At least two complete blocks are needed; one circular block would
    # reproduce the same mean and report a false zero-width interval.
    if len(values) < 2 * block_size:
        return [None, None]
    block_size = min(block_size, len(values))
    weights = np.ones(len(values)) if weights is None else np.asarray(weights, dtype=float)
    generator = np.random.default_rng(20261015)
    means = []
    for _ in range(20):
        starts = generator.integers(0, len(values), size=(100, int(np.ceil(len(values) / block_size))))
        indices = (starts[:, :, None] + np.arange(block_size)) % len(values)
        indices = indices.reshape(100, -1)[:, : len(values)]
        sampled_weights = weights[indices]
        means.extend((values[indices] * sampled_weights).sum(axis=1) / sampled_weights.sum(axis=1))
    return [round(float(value), 8) for value in np.quantile(means, [0.025, 0.975])]


def _calibration_summary(records: list[dict[str, object]], probability_basis: str) -> dict[str, object]:
    frame = pd.DataFrame(records)
    if frame.empty:
        return {'evaluated_draws': 0, 'status': 'insufficient_history', 'models': []}
    dates = sorted(frame['draw_date'].unique())
    holdout_date = dates[min(len(dates) - 1, int(0.8 * len(dates)))]
    rows = []
    for phase, phase_frame in (
        ('walk_forward_all', frame),
        ('chronological_final_20_percent', frame[frame['draw_date'] >= holdout_date]),
    ):
        for model, selected in phase_frame.groupby('model', sort=True):
            # XSMN contains multiple stations on a day: cluster them before
            # resampling dates to avoid pretending those observations are iid.
            by_date = selected.groupby('draw_date', sort=True)[['brier_gain', 'log_loss_gain']].mean()
            date_weights = selected.groupby('draw_date', sort=True).size().to_numpy()
            interval = _paired_block_interval(by_date['brier_gain'].to_numpy(), weights=date_weights)
            rows.append(
                {
                    'phase': phase,
                    'model': model,
                    'evaluated_draws': len(selected),
                    'first_date': str(selected['draw_date'].min()),
                    'last_date': str(selected['draw_date'].max()),
                    'mean_brier_score': round(float(selected['brier'].mean()), 8),
                    'mean_log_loss': round(float(selected['log_loss'].mean()), 8),
                    'brier_gain_vs_uniform': round(float(selected['brier_gain'].mean()), 8),
                    'brier_gain_ci95_date_blocks': interval,
                    'log_loss_gain_vs_uniform': round(float(selected['log_loss_gain'].mean()), 8),
                    'positive_brier_gain_interval': bool(interval[0] is not None and interval[0] > 0),
                }
            )
    return {
        'probability_basis': probability_basis,
        'holdout_start': str(holdout_date),
        'protocol': 'Fixed 100-draw-equivalent prior; fixed 30-draw recent window; no parameter tuning on holdout.',
        'interval': '2000 moving-block resamples of date clusters, weighted by draw count; 14 dates per block; fewer than 28 dates yields no interval; seed 20261015.',
        'multiple_comparisons': 'Exploratory intervals, not adjusted; no automatic promotion based on a positive result.',
        'production_selection': 'uniform_baseline',
        'models': rows,
    }


def calibration_walk_forward(
    xsmn_results: pd.DataFrame,
    power_draws: pd.DataFrame,
    minimum_training_draws: int = 100,
) -> dict[str, object]:
    """Prequential proper scores for predeclared frequency models vs uniform.

    Every forecast is recorded before its outcome updates the count. Power
    scores are marginal Bernoulli scores, not a product pretending the six
    without-replacement balls are independent. XSMN scores concern a single
    station's special-prize two-digit suffix, never the entire ticket.
    """
    if minimum_training_draws < 1:
        raise ValueError('minimum_training_draws must be positive')
    power_records: list[dict[str, object]] = []
    counts = np.zeros(55)
    recent: deque[np.ndarray] = deque(maxlen=30)
    uniform = np.full(55, 6 / 55)
    work = power_draws.sort_values(['draw_date', 'draw_id'])
    for draw_index, row in enumerate(work.itertuples(index=False)):
        actual = np.zeros(55)
        actual[[int(value) - 1 for value in str(row.main_numbers).split()]] = 1
        if draw_index >= minimum_training_draws:
            models = {
                'uniform': uniform,
                'long_shrinkage': (counts + PRIOR_DRAWS * uniform) / (draw_index + PRIOR_DRAWS),
                'recent_shrinkage': (np.sum(recent, axis=0) + PRIOR_DRAWS * uniform) / (len(recent) + PRIOR_DRAWS),
            }
            uniform_brier = float(np.mean((uniform - actual) ** 2))
            uniform_loss = float(-np.mean(actual * np.log(uniform) + (1 - actual) * np.log1p(-uniform)))
            for model, probabilities in models.items():
                brier = float(np.mean((probabilities - actual) ** 2))
                loss = float(-np.mean(actual * np.log(probabilities) + (1 - actual) * np.log1p(-probabilities)))
                power_records.append(
                    {
                        'draw_date': str(row.draw_date)[:10],
                        'model': model,
                        'brier': brier,
                        'log_loss': loss,
                        'brier_gain': uniform_brier - brier,
                        'log_loss_gain': uniform_loss - loss,
                    }
                )
        counts += actual
        recent.append(actual)

    xsmn_records: list[dict[str, object]] = []
    uniform = np.full(100, 0.01)
    special = xsmn_results[
        (xsmn_results['prize_code'] == 'db') & xsmn_results['number'].astype(str).str.fullmatch(r'[0-9]{6}')
    ]
    for _, station in special.groupby('station_code', sort=True):
        counts = np.zeros(100)
        recent = deque(maxlen=30)
        for draw_index, row in enumerate(station.sort_values('draw_date').itertuples(index=False)):
            actual = int(str(row.number)[-2:])
            if draw_index >= minimum_training_draws:
                models = {
                    'uniform': uniform,
                    'long_shrinkage': (counts + 1) / (draw_index + 100),
                    'recent_shrinkage': (np.sum(recent, axis=0) + 1) / (len(recent) + 100),
                }
                for model, probabilities in models.items():
                    brier = float(np.sum(probabilities**2) - 2 * probabilities[actual] + 1)
                    loss = float(-np.log(probabilities[actual]))
                    xsmn_records.append(
                        {
                            'draw_date': str(row.draw_date)[:10],
                            'model': model,
                            'brier': brier,
                            'log_loss': loss,
                            'brier_gain': 0.99 - brier,
                            'log_loss_gain': float(np.log(100)) - loss,
                        }
                    )
            counts[actual] += 1
            observation = np.zeros(100)
            observation[actual] = 1
            recent.append(observation)
    return {
        'power655': _calibration_summary(
            power_records, 'mean per-number Bernoulli Brier and log loss; 55 main-ball indicators'
        ),
        'xsmn_special_suffix2': _calibration_summary(
            xsmn_records, 'categorical Brier sum and log loss; one special suffix per station-draw'
        ),
    }


def run_backtest(root: Path, minimum_training_draws: int = 100) -> dict[str, object]:
    xsmn_database = root / 'data' / 'xsmn.sqlite3'
    power_database = root / 'data' / 'vietlott_power655.sqlite3'
    power_draws = _load_power_draws(power_database)
    xsmn_results = _load_xsmn_results(xsmn_database)
    # Legacy source pages have incompatible widths/payout eras; compare every
    # strategy on the same modern source era, never silently truncate digits.
    modern_xsmn = xsmn_results[xsmn_results['draw_date'] >= '2017-01-01'].copy()
    return {
        'as_of': {
            'xsmn': str(xsmn_results['draw_date'].max()),
            'power655': str(power_draws['draw_date'].max()),
        },
        'data_quality': data_quality_profile(xsmn_database, power_database),
        'protocol': {
            'xsmn_source_era_start': '2017-01-01',
            'xsmn_rows_excluded_legacy_era': len(xsmn_results) - len(modern_xsmn),
            'training': 'strictly earlier dates, expanding window',
            'minimum_training_draws': minimum_training_draws,
            'selection': 'uniform production baseline; experimental models are not auto-promoted',
            'limitation': 'Historical diagnostics do not demonstrate future profit or independent replication.',
            'data_exposure_note': 'Repository ledgers may already contain outcomes beyond the database snapshot; '
            'this evaluation does not certify an untouched holdout.',
        },
        'power655': power_walk_forward(power_draws, minimum_training_draws),
        'xsmn_special': xsmn_special_walk_forward(modern_xsmn, minimum_training_draws),
        'xsmn_full_prize': xsmn_full_prize_walk_forward(modern_xsmn, minimum_training_draws),
        'calibration': calibration_walk_forward(modern_xsmn, power_draws, minimum_training_draws),
    }


def run_power_backtest(database_path: Path, minimum_training_draws: int = 100) -> dict[str, object]:
    draws = _load_power_draws(database_path)
    special = pd.DataFrame(columns=['prize_code', 'number', 'station_code', 'draw_date'])
    return {
        'protocol': {
            'minimum_training_draws': minimum_training_draws,
            'total_draws': len(draws),
            'evaluated_draws': max(0, len(draws) - minimum_training_draws),
            'first_evaluation_date': str(draws.iloc[minimum_training_draws]['draw_date'])
            if len(draws) > minimum_training_draws
            else None,
            'last_evaluation_date': str(draws.iloc[-1]['draw_date']) if len(draws) else None,
            'evaluation': 'Fixed methods; strictly earlier draws; expanding chronological walk-forward.',
            'scope': 'Power 6/55 supplementary historical evaluation, not a prospective live forecast test.',
            'data_exposure_note': 'Repository ledgers may already contain outcomes beyond the database snapshot; '
            'this evaluation does not certify an untouched holdout.',
        },
        'power655': power_walk_forward(draws, minimum_training_draws),
        'calibration': calibration_walk_forward(special, draws, minimum_training_draws)['power655'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Walk-forward backtest for lottery prediction heuristics')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--minimum-training-draws', type=int, default=100)
    parser.add_argument('--power-only', action='store_true')
    parser.add_argument('--power-database', type=Path)
    arguments = parser.parse_args()
    if arguments.minimum_training_draws < 1:
        parser.error('--minimum-training-draws must be positive')
    if arguments.power_database and not arguments.power_only:
        parser.error('--power-database requires --power-only')
    report = (
        run_power_backtest(
            arguments.power_database or arguments.root / 'data' / 'vietlott_power655.sqlite3',
            arguments.minimum_training_draws,
        )
        if arguments.power_only
        else run_backtest(arguments.root, arguments.minimum_training_draws)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
