from __future__ import annotations

import pandas as pd


def _validate_suffix_digits(suffix_digits: int) -> None:
    if suffix_digits not in {2, 3}:
        raise ValueError('Ứng dụng hiện hỗ trợ thống kê đuôi 2 hoặc 3 số')


def select_scope(results: pd.DataFrame, prize_scope: str = 'all') -> pd.DataFrame:
    if prize_scope not in {'all', 'special'}:
        raise ValueError("Phạm vi giải phải là 'all' hoặc 'special'")
    selected = results.copy()
    if prize_scope == 'special':
        selected = selected[selected['prize_code'] == 'db'].copy()
    return selected


def frequency_statistics(
    results: pd.DataFrame,
    suffix_digits: int = 2,
    prize_scope: str = 'all',
) -> pd.DataFrame:
    """Return occurrence and per-draw rates with an explicit denominator.

    ``observation_rate`` counts occurrences across individual prize results.
    ``draw_rate`` counts the share of station-draws where the number appeared at
    least once. A station drawing on one date is one draw in the denominator.
    """

    _validate_suffix_digits(suffix_digits)
    work = select_scope(results, prize_scope)
    if not work.empty:
        work['number'] = work['number'].astype(str)
        work = work[work['number'].str.len() >= suffix_digits].copy()
    universe = [f'{number:0{suffix_digits}d}' for number in range(10**suffix_digits)]
    if work.empty:
        empty = pd.DataFrame(
            {
                'number': universe,
                'count': 0,
                'observation_rate': 0.0,
                'draws_with_number': 0,
                'draw_rate': 0.0,
                'last_seen': pd.NaT,
                'gap_draws': 0,
            }
        )
        empty.attrs.update(total_observations=0, total_draws=0, suffix_digits=suffix_digits)
        return empty

    work['draw_date'] = pd.to_datetime(work['draw_date'])
    work['suffix'] = work['number'].str[-suffix_digits:]

    station_draws = (
        work[['draw_date', 'station_code']]
        .drop_duplicates()
        .sort_values(['draw_date', 'station_code'])
        .reset_index(drop=True)
    )
    date_order = work[['draw_date']].drop_duplicates().sort_values('draw_date').reset_index(drop=True)
    date_order['date_index'] = date_order.index
    work = work.merge(date_order, on='draw_date', how='left', validate='many_to_one')

    total_observations = len(work)
    total_draws = len(station_draws)
    total_dates = len(date_order)
    counts = work['suffix'].value_counts().reindex(universe, fill_value=0)
    unique_draw_hits = work.drop_duplicates(['draw_date', 'station_code', 'suffix'])
    draws_with_number = unique_draw_hits['suffix'].value_counts().reindex(universe, fill_value=0)
    last_seen = work.groupby('suffix')['draw_date'].max().reindex(universe)
    last_index = work.groupby('suffix')['date_index'].max().reindex(universe)
    gap_draws = (total_dates - 1 - last_index).fillna(total_dates).astype(int)

    frame = pd.DataFrame(
        {
            'number': universe,
            'count': counts.astype(int).to_numpy(),
            'observation_rate': counts.to_numpy() / total_observations,
            'draws_with_number': draws_with_number.astype(int).to_numpy(),
            'draw_rate': draws_with_number.to_numpy() / total_draws,
            'last_seen': last_seen.to_numpy(),
            'gap_draws': gap_draws.to_numpy(),
        }
    )
    frame.attrs.update(
        total_observations=total_observations,
        total_draws=total_draws,
        suffix_digits=suffix_digits,
        prize_scope=prize_scope,
    )
    return frame


def top_frequency_table(statistics: pd.DataFrame, limit: int = 15) -> pd.DataFrame:
    return (
        statistics[statistics['count'] > 0]
        .sort_values(['count', 'draws_with_number', 'number'], ascending=[False, False, True])
        .head(limit)
        .reset_index(drop=True)
    )


def frequency_matrix(statistics: pd.DataFrame) -> pd.DataFrame:
    suffix_digits = int(statistics.attrs.get('suffix_digits', 2))
    if suffix_digits != 2:
        raise ValueError('Ma trận nhiệt chỉ áp dụng cho đuôi 2 số')
    matrix = statistics[['number', 'count']].copy()
    matrix['Đầu'] = matrix['number'].str[0]
    matrix['Đuôi'] = matrix['number'].str[1]
    return matrix.pivot(index='Đầu', columns='Đuôi', values='count').fillna(0).astype(int)
