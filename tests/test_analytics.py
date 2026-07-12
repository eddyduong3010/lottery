from datetime import date

import pandas as pd
import pytest

from xsmn.analytics import frequency_statistics
from xsmn.prediction import generate_special_number_candidates, rank_suffix_candidates


def sample_results() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {'draw_date': '2026-07-01', 'station_code': 'VL', 'prize_code': 'g8', 'number': '05'},
            {'draw_date': '2026-07-01', 'station_code': 'VL', 'prize_code': 'g7', 'number': '105'},
            {'draw_date': '2026-07-01', 'station_code': 'VL', 'prize_code': 'db', 'number': '123405'},
            {'draw_date': '2026-07-08', 'station_code': 'VL', 'prize_code': 'g8', 'number': '12'},
            {'draw_date': '2026-07-08', 'station_code': 'VL', 'prize_code': 'db', 'number': '223412'},
            {'draw_date': '2026-07-10', 'station_code': 'VL', 'prize_code': 'g8', 'number': '99'},
            {'draw_date': '2026-07-10', 'station_code': 'VL', 'prize_code': 'db', 'number': '323499'},
        ]
    )


def test_frequency_uses_observation_and_draw_denominators() -> None:
    stats = frequency_statistics(sample_results(), suffix_digits=2)
    number_05 = stats.set_index('number').loc['05']

    assert number_05['count'] == 3
    assert number_05['observation_rate'] == 3 / 7
    assert number_05['draws_with_number'] == 1
    assert number_05['draw_rate'] == 1 / 3
    assert number_05['gap_draws'] == 2


def test_three_digit_statistics_exclude_two_digit_prizes_from_denominator() -> None:
    stats = frequency_statistics(sample_results(), suffix_digits=3)
    assert stats.attrs['total_observations'] == 4
    assert stats['count'].sum() == 4
    assert stats['observation_rate'].sum() == 1


def test_gap_uses_draw_dates_not_arbitrary_same_day_station_order() -> None:
    results = pd.DataFrame(
        [
            {'draw_date': '2026-07-01', 'station_code': 'VL', 'prize_code': 'g8', 'number': '99'},
            {'draw_date': '2026-07-02', 'station_code': 'BD', 'prize_code': 'g8', 'number': '05'},
            {'draw_date': '2026-07-02', 'station_code': 'VL', 'prize_code': 'g8', 'number': '99'},
        ]
    )
    stats = frequency_statistics(results, suffix_digits=2).set_index('number')
    assert stats.loc['05', 'gap_draws'] == 0


def test_predictions_are_deterministic_and_well_shaped() -> None:
    results = sample_results()
    first = rank_suffix_candidates(results, recent_draws=2, top_k=5)
    second = rank_suffix_candidates(results, recent_draws=2, top_k=5)
    pd.testing.assert_frame_equal(first, second)
    assert first['model_score'].between(0, 100).all()

    candidates_a = generate_special_number_candidates(results, 'VL', date(2026, 7, 17), candidate_count=5)
    candidates_b = generate_special_number_candidates(results, 'VL', date(2026, 7, 17), candidate_count=5)
    pd.testing.assert_frame_equal(candidates_a, candidates_b)
    assert candidates_a['number'].str.fullmatch(r'\d{6}').all()


def test_special_prediction_ignores_other_stations_and_future_draws() -> None:
    results = sample_results()
    baseline = generate_special_number_candidates(results, 'VL', date(2026, 7, 17), candidate_count=5)
    contaminated = pd.concat(
        [
            results,
            pd.DataFrame(
                [
                    {
                        'draw_date': '2026-07-09',
                        'station_code': 'BD',
                        'prize_code': 'db',
                        'number': '999999',
                    },
                    {
                        'draw_date': '2026-07-18',
                        'station_code': 'VL',
                        'prize_code': 'db',
                        'number': '888888',
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    actual = generate_special_number_candidates(contaminated, 'VL', date(2026, 7, 17), candidate_count=5)
    pd.testing.assert_frame_equal(baseline, actual)


def test_special_prediction_ignores_historical_five_digit_special_prizes() -> None:
    results = sample_results()
    baseline = generate_special_number_candidates(results, 'VL', date(2026, 7, 17), candidate_count=5)
    with_historical_draw = pd.concat(
        [
            results,
            pd.DataFrame(
                [
                    {
                        'draw_date': '2005-12-29',
                        'station_code': 'VL',
                        'prize_code': 'db',
                        'number': '12345',
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    actual = generate_special_number_candidates(with_historical_draw, 'VL', date(2026, 7, 17), candidate_count=5)

    pd.testing.assert_frame_equal(baseline, actual)
    assert actual['number'].str.fullmatch(r'\d{6}').all()


def test_special_prediction_validates_window_and_candidate_count() -> None:
    with pytest.raises(ValueError, match='Số lượng'):
        generate_special_number_candidates(sample_results(), 'VL', date(2026, 7, 17), candidate_count=0)
    with pytest.raises(ValueError, match='Số kỳ'):
        generate_special_number_candidates(sample_results(), 'VL', date(2026, 7, 17), recent_draws=0)
