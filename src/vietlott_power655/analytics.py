from __future__ import annotations

from math import comb, sqrt

import pandas as pd

from .config import NUMBER_MAX, NUMBER_MIN


def frequency_statistics(draws: pd.DataFrame, include_bonus: bool = False) -> pd.DataFrame:
    universe = [f'{number:02d}' for number in range(NUMBER_MIN, NUMBER_MAX + 1)]
    if draws.empty:
        frame = pd.DataFrame(
            {
                'number': universe,
                'count': 0,
                'observation_rate': 0.0,
                'draws_with_number': 0,
                'draw_rate': 0.0,
                'gap_draws': 0,
            }
        )
        frame.attrs.update(total_draws=0, total_observations=0, include_bonus=include_bonus)
        return frame

    work = draws.copy()
    work['draw_date'] = pd.to_datetime(work['draw_date'])
    records: list[dict[str, object]] = []
    for row in work.itertuples(index=False):
        main_numbers = str(row.main_numbers).split()
        for number in main_numbers:
            records.append({'draw_id': row.draw_id, 'draw_date': row.draw_date, 'number': number, 'role': 'main'})
        if include_bonus:
            records.append(
                {'draw_id': row.draw_id, 'draw_date': row.draw_date, 'number': row.bonus_number, 'role': 'bonus'}
            )
    observations = pd.DataFrame(records)
    total_observations = len(observations)
    total_draws = work['draw_id'].nunique()
    counts = observations['number'].value_counts().reindex(universe, fill_value=0)
    unique_hits = observations.drop_duplicates(['draw_id', 'number'])
    draws_with_number = unique_hits['number'].value_counts().reindex(universe, fill_value=0)
    draw_order = (
        work[['draw_id', 'draw_date']].drop_duplicates().sort_values(['draw_date', 'draw_id']).reset_index(drop=True)
    )
    draw_order['draw_index'] = draw_order.index
    observations = observations.merge(
        draw_order[['draw_id', 'draw_index']], on='draw_id', how='left', validate='many_to_one'
    )
    last_index = observations.groupby('number')['draw_index'].max().reindex(universe)
    gap_draws = (len(draw_order) - 1 - last_index).fillna(len(draw_order)).astype(int)

    frame = pd.DataFrame(
        {
            'number': universe,
            'count': counts.astype(int).to_numpy(),
            'observation_rate': counts.to_numpy() / total_observations,
            'draws_with_number': draws_with_number.astype(int).to_numpy(),
            'draw_rate': draws_with_number.to_numpy() / total_draws,
            'gap_draws': gap_draws.to_numpy(),
        }
    )
    theoretical_draw_rate = (7 if include_bonus else 6) / (NUMBER_MAX - NUMBER_MIN + 1)
    standard_error = sqrt(theoretical_draw_rate * (1 - theoretical_draw_rate) / total_draws)
    frame['theoretical_draw_rate'] = theoretical_draw_rate
    frame['draw_rate_difference'] = frame['draw_rate'] - theoretical_draw_rate
    frame['z_score'] = frame['draw_rate_difference'] / standard_error
    frame.attrs.update(total_draws=total_draws, total_observations=total_observations, include_bonus=include_bonus)
    return frame


def prize_probabilities() -> pd.DataFrame:
    """Return exact single-ticket probabilities under a uniform 6-from-55 draw."""
    total = comb(55, 6)
    outcomes = (
        ('Jackpot 1', 1),
        ('Jackpot 2', comb(6, 5)),
        ('Giải Nhất', comb(6, 5) * 48),
        ('Giải Nhì', comb(6, 4) * comb(49, 2)),
        ('Giải Ba', comb(6, 3) * comb(49, 3)),
    )
    rows = [
        {'prize': prize, 'winning_combinations': count, 'probability': count / total, 'odds_one_in': total / count}
        for prize, count in outcomes
    ]
    any_prize_count = sum(count for _, count in outcomes)
    rows.append(
        {
            'prize': 'Bất kỳ giải nào',
            'winning_combinations': any_prize_count,
            'probability': any_prize_count / total,
            'odds_one_in': total / any_prize_count,
        }
    )
    return pd.DataFrame(rows)


def top_frequency_table(statistics: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    ranked = (
        statistics.sort_values(['count', 'draws_with_number', 'number'], ascending=[False, False, True])
        .head(limit)
        .reset_index(drop=True)
    )
    return ranked[['number', 'count', 'observation_rate', 'draws_with_number', 'draw_rate', 'gap_draws']]
