from datetime import date, timedelta
from math import comb, log

import numpy as np
import pandas as pd
import pytest

from prediction_history import (
    _append_power655_prediction,
    _append_xsmn_predictions,
    _ensure_xsmn_full_predictions,
    prediction_performance_summary,
    prediction_prize_timing_verified,
    prediction_timing_verified,
    saved_prediction_candidates,
)
from vietlott_power655.prediction import generate_ticket_candidates, rank_number_candidates
from xsmn.prediction import generate_special_number_candidates, rank_suffix_candidates


def power_history(count: int = 10) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'draw_date': (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                'draw_id': f'{index + 1:05}',
                'main_numbers': '01 02 03 04 05 06',
                'bonus_number': '07',
            }
            for index in range(count)
        ]
    )


def special_history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {'draw_date': '2026-01-01', 'station_code': 'VL', 'prize_code': 'db', 'number': '000001'},
            {'draw_date': '2026-01-08', 'station_code': 'VL', 'prize_code': 'db', 'number': '000099'},
        ]
    )


def test_power_target_and_future_rows_never_enter_prediction_or_metadata() -> None:
    draws = power_history()
    target = date(2026, 1, 6)
    expected = generate_ticket_candidates(draws.iloc[:5], target, candidate_count=3)
    actual = generate_ticket_candidates(draws, target, candidate_count=3)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.attrs['training_cutoff'] == pd.Timestamp('2026-01-05')
    assert generate_ticket_candidates(draws.iloc[5:], target).empty
    pd.testing.assert_frame_equal(
        rank_number_candidates(draws, target_date=target),
        rank_number_candidates(draws.iloc[:5], target_date=target),
    )


def test_uniform_references_have_exact_theoretical_probability_and_stable_first_candidate() -> None:
    draws = power_history()
    one = generate_ticket_candidates(draws, date(2026, 2, 1), candidate_count=1)
    many = generate_ticket_candidates(draws, date(2026, 2, 1), candidate_count=100)
    assert one.iloc[0]['numbers'] == many.iloc[0]['numbers']
    assert many['numbers'].is_unique
    assert all(len(set(value.split())) == 6 for value in many['numbers'])
    assert (many['probability'] == 1 / comb(55, 6)).all()
    assert (many['model_score'] == 0).all()
    assert many.attrs['selection_method'] == 'uniform_baseline'
    special = generate_special_number_candidates(special_history(), 'VL', date(2026, 2, 1))
    assert (special['probability'] == 1 / 1_000_000).all()
    assert (special['model_score'] == 0).all()


def test_smoothed_frequencies_do_not_treat_gaps_as_predictive_probability() -> None:
    ranked = rank_number_candidates(power_history(1), top_k=55).set_index('number')
    assert ranked['probability'].sum() == pytest.approx(6)
    assert ranked['model_score'].sum() == pytest.approx(600)
    assert ranked.loc['01', 'model_score'] < ranked.loc['01', 'long_frequency']
    assert ranked.loc['55', 'model_score'] > 0
    assert (ranked['gap_component'] == 0).all()
    suffix = rank_suffix_candidates(special_history(), top_k=100).set_index('number')
    assert suffix['model_score'].sum() == pytest.approx(100)
    assert suffix['probability'].sum() == pytest.approx(1)
    assert suffix.loc['01', 'gap_draws'] != suffix.loc['99', 'gap_draws']
    assert suffix.loc['01', 'model_score'] == suffix.loc['99', 'model_score']


def test_prediction_parameter_validation_and_target_filter() -> None:
    with pytest.raises(ValueError, match='Số kỳ'):
        generate_ticket_candidates(power_history(), date(2026, 2, 1), recent_draws=0)
    with pytest.raises(ValueError, match='Số lượng'):
        rank_number_candidates(power_history(), top_k=-1)
    with pytest.raises(ValueError, match='Số lượng'):
        rank_suffix_candidates(special_history(), top_k=0)
    assert rank_suffix_candidates(special_history(), target_date=date(2025, 1, 1)).empty


def test_saved_prediction_preserves_version_and_filtered_cutoff() -> None:
    records = []
    _append_power655_prediction(records, set(), power_history(), date(2026, 1, 6), '2026-01-05T10:00:00+07:00')
    assert records[0]['training_cutoff'] == '2026-01-05'
    assert records[0]['selection_method'] == 'uniform_baseline'
    assert records[0]['candidates'][0]['probability'] == 1 / comb(55, 6)
    saved = saved_prediction_candidates({'predictions': records}, 'power655', date(2026, 1, 6))
    assert saved.attrs['model_version'] == records[0]['model_version']
    assert saved.attrs['selection_method'] == 'uniform_baseline'


def test_past_forecasts_are_never_retroactively_backfilled() -> None:
    records = [
        {
            'game': 'xsmn',
            'target_date': '2026-01-15',
            'station_code': 'VL',
            'model_version': 'legacy',
            'candidates': [{'rank': 1, 'value': '123456', 'model_score': 99}],
        }
    ]
    _ensure_xsmn_full_predictions(records, special_history(), date(2026, 1, 15))
    assert 'prize_predictions' not in records[0]
    saved = saved_prediction_candidates({'predictions': records}, 'xsmn', date(2026, 1, 15), 'VL')
    assert saved.attrs['selection_method'] == 'legacy_unverified'
    assert saved.attrs['model_version'] == 'legacy'


def test_summary_exposes_unverified_timing_and_best_of_multiple_mismatch() -> None:
    record = {
        'game': 'xsmn',
        'target_date': '2026-01-15',
        'model_version': 'legacy',
        'candidates': [{'value': '123456'}, {'value': '654356'}],
        'actual': {'special_number': '000056'},
        'metrics': {'best_suffix_digits': 2},
    }
    summary = prediction_performance_summary({'predictions': [record]}, 'xsmn')
    assert summary['timing_unverified_count'] == 1
    assert summary['baseline_comparison_valid'] is False
    # Two references sharing the same suffix still cover only one of 100 tails.
    assert summary['random_suffix_two_rate'] == 0.01
    record.update(created_at='2026-01-14T11:00:00+07:00', training_cutoff='2026-01-08')
    summary = prediction_performance_summary({'predictions': [record]}, 'xsmn')
    assert summary['timing_verified_count'] == 1


def test_chronological_calibration_scores_against_exact_uniform_baseline() -> None:
    # The report is executable from both root and analysis/ without import drift.
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'analysis' / 'prediction_backtest.py'
    spec = importlib.util.spec_from_file_location('lottery_backtest', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    history = power_history(12)
    special = special_history()
    expected = module.calibration_walk_forward(special, history, minimum_training_draws=2)
    shuffled = module.calibration_walk_forward(
        special, history.sample(frac=1, random_state=12), minimum_training_draws=2
    )
    assert expected == shuffled
    uniform = next(
        row
        for row in expected['power655']['models']
        if row['model'] == 'uniform' and row['phase'] == 'walk_forward_all'
    )
    p = 6 / 55
    assert uniform['evaluated_draws'] == 10
    assert uniform['mean_brier_score'] == pytest.approx(p * (1 - p))
    assert uniform['mean_log_loss'] == pytest.approx(-p * log(p) - (1 - p) * log(1 - p))
    assert uniform['brier_gain_ci95_date_blocks'] == [0, 0]
    assert expected['xsmn_special_suffix2']['status'] == 'insufficient_history'
    assert module._bootstrap_mean_interval(np.array([])) == [None, None]
    assert module._paired_block_interval(np.array([0.1, -0.1, 0.2])) == [None, None]


def test_legacy_prize_backfill_does_not_inherit_original_candidate_timestamp() -> None:
    record = {
        'game': 'xsmn',
        'target_date': '2026-01-15',
        'station_code': 'VL',
        'created_at': '2026-01-01T10:00:00+07:00',
        'training_cutoff': '2026-01-08',
        'candidates': [{'rank': 1, 'value': '123456'}],
        'prize_predictions': [{'prize_code': 'db', 'number': '123456'}],
        'actual': {'special_number': '654321'},
    }
    assert prediction_timing_verified(record)
    assert not prediction_prize_timing_verified(record)
    summary = prediction_performance_summary({'predictions': [record]}, 'xsmn')
    assert summary['prize_timing_verified_count'] == 0
    assert summary['prize_timing_unverified_count'] == 1
    record['prize_predictions_created_at'] = '2026-01-14T10:00:00+07:00'
    assert prediction_prize_timing_verified(record)
    record['prize_predictions_created_at'] = '2026-01-15T10:00:00+07:00'
    assert not prediction_prize_timing_verified(record)


def test_new_full_predictions_and_future_fill_record_their_own_generation_time() -> None:
    generated_at = '2026-01-12T10:00:00+07:00'
    records = []
    _append_xsmn_predictions(records, set(), special_history(), date(2026, 1, 15), ['VL'], generated_at)
    assert records[0]['prize_predictions_created_at'] == generated_at
    assert records[0]['prize_training_cutoff'] == '2026-01-08'
    assert prediction_prize_timing_verified(records[0])
    legacy = [
        {
            'game': 'xsmn',
            'target_date': '2026-01-15',
            'station_code': 'VL',
            'created_at': '2026-01-01T10:00:00+07:00',
            'training_cutoff': '2026-01-01',
            'candidates': [{'rank': 1, 'value': '123456'}],
        }
    ]
    _ensure_xsmn_full_predictions(legacy, special_history(), date(2026, 1, 12), generated_at=generated_at)
    assert legacy[0]['prize_predictions_created_at'] == generated_at
    assert legacy[0]['prize_predictions_created_at'] != legacy[0]['created_at']
    assert legacy[0]['prize_training_cutoff'] == '2026-01-08'
    assert prediction_prize_timing_verified(legacy[0])
