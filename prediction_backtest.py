from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

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


def _bootstrap_mean_interval(values: list[float] | np.ndarray, seed: int = 20260722) -> list[float]:
    numeric = np.asarray(values, dtype=float)
    generator = np.random.default_rng(seed)
    means = generator.choice(numeric, size=(4_000, len(numeric)), replace=True).mean(axis=1)
    return [round(float(value), 6) for value in np.quantile(means, [0.025, 0.975])]


def _longest_matching_suffix(candidate: str, actual: str) -> int:
    for digits in range(min(len(candidate), len(actual)), 0, -1):
        if candidate[-digits:] == actual[-digits:]:
            return digits
    return 0


def _load_power_draws(database_path: Path) -> pd.DataFrame:
    with sqlite3.connect(database_path) as connection:
        return pd.read_sql_query(
            'SELECT draw_id, draw_date, main_numbers, bonus_number FROM power655_draws ORDER BY draw_date, draw_id',
            connection,
        )


def _load_xsmn_results(database_path: Path) -> pd.DataFrame:
    with sqlite3.connect(database_path) as connection:
        return pd.read_sql_query(
            'SELECT d.draw_date, d.station_code, p.prize_code, p.ordinal, p.number '
            'FROM draws d JOIN prize_results p ON p.draw_id = d.id '
            'ORDER BY d.station_code, d.draw_date, p.prize_code, p.ordinal',
            connection,
        )


def data_quality_profile(xsmn_database: Path, power_database: Path) -> dict[str, object]:
    with sqlite3.connect(xsmn_database) as connection:
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
    with sqlite3.connect(power_database) as connection:
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
    best = sorted(generated.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return np.asarray(best)


def power_walk_forward(draws: pd.DataFrame, minimum_training_draws: int = 100) -> dict[str, object]:
    results = {'current_v1': [], 'top6_blend': [], 'top6_without_gap': [], 'top6_long_term': []}
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
                'current_v1': _current_power_ticket(blend, str(row.draw_date)),
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
    suffix_results = {'current_v1': [], 'positional_map': [], 'suffix2_map': [], 'recent_map': []}
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
                totals = {'current_v1': 0, 'positional_map': 0}
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
    random_expected_per_draw = sum(
        result_count * result_count / (10**digits) for digits, result_count in XSMN_PRIZE_SPECS.values()
    )
    models = []
    for model_name in ('current_v1', 'positional_map'):
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


def run_backtest(root: Path) -> dict[str, object]:
    xsmn_database = root / 'data' / 'xsmn.sqlite3'
    power_database = root / 'data' / 'vietlott_power655.sqlite3'
    power_draws = _load_power_draws(power_database)
    xsmn_results = _load_xsmn_results(xsmn_database)
    return {
        'as_of': {
            'xsmn': str(xsmn_results['draw_date'].max()),
            'power655': str(power_draws['draw_date'].max()),
        },
        'data_quality': data_quality_profile(xsmn_database, power_database),
        'power655': power_walk_forward(power_draws),
        'xsmn_special': xsmn_special_walk_forward(xsmn_results),
        'xsmn_full_prize': xsmn_full_prize_walk_forward(xsmn_results),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Walk-forward backtest for lottery prediction heuristics')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    print(json.dumps(run_backtest(arguments.root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
