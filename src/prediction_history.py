from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from vietlott_power655.calendar import next_draw_date as next_power655_draw_date
from vietlott_power655.prediction import MODEL_VERSION as POWER655_MODEL_VERSION
from vietlott_power655.prediction import generate_ticket_candidates
from xsmn.calendar import VIETNAM_TZ, next_regional_draw_date, stations_for_date
from xsmn.prediction import MODEL_VERSION as XSMN_MODEL_VERSION
from xsmn.prediction import generate_special_number_candidates

SCHEMA_VERSION = 1


def empty_history() -> dict[str, Any]:
    return {'schema_version': SCHEMA_VERSION, 'predictions': []}


def load_prediction_history(path: str | Path) -> dict[str, Any]:
    history_path = Path(path)
    if not history_path.exists():
        return empty_history()
    try:
        payload = json.loads(history_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, TypeError):
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


def _longest_matching_suffix(candidate: str, actual: str) -> int:
    for digits in range(min(len(candidate), len(actual)), 0, -1):
        if candidate[-digits:] == actual[-digits:]:
            return digits
    return 0


def _evaluate_xsmn_prediction(record: dict[str, Any], actual: str, evaluated_at: str) -> None:
    candidates = [str(candidate['value']) for candidate in record.get('candidates', [])]
    suffix_matches = [(_longest_matching_suffix(candidate, actual), candidate) for candidate in candidates]
    best_suffix_digits, best_candidate = max(suffix_matches, default=(0, ''))
    record['actual'] = {'special_number': actual, 'evaluated_at': evaluated_at}
    record['metrics'] = {
        'exact_hit': actual in candidates,
        'best_suffix_digits': best_suffix_digits,
        'best_candidate': best_candidate,
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
    xsmn_actual: dict[tuple[str, str], str] = {}
    if not xsmn_results.empty:
        special = xsmn_results[xsmn_results['prize_code'] == 'db'].copy()
        special['draw_date'] = pd.to_datetime(special['draw_date']).dt.date.astype(str)
        for row in special.itertuples(index=False):
            if len(str(row.number)) == 6:
                xsmn_actual[(str(row.draw_date), str(row.station_code))] = str(row.number)

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


def _append_xsmn_predictions(
    predictions: list[dict[str, Any]],
    existing_ids: set[str],
    results: pd.DataFrame,
    target_date: date,
    created_at: str,
) -> None:
    if results.empty:
        return
    for station_code in stations_for_date(target_date):
        station_history = results[
            (results['station_code'] == station_code) & (pd.to_datetime(results['draw_date']).dt.date < target_date)
        ].copy()
        draw_count = station_history[['draw_date', 'station_code']].drop_duplicates().shape[0]
        if draw_count < 1:
            continue
        recent_draws = min(30, draw_count)
        candidates = generate_special_number_candidates(
            station_history,
            station_code,
            target_date,
            candidate_count=5,
            recent_draws=recent_draws,
        )
        if candidates.empty:
            continue
        prediction_id = _prediction_id('xsmn', target_date, station_code, XSMN_MODEL_VERSION)
        if prediction_id in existing_ids:
            continue
        training_cutoff = pd.to_datetime(station_history['draw_date']).max().date().isoformat()
        predictions.append(
            {
                'id': prediction_id,
                'game': 'xsmn',
                'target_date': target_date.isoformat(),
                'station_code': station_code,
                'model_version': XSMN_MODEL_VERSION,
                'recent_draws': recent_draws,
                'training_cutoff': training_cutoff,
                'created_at': created_at,
                'candidates': [
                    {'rank': rank, 'value': str(row.number), 'model_score': float(row.model_score)}
                    for rank, row in enumerate(candidates.itertuples(index=False), start=1)
                ],
            }
        )
        existing_ids.add(prediction_id)


def _append_power655_prediction(
    predictions: list[dict[str, Any]],
    existing_ids: set[str],
    draws: pd.DataFrame,
    target_date: date,
    created_at: str,
) -> None:
    if draws.empty:
        return
    draw_count = int(draws['draw_id'].nunique())
    recent_draws = min(30, draw_count)
    prediction_id = _prediction_id('power655', target_date, '', POWER655_MODEL_VERSION)
    if prediction_id in existing_ids:
        return
    candidates = generate_ticket_candidates(
        draws,
        target_date,
        candidate_count=5,
        recent_draws=recent_draws,
    )
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
    existing_ids.add(prediction_id)


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
    _evaluate_predictions(predictions, xsmn_results, power655_draws, created_at)

    existing_ids = {str(record.get('id')) for record in predictions}
    _append_xsmn_predictions(
        predictions,
        existing_ids,
        xsmn_results,
        next_regional_draw_date(current),
        created_at,
    )
    _append_power655_prediction(
        predictions,
        existing_ids,
        power655_draws,
        next_power655_draw_date(current),
        created_at,
    )
    predictions.sort(key=lambda record: (record['target_date'], record['game'], record.get('station_code', '')))
    if history != original:
        save_prediction_history(path, history)
    return history


def prediction_performance_frame(history: dict[str, Any], game: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in history.get('predictions', []):
        if record.get('game') != game:
            continue
        actual = record.get('actual') or {}
        metrics = record.get('metrics') or {}
        candidates = ' | '.join(str(item.get('value', '')) for item in record.get('candidates', []))
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
                'best_main_matches': metrics.get('best_main_matches'),
                'best_candidate': metrics.get('best_candidate', ''),
                'model_version': record.get('model_version', ''),
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame['target_date'] = pd.to_datetime(frame['target_date'])
        frame = frame.sort_values(['target_date', 'station_code'], ascending=[False, True]).reset_index(drop=True)
    return frame
