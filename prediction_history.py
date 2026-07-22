from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime
from math import comb
from pathlib import Path
from typing import Any

import pandas as pd

from vietlott_power655.calendar import next_draw_date as next_power655_draw_date
from vietlott_power655.prediction import MODEL_VERSION as POWER655_MODEL_VERSION
from vietlott_power655.prediction import generate_ticket_candidates
from xsmn.calendar import VIETNAM_TZ
from xsmn.calendar import next_draw_date as next_xsmn_draw_date
from xsmn.config import STATIONS
from xsmn.prediction import FULL_DRAW_MODEL_VERSION, generate_full_draw_prediction

SCHEMA_VERSION = 1


def empty_history() -> dict[str, Any]:
    return {'schema_version': SCHEMA_VERSION, 'predictions': []}


def load_prediction_history(path: str | Path) -> dict[str, Any]:
    history_path = Path(path)
    if not history_path.exists():
        return empty_history()
    try:
        payload = json.loads(history_path.read_text(encoding='utf-8'))
    except OSError, json.JSONDecodeError, TypeError:
        return empty_history()
    if payload.get('schema_version') != SCHEMA_VERSION or not isinstance(payload.get('predictions'), list):
        return empty_history()
    return payload


def save_prediction_history(path: str | Path, history: dict[str, Any]) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = history_path.with_suffix(history_path.suffix + '.tmp')
    temporary_path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    temporary_path.replace(history_path)


def _prediction_id(game: str, target_date: date, station_code: str, model_version: str) -> str:
    return ':'.join((game, target_date.isoformat(), station_code or '-', model_version))


def _prediction_key(game: str, target_date: date | str, station_code: str) -> tuple[str, str, str]:
    target_value = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
    return game, target_value, station_code


def _longest_matching_suffix(candidate: str, actual: str) -> int:
    for digits in range(min(len(candidate), len(actual)), 0, -1):
        if candidate[-digits:] == actual[-digits:]:
            return digits
    return 0


def _evaluate_xsmn_prediction(record: dict[str, Any], actual_results: dict[str, list[str]], evaluated_at: str) -> None:
    actual = next((number for number in actual_results.get('db', []) if len(number) == 6), '')
    if not actual:
        return
    candidates = [str(candidate['value']) for candidate in record.get('candidates', [])]
    suffix_matches = [(_longest_matching_suffix(candidate, actual), candidate) for candidate in candidates]
    best_suffix_digits, best_candidate = max(suffix_matches, default=(0, ''))
    prize_predictions = record.get('prize_predictions', [])
    prize_hits = [
        prediction
        for prediction in prize_predictions
        if str(prediction.get('number', '')) in actual_results.get(str(prediction.get('prize_code', '')), [])
    ]
    record['actual'] = {
        'special_number': actual,
        'prize_results': actual_results,
        'evaluated_at': evaluated_at,
    }
    record['metrics'] = {
        'exact_hit': actual in candidates,
        'best_suffix_digits': best_suffix_digits,
        'best_candidate': best_candidate,
        'prize_hits': len(prize_hits),
        'prize_total': len(prize_predictions),
        'prize_hit_rate': len(prize_hits) / len(prize_predictions) if prize_predictions else 0.0,
        'hit_prize_codes': sorted({str(prediction['prize_code']) for prediction in prize_hits}),
    }


def _evaluate_power655_prediction(
    record: dict[str, Any], main_numbers: str, bonus_number: str, evaluated_at: str
) -> None:
    actual_main = set(main_numbers.split())
    scored_candidates = []
    for candidate in record.get('candidates', []):
        value = str(candidate['value'])
        selected = set(value.split())
        scored_candidates.append((len(selected & actual_main), bonus_number in selected, value))
    best_main_matches, bonus_hit, best_candidate = max(scored_candidates, default=(0, False, ''))
    record['actual'] = {
        'main_numbers': main_numbers,
        'bonus_number': bonus_number,
        'evaluated_at': evaluated_at,
    }
    record['metrics'] = {
        'exact_hit': best_main_matches == 6,
        'best_main_matches': best_main_matches,
        'bonus_in_best_candidate': bonus_hit,
        'best_candidate': best_candidate,
    }


def _evaluate_predictions(
    predictions: list[dict[str, Any]],
    xsmn_results: pd.DataFrame,
    power655_draws: pd.DataFrame,
    evaluated_at: str,
) -> None:
    xsmn_actual: dict[tuple[str, str], dict[str, list[str]]] = {}
    if not xsmn_results.empty:
        work = xsmn_results.copy()
        work['draw_date'] = pd.to_datetime(work['draw_date']).dt.date.astype(str)
        for row in work.itertuples(index=False):
            key = (str(row.draw_date), str(row.station_code))
            prize_results = xsmn_actual.setdefault(key, {})
            prize_results.setdefault(str(row.prize_code), []).append(str(row.number))

    power_actual: dict[str, tuple[str, str]] = {}
    if not power655_draws.empty:
        work = power655_draws.copy()
        work['draw_date'] = pd.to_datetime(work['draw_date']).dt.date.astype(str)
        for row in work.itertuples(index=False):
            power_actual[str(row.draw_date)] = (str(row.main_numbers), str(row.bonus_number))

    for record in predictions:
        target_date = str(record.get('target_date', ''))
        if record.get('game') == 'xsmn':
            actual = xsmn_actual.get((target_date, str(record.get('station_code', ''))))
            if actual:
                _evaluate_xsmn_prediction(record, actual, evaluated_at)
        elif record.get('game') == 'power655':
            actual = power_actual.get(target_date)
            if actual:
                _evaluate_power655_prediction(record, actual[0], actual[1], evaluated_at)


def _lock_one_candidate_per_draw(predictions: list[dict[str, Any]]) -> None:
    for record in predictions:
        if record.get('actual'):
            continue
        candidates = record.get('candidates', [])
        if candidates:
            record['candidates'] = [min(candidates, key=lambda candidate: int(candidate.get('rank', 9999)))]


def _full_prediction_payload(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            'prize_code': str(row.prize_code),
            'ordinal': int(row.ordinal),
            'number': str(row.number),
            'model_score': float(row.model_score),
        }
        for row in frame.itertuples(index=False)
    ]


def _ensure_xsmn_full_predictions(predictions: list[dict[str, Any]], results: pd.DataFrame) -> None:
    for record in predictions:
        if record.get('game') != 'xsmn':
            continue
        locked_candidate = next(
            (candidate for candidate in record.get('candidates', []) if candidate.get('value')),
            None,
        )
        if record.get('prize_predictions'):
            if locked_candidate:
                for prediction in record['prize_predictions']:
                    if prediction.get('prize_code') == 'db':
                        prediction['number'] = str(locked_candidate['value'])
                        prediction['model_score'] = float(locked_candidate.get('model_score', 0.0))
            continue
        target_date = date.fromisoformat(str(record['target_date']))
        station_code = str(record['station_code'])
        recent_draws = max(int(record.get('recent_draws', 30)), 1)
        station_history = results[
            (results['station_code'] == station_code) & (pd.to_datetime(results['draw_date']).dt.date < target_date)
        ].copy()
        full_prediction = generate_full_draw_prediction(
            station_history,
            station_code,
            target_date,
            recent_draws=recent_draws,
        )
        if full_prediction.empty:
            continue
        if locked_candidate:
            special_mask = full_prediction['prize_code'] == 'db'
            full_prediction.loc[special_mask, 'number'] = str(locked_candidate['value'])
            full_prediction.loc[special_mask, 'model_score'] = float(locked_candidate.get('model_score', 0.0))
        record['prize_predictions'] = _full_prediction_payload(full_prediction)
        record['prize_model_version'] = FULL_DRAW_MODEL_VERSION


def _append_xsmn_predictions(
    predictions: list[dict[str, Any]],
    locked_keys: set[tuple[str, str, str]],
    results: pd.DataFrame,
    target_date: date,
    station_codes: list[str],
    created_at: str,
) -> None:
    if results.empty:
        return
    for station_code in station_codes:
        prediction_key = _prediction_key('xsmn', target_date, station_code)
        if prediction_key in locked_keys:
            continue
        station_history = results[
            (results['station_code'] == station_code) & (pd.to_datetime(results['draw_date']).dt.date < target_date)
        ].copy()
        draw_count = station_history[['draw_date', 'station_code']].drop_duplicates().shape[0]
        if draw_count < 1:
            continue
        recent_draws = min(30, draw_count)
        full_prediction = generate_full_draw_prediction(
            station_history,
            station_code,
            target_date,
            recent_draws=recent_draws,
        )
        if full_prediction.empty:
            continue
        special = full_prediction[full_prediction['prize_code'] == 'db'].head(1)
        if special.empty:
            continue
        prediction_id = _prediction_id('xsmn', target_date, station_code, FULL_DRAW_MODEL_VERSION)
        training_cutoff = pd.to_datetime(station_history['draw_date']).max().date().isoformat()
        predictions.append(
            {
                'id': prediction_id,
                'game': 'xsmn',
                'target_date': target_date.isoformat(),
                'station_code': station_code,
                'model_version': FULL_DRAW_MODEL_VERSION,
                'recent_draws': recent_draws,
                'training_cutoff': training_cutoff,
                'created_at': created_at,
                'candidates': [
                    {'rank': rank, 'value': str(row.number), 'model_score': float(row.model_score)}
                    for rank, row in enumerate(special.itertuples(index=False), start=1)
                ],
                'prize_predictions': _full_prediction_payload(full_prediction),
                'prize_model_version': FULL_DRAW_MODEL_VERSION,
            }
        )
        locked_keys.add(prediction_key)


def _append_power655_prediction(
    predictions: list[dict[str, Any]],
    locked_keys: set[tuple[str, str, str]],
    draws: pd.DataFrame,
    target_date: date,
    created_at: str,
) -> None:
    if draws.empty:
        return
    prediction_key = _prediction_key('power655', target_date, '')
    if prediction_key in locked_keys:
        return
    draw_count = int(draws['draw_id'].nunique())
    recent_draws = min(30, draw_count)
    prediction_id = _prediction_id('power655', target_date, '', POWER655_MODEL_VERSION)
    candidates = generate_ticket_candidates(
        draws,
        target_date,
        candidate_count=5,
        recent_draws=recent_draws,
    ).head(1)
    if candidates.empty:
        return
    predictions.append(
        {
            'id': prediction_id,
            'game': 'power655',
            'target_date': target_date.isoformat(),
            'station_code': '',
            'model_version': POWER655_MODEL_VERSION,
            'recent_draws': recent_draws,
            'training_cutoff': pd.to_datetime(draws['draw_date']).max().date().isoformat(),
            'created_at': created_at,
            'candidates': [
                {'rank': rank, 'value': str(row.numbers), 'model_score': float(row.model_score)}
                for rank, row in enumerate(candidates.itertuples(index=False), start=1)
            ],
        }
    )
    locked_keys.add(prediction_key)


def update_prediction_history(
    path: str | Path,
    xsmn_results: pd.DataFrame,
    power655_draws: pd.DataFrame,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now.astimezone(VIETNAM_TZ) if now else datetime.now(VIETNAM_TZ)
    created_at = current.isoformat()
    original = load_prediction_history(path)
    history = deepcopy(original)
    predictions = history['predictions']
    _lock_one_candidate_per_draw(predictions)
    _ensure_xsmn_full_predictions(predictions, xsmn_results)
    _evaluate_predictions(predictions, xsmn_results, power655_draws, created_at)

    locked_keys = {
        _prediction_key(
            str(record.get('game', '')),
            str(record.get('target_date', '')),
            str(record.get('station_code', '')),
        )
        for record in predictions
    }
    stations_by_target: dict[date, list[str]] = {}
    for station_code in STATIONS:
        target_date = next_xsmn_draw_date(station_code, current)
        stations_by_target.setdefault(target_date, []).append(station_code)
    for target_date, station_codes in sorted(stations_by_target.items()):
        _append_xsmn_predictions(
            predictions,
            locked_keys,
            xsmn_results,
            target_date,
            station_codes,
            created_at,
        )
    _append_power655_prediction(
        predictions,
        locked_keys,
        power655_draws,
        next_power655_draw_date(current),
        created_at,
    )
    predictions.sort(key=lambda record: (record['target_date'], record['game'], record.get('station_code', '')))
    if history != original:
        save_prediction_history(path, history)
    return history


def saved_prediction_candidates(
    history: dict[str, Any], game: str, target_date: date, station_code: str = ''
) -> pd.DataFrame:
    matching = [
        record
        for record in history.get('predictions', [])
        if _prediction_key(
            str(record.get('game', '')),
            str(record.get('target_date', '')),
            str(record.get('station_code', '')),
        )
        == _prediction_key(game, target_date, station_code)
    ]
    if not matching:
        return pd.DataFrame(columns=['rank', 'value', 'model_score'])
    locked = min(matching, key=lambda record: str(record.get('created_at', '')))
    frame = pd.DataFrame(locked.get('candidates', []), columns=['rank', 'value', 'model_score'])
    if frame.empty:
        return frame
    return frame.sort_values('rank').reset_index(drop=True)


def saved_xsmn_prize_predictions(history: dict[str, Any], target_date: date, station_code: str) -> pd.DataFrame:
    matching = [
        record
        for record in history.get('predictions', [])
        if _prediction_key(
            str(record.get('game', '')),
            str(record.get('target_date', '')),
            str(record.get('station_code', '')),
        )
        == _prediction_key('xsmn', target_date, station_code)
    ]
    if not matching:
        return pd.DataFrame(columns=['prize_code', 'ordinal', 'number', 'model_score'])
    locked = min(matching, key=lambda record: str(record.get('created_at', '')))
    frame = pd.DataFrame(
        locked.get('prize_predictions', []),
        columns=['prize_code', 'ordinal', 'number', 'model_score'],
    )
    if frame.empty:
        return frame
    return frame.sort_values(['prize_code', 'ordinal']).reset_index(drop=True)


def prediction_performance_frame(history: dict[str, Any], game: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in history.get('predictions', []):
        if record.get('game') != game:
            continue
        actual = record.get('actual') or {}
        metrics = record.get('metrics') or {}
        candidates = ' | '.join(str(item.get('value', '')) for item in record.get('candidates', []))
        actual_main_numbers = str(actual.get('main_numbers', '')).split()
        best_power_candidate = str(metrics.get('best_candidate', '')).split()
        matched_main_numbers = [number for number in actual_main_numbers if number in best_power_candidate]
        matched_prize_predictions = _matched_xsmn_prize_predictions(record)
        rows.append(
            {
                'target_date': record.get('target_date'),
                'station_code': record.get('station_code', ''),
                'candidates': candidates,
                'actual': actual.get('special_number')
                or ' '.join(filter(None, (actual.get('main_numbers'), actual.get('bonus_number')))),
                'evaluated': bool(actual),
                'exact_hit': bool(metrics.get('exact_hit', False)),
                'best_suffix_digits': metrics.get('best_suffix_digits'),
                'prize_hits': metrics.get('prize_hits'),
                'prize_total': metrics.get('prize_total'),
                'prize_hit_rate': metrics.get('prize_hit_rate'),
                'best_main_matches': metrics.get('best_main_matches'),
                'best_candidate': metrics.get('best_candidate', ''),
                'matched_numbers': ' '.join(matched_main_numbers),
                'matched_prizes': ' · '.join(
                    f'{match["prize_code"].upper()} {match["number"]}' for match in matched_prize_predictions
                ),
                'model_version': record.get('model_version', ''),
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame['target_date'] = pd.to_datetime(frame['target_date'])
        frame = frame.sort_values(['target_date', 'station_code'], ascending=[False, True]).reset_index(drop=True)
    return frame


def _matched_xsmn_prize_predictions(record: dict[str, Any]) -> list[dict[str, Any]]:
    actual_results = (record.get('actual') or {}).get('prize_results') or {}
    matches: list[dict[str, Any]] = []
    for prediction in record.get('prize_predictions', []):
        prize_code = str(prediction.get('prize_code', ''))
        number = str(prediction.get('number', ''))
        if number and number in {str(value) for value in actual_results.get(prize_code, [])}:
            matches.append(
                {
                    'prize_code': prize_code,
                    'ordinal': int(prediction.get('ordinal', 0)),
                    'number': number,
                }
            )
    return matches


def prediction_comparison_rows(history: dict[str, Any], game: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent evaluated predictions in a UI-friendly, token-level shape."""

    if limit < 1:
        return []
    evaluated = [
        record for record in history.get('predictions', []) if record.get('game') == game and record.get('actual')
    ]
    evaluated.sort(
        key=lambda record: (str(record.get('target_date', '')), str(record.get('station_code', ''))),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for record in evaluated[:limit]:
        actual = record.get('actual') or {}
        metrics = record.get('metrics') or {}
        if game == 'xsmn':
            predicted_number = str(metrics.get('best_candidate', ''))
            if not predicted_number:
                predicted_number = next(
                    (str(candidate.get('value', '')) for candidate in record.get('candidates', [])),
                    '',
                )
            rows.append(
                {
                    'target_date': str(record.get('target_date', '')),
                    'station_code': str(record.get('station_code', '')),
                    'predicted_numbers': [predicted_number] if predicted_number else [],
                    'actual_main_numbers': [str(actual.get('special_number', ''))]
                    if actual.get('special_number')
                    else [],
                    'actual_bonus_number': '',
                    'matched_main_numbers': [str(actual.get('special_number', ''))] if metrics.get('exact_hit') else [],
                    'matched_bonus': False,
                    'matching_suffix_digits': int(metrics.get('best_suffix_digits') or 0),
                    'prize_matches': _matched_xsmn_prize_predictions(record),
                    'model_version': str(record.get('model_version', '')),
                }
            )
            continue

        predicted_numbers = str(metrics.get('best_candidate', '')).split()
        if not predicted_numbers:
            predicted_numbers = next(
                (str(candidate.get('value', '')).split() for candidate in record.get('candidates', [])),
                [],
            )
        actual_main_numbers = str(actual.get('main_numbers', '')).split()
        matched_main_numbers = [number for number in actual_main_numbers if number in predicted_numbers]
        actual_bonus_number = str(actual.get('bonus_number', ''))
        rows.append(
            {
                'target_date': str(record.get('target_date', '')),
                'station_code': '',
                'predicted_numbers': predicted_numbers,
                'actual_main_numbers': actual_main_numbers,
                'actual_bonus_number': actual_bonus_number,
                'matched_main_numbers': matched_main_numbers,
                'matched_bonus': bool(actual_bonus_number and actual_bonus_number in predicted_numbers),
                'matching_suffix_digits': 0,
                'prize_matches': [],
                'model_version': str(record.get('model_version', '')),
            }
        )
    return rows


def _xsmn_random_expected_prize_hits(record: dict[str, Any]) -> float:
    actual_results = (record.get('actual') or {}).get('prize_results') or {}
    expected = 0.0
    for prediction in record.get('prize_predictions', []):
        prize_code = str(prediction.get('prize_code', ''))
        number = str(prediction.get('number', ''))
        actual_count = len(actual_results.get(prize_code, []))
        if number and actual_count:
            expected += actual_count / (10 ** len(number))
    return expected


def _power_at_least_two_probability(ticket_size: int) -> float:
    denominator = comb(55, ticket_size)
    probability = 0.0
    for matched in range(2, min(6, ticket_size) + 1):
        probability += comb(6, matched) * comb(49, ticket_size - matched) / denominator
    return probability


def prediction_performance_summary(history: dict[str, Any], game: str) -> dict[str, float | int]:
    """Summarize observed performance next to a fair-draw mathematical baseline."""

    records = [record for record in history.get('predictions', []) if record.get('game') == game]
    evaluated = [record for record in records if record.get('actual')]
    exact_hits = sum(bool((record.get('metrics') or {}).get('exact_hit')) for record in evaluated)
    summary: dict[str, float | int] = {
        'saved_count': len(records),
        'evaluated_count': len(evaluated),
        'exact_hits': exact_hits,
        'exact_rate': exact_hits / len(evaluated) if evaluated else 0.0,
    }
    if game == 'xsmn':
        prize_hits = [float((record.get('metrics') or {}).get('prize_hits') or 0) for record in evaluated]
        suffix_two_hits = sum(
            int((record.get('metrics') or {}).get('best_suffix_digits') or 0) >= 2 for record in evaluated
        )
        suffix_two_baselines = []
        for record in evaluated:
            candidate_count = len(record.get('candidates', []))
            suffix_two_baselines.append(1 - (1 - 0.01) ** candidate_count)
        summary.update(
            average_prize_hits=sum(prize_hits) / len(prize_hits) if prize_hits else 0.0,
            random_expected_prize_hits=sum(_xsmn_random_expected_prize_hits(record) for record in evaluated)
            / len(evaluated)
            if evaluated
            else 0.0,
            suffix_two_rate=suffix_two_hits / len(evaluated) if evaluated else 0.0,
            random_suffix_two_rate=sum(suffix_two_baselines) / len(suffix_two_baselines)
            if suffix_two_baselines
            else 0.0,
        )
        return summary

    main_matches = [float((record.get('metrics') or {}).get('best_main_matches') or 0) for record in evaluated]
    at_least_two = sum(value >= 2 for value in main_matches)
    ticket_sizes = []
    candidate_counts = []
    for record in evaluated:
        candidates = record.get('candidates', [])
        candidate_counts.append(len(candidates))
        ticket_sizes.append(len(str(candidates[0].get('value', '')).split()) if candidates else 0)
    summary.update(
        average_main_matches=sum(main_matches) / len(main_matches) if main_matches else 0.0,
        random_expected_main_matches=sum(ticket_size * 6 / 55 for ticket_size in ticket_sizes) / len(ticket_sizes)
        if ticket_sizes
        else 0.0,
        at_least_two_rate=at_least_two / len(evaluated) if evaluated else 0.0,
        random_at_least_two_rate=sum(_power_at_least_two_probability(ticket_size) for ticket_size in ticket_sizes)
        / len(ticket_sizes)
        if ticket_sizes
        else 0.0,
        random_exact_rate=sum(candidate_counts) / (len(evaluated) * comb(55, 6)) if evaluated else 0.0,
    )
    return summary
