from datetime import datetime

import pandas as pd
import pytest

from prediction_history import (
    load_prediction_history,
    prediction_comparison_rows,
    prediction_performance_frame,
    prediction_performance_summary,
    saved_prediction_candidates,
    update_prediction_history,
)
from xsmn.calendar import VIETNAM_TZ


def xsmn_training_results() -> pd.DataFrame:
    rows = []
    for station_code, number in [('HCM', '111111'), ('DT', '222222'), ('CM', '333333')]:
        for draw_date in ('2026-06-29', '2026-07-06'):
            rows.append(
                {
                    'draw_date': draw_date,
                    'station_code': station_code,
                    'prize_code': 'db',
                    'number': number,
                }
            )
    return pd.DataFrame(rows)


def power_training_draws() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'draw_id': '01368',
                'draw_date': '2026-07-07',
                'main_numbers': '01 02 03 04 05 06',
                'bonus_number': '07',
            },
            {
                'draw_id': '01369',
                'draw_date': '2026-07-09',
                'main_numbers': '08 09 10 11 12 13',
                'bonus_number': '14',
            },
            {
                'draw_id': '01370',
                'draw_date': '2026-07-11',
                'main_numbers': '15 16 17 18 19 20',
                'bonus_number': '21',
            },
        ]
    )


def test_prediction_history_is_idempotent_and_evaluates_real_results(tmp_path) -> None:
    history_path = tmp_path / 'prediction-history.json'
    initial_now = datetime(2026, 7, 12, 17, 30, tzinfo=VIETNAM_TZ)

    first = update_prediction_history(
        history_path,
        xsmn_training_results(),
        power_training_draws(),
        now=initial_now,
    )
    second = update_prediction_history(
        history_path,
        xsmn_training_results(),
        power_training_draws(),
        now=initial_now,
    )

    assert len(first['predictions']) == 4
    assert all(len(record['candidates']) == 1 for record in first['predictions'])
    assert second == first
    assert load_prediction_history(history_path) == first
    assert {record['target_date'] for record in first['predictions']} == {'2026-07-13', '2026-07-14'}

    changed_training = pd.concat(
        [
            xsmn_training_results(),
            pd.DataFrame(
                [
                    {
                        'draw_date': '2026-07-11',
                        'station_code': 'HCM',
                        'prize_code': 'db',
                        'number': '999999',
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    locked = update_prediction_history(history_path, changed_training, power_training_draws(), now=initial_now)
    assert locked == first
    saved = saved_prediction_candidates(locked, 'xsmn', initial_now.date().replace(day=13), 'HCM')
    expected_values = [
        candidate['value']
        for record in first['predictions']
        if record['game'] == 'xsmn' and record['station_code'] == 'HCM'
        for candidate in record['candidates']
    ]
    assert saved['value'].tolist() == expected_values

    xsmn_actual = pd.concat(
        [
            xsmn_training_results(),
            pd.DataFrame(
                [
                    {
                        'draw_date': '2026-07-13',
                        'station_code': station_code,
                        'prize_code': 'db',
                        'number': number,
                    }
                    for station_code, number in [('HCM', '111111'), ('DT', '222222'), ('CM', '333333')]
                ]
            ),
        ],
        ignore_index=True,
    )
    power_actual = pd.concat(
        [
            power_training_draws(),
            pd.DataFrame(
                [
                    {
                        'draw_id': '01371',
                        'draw_date': '2026-07-14',
                        'main_numbers': '01 08 15 22 29 36',
                        'bonus_number': '55',
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    evaluated = update_prediction_history(
        history_path,
        xsmn_actual,
        power_actual,
        now=datetime(2026, 7, 14, 19, 0, tzinfo=VIETNAM_TZ),
    )

    evaluated_originals = [
        record for record in evaluated['predictions'] if record['target_date'] in {'2026-07-13', '2026-07-14'}
    ]
    assert len(evaluated_originals) == 4
    assert all(record.get('actual') for record in evaluated_originals)
    assert prediction_performance_frame(evaluated, 'xsmn')['evaluated'].any()
    assert prediction_performance_frame(evaluated, 'power655')['best_main_matches'].notna().any()


def test_prediction_comparison_rows_expose_suffix_prize_and_power_matches() -> None:
    history = {
        'schema_version': 1,
        'predictions': [
            {
                'game': 'xsmn',
                'target_date': '2026-07-20',
                'station_code': 'CM',
                'model_version': 'xsmn-test',
                'candidates': [{'rank': 1, 'value': '123456', 'model_score': 80.0}],
                'prize_predictions': [
                    {'prize_code': 'g8', 'ordinal': 1, 'number': '12', 'model_score': 70.0},
                    {'prize_code': 'db', 'ordinal': 1, 'number': '123456', 'model_score': 80.0},
                ],
                'actual': {
                    'special_number': '923456',
                    'prize_results': {'g8': ['12'], 'db': ['923456']},
                },
                'metrics': {
                    'exact_hit': False,
                    'best_suffix_digits': 5,
                    'best_candidate': '123456',
                    'prize_hits': 1,
                    'prize_total': 2,
                },
            },
            {
                'game': 'power655',
                'target_date': '2026-07-21',
                'station_code': '',
                'model_version': 'power-test',
                'candidates': [{'rank': 1, 'value': '01 02 03 20 21 55', 'model_score': 90.0}],
                'actual': {
                    'main_numbers': '01 03 05 07 09 11',
                    'bonus_number': '55',
                },
                'metrics': {
                    'exact_hit': False,
                    'best_main_matches': 2,
                    'bonus_in_best_candidate': True,
                    'best_candidate': '01 02 03 20 21 55',
                },
            },
        ],
    }

    xsmn_row = prediction_comparison_rows(history, 'xsmn')[0]
    assert xsmn_row['matching_suffix_digits'] == 5
    assert xsmn_row['prize_matches'] == [{'prize_code': 'g8', 'ordinal': 1, 'number': '12'}]

    power_row = prediction_comparison_rows(history, 'power655')[0]
    assert power_row['matched_main_numbers'] == ['01', '03']
    assert power_row['matched_bonus'] is True

    frame = prediction_performance_frame(history, 'power655')
    assert frame.loc[0, 'matched_numbers'] == '01 03'


def test_prediction_performance_summary_uses_mathematical_random_baselines() -> None:
    history = {
        'schema_version': 1,
        'predictions': [
            {
                'game': 'xsmn',
                'candidates': [{'rank': 1, 'value': '123456'}],
                'prize_predictions': [
                    {'prize_code': 'g8', 'ordinal': 1, 'number': '12'},
                    {'prize_code': 'db', 'ordinal': 1, 'number': '123456'},
                ],
                'actual': {'prize_results': {'g8': ['12'], 'db': ['923456']}},
                'metrics': {'exact_hit': False, 'best_suffix_digits': 5, 'prize_hits': 1},
            },
            {
                'game': 'power655',
                'candidates': [{'rank': 1, 'value': '01 02 03 20 21 55'}],
                'actual': {'main_numbers': '01 03 05 07 09 11', 'bonus_number': '55'},
                'metrics': {'exact_hit': False, 'best_main_matches': 2},
            },
        ],
    }

    xsmn = prediction_performance_summary(history, 'xsmn')
    assert xsmn['suffix_two_rate'] == 1.0
    assert xsmn['random_suffix_two_rate'] == pytest.approx(0.01)
    assert xsmn['random_expected_prize_hits'] == pytest.approx(0.010001)

    power = prediction_performance_summary(history, 'power655')
    assert power['average_main_matches'] == 2.0
    assert power['random_expected_main_matches'] == 36 / 55
    assert 0.12 < power['random_at_least_two_rate'] < 0.13
