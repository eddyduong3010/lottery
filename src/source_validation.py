"""Read-only source reconciliation. Agreement is data evidence, not prediction skill."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from xsmn.config import PRIZE_RULES_EFFECTIVE_FROM

HCM_URL = 'https://www.xskthcm.com/'
HCM_PRIZES = {
    'g8': (1, 2),
    'g7': (1, 3),
    'g6': (3, 4),
    'g5': (1, 4),
    'g4': (7, 5),
    'g3': (2, 5),
    'g2': (1, 5),
    'g1': (1, 5),
    'db': (1, 6),
}
HCM_LABELS = {
    'giải tám': 'g8',
    'giải bảy': 'g7',
    'giải sáu': 'g6',
    'giải năm': 'g5',
    'giải tư': 'g4',
    'giải ba': 'g3',
    'giải nhì': 'g2',
    'giải nhất': 'g1',
    'giải đặc biệt': 'db',
}


class SourceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceRecord:
    game: str
    draw_date: date
    source_url: str
    station_code: str = ''
    draw_id: str = ''
    prizes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    main_numbers: tuple[str, ...] = ()
    bonus_number: str = ''

    @property
    def key(self) -> tuple[str, ...]:
        if self.game == 'power655':
            # A changed date for one draw ID is a conflict, not two missing records.
            return self.game, str(int(self.draw_id))
        return self.game, self.draw_date.isoformat(), self.station_code

    def payload(self) -> dict[str, object]:
        return {
            'date': self.draw_date.isoformat(),
            'station_code': self.station_code,
            'prizes': {code: list(numbers) for code, numbers in self.prizes},
            'main_numbers': list(self.main_numbers),
            'bonus_number': self.bonus_number,
        }


def make_xsmn_record(
    draw_date: date,
    station_code: str,
    prizes: Mapping[str, Sequence[str]],
    source_url: str,
) -> SourceRecord:
    if set(prizes) != set(HCM_PRIZES):
        raise SourceValidationError('Missing or unexpected XSMN prize tiers')
    for code, (count, width) in HCM_PRIZES.items():
        values = prizes[code]
        # Some historical southern special prizes contained five digits.
        widths = {width, 5} if code == 'db' and draw_date < PRIZE_RULES_EFFECTIVE_FROM else {width}
        if len(values) != count or any(
            not isinstance(v, str) or not v.isascii() or not v.isdigit() or len(v) not in widths for v in values
        ):
            raise SourceValidationError(f'Invalid {code} count or digit width')
    return SourceRecord(
        game='xsmn',
        draw_date=draw_date,
        source_url=source_url,
        station_code=station_code,
        # Order within a prize is not relevant to matching; repeated values remain repeated.
        prizes=tuple(sorted((code, tuple(sorted(values))) for code, values in prizes.items())),
    )


def make_power_record(
    draw_id: str,
    draw_date: date,
    main_numbers: Sequence[str],
    bonus_number: str,
    source_url: str,
) -> SourceRecord:
    if not str(draw_id).isascii() or not str(draw_id).isdigit() or int(draw_id) < 1:
        raise SourceValidationError('Invalid Power draw ID')
    numbers = [*main_numbers, bonus_number]
    if (
        len(main_numbers) != 6
        or len(set(numbers)) != 7
        or any(not isinstance(n, str) or re.fullmatch(r'[0-9]{2}', n) is None or not 1 <= int(n) <= 55 for n in numbers)
    ):
        raise SourceValidationError('Power requires six unique mains and a different bonus in 01..55')
    return SourceRecord(
        game='power655',
        draw_date=draw_date,
        source_url=source_url,
        draw_id=str(int(draw_id)).zfill(5),
        main_numbers=tuple(sorted(main_numbers)),
        bonus_number=bonus_number,
    )


def power_records(draws: Iterable[object]) -> list[SourceRecord]:
    return [make_power_record(d.draw_id, d.draw_date, d.main_numbers, d.bonus_number, d.source_url) for d in draws]


def _date_from_text(text: str) -> date:
    match = re.search(r'(?<!\d)(\d{1,2})-(\d{1,2})-(\d{4})(?!\d)', text)
    if not match:
        raise SourceValidationError(f'Cannot parse official HCM date: {text}')
    day, month, year = map(int, match.groups())
    return date(year, month, day)


def parse_hcm_date_options(html: str) -> dict[date, str]:
    """IDs are discovered from the actual date menu, never guessed from dates."""
    soup = BeautifulSoup(html, 'lxml')
    options: dict[date, str] = {}
    for option in soup.select('#dkw-nav-kqxs-search option[value], #kqxs-search option[value]'):
        identifier = str(option.get('value', ''))
        if not identifier.isascii() or not identifier.isdigit():
            continue
        draw_date = _date_from_text(option.get_text(' ', strip=True))
        url = urljoin(HCM_URL, f'xskt?id={identifier}')
        if draw_date in options and options[draw_date] != url:
            raise SourceValidationError(f'Conflicting HCM date menu entries: {draw_date}')
        options[draw_date] = url
    if not options:
        raise SourceValidationError('Official HCM date menu is unavailable')
    return options


def parse_hcm_html(html: str, source_url: str = HCM_URL, expected_date: date | None = None) -> list[SourceRecord]:
    soup = BeautifulSoup(html, 'lxml')
    indexed: dict[tuple[str, ...], SourceRecord] = {}
    for table in soup.select('table.table-bordered'):
        heading = table.select_one('thead .date')
        if heading is None:
            continue
        draw_date = _date_from_text(heading.get_text(' ', strip=True))
        if expected_date is not None and draw_date != expected_date:
            raise SourceValidationError(f'Official HCM date {draw_date} differs from requested {expected_date}')
        prizes: dict[str, tuple[str, ...]] = {}
        for row in table.select('tbody > tr'):
            cells = row.find_all('td', recursive=False)
            if len(cells) != 2:
                continue
            label = ' '.join(cells[0].get_text(' ', strip=True).lower().split())
            # Home labels also contain a payout; detail labels do not.
            code = next((code for name, code in HCM_LABELS.items() if label.startswith(name)), None)
            if code is None:
                continue
            values = tuple(re.split(r'\s*-\s*', cells[1].get_text(' ', strip=True)))
            if code in prizes:
                raise SourceValidationError(f'Duplicate prize tier {code} in official HCM table')
            prizes[code] = values
        record = make_xsmn_record(draw_date, 'HCM', prizes, source_url)
        if record.key in indexed and indexed[record.key].payload() != record.payload():
            raise SourceValidationError(f'Conflicting duplicate official HCM draw {draw_date}')
        indexed[record.key] = record
    if not indexed:
        raise SourceValidationError('No complete official HCM result tables')
    return sorted(indexed.values(), key=lambda record: record.draw_date)


def _index(records: Sequence[SourceRecord], label: str) -> tuple[dict, list[dict], int, set]:
    grouped: dict[tuple[str, ...], list[SourceRecord]] = {}
    for record in records:
        grouped.setdefault(record.key, []).append(record)
    index, conflicts, duplicate_count, quarantined = {}, [], 0, set()
    for key, values in grouped.items():
        if any(value.payload() != values[0].payload() for value in values[1:]):
            quarantined.add(key)
            conflicts.append(
                {
                    'kind': 'duplicate_conflict',
                    'source': label,
                    'key': list(key),
                    'records': [{'source_url': value.source_url, **value.payload()} for value in values],
                }
            )
            continue
        duplicate_count += len(values) - 1
        index[key] = values
    return index, conflicts, duplicate_count, quarantined


def compare_sources(
    reference: Sequence[SourceRecord],
    candidate: Sequence[SourceRecord],
    reference_label: str = 'official',
    candidate_label: str = 'snapshot',
) -> dict[str, object]:
    left, conflicts, left_duplicates, left_bad = _index(reference, reference_label)
    right, right_conflicts, right_duplicates, right_bad = _index(candidate, candidate_label)
    conflicts.extend(right_conflicts)
    quarantined = left_bad | right_bad
    common = sorted((left.keys() & right.keys()) - quarantined)
    matches = []
    for key in common:
        first, second = left[key][0], right[key][0]
        evidence = {
            'key': list(key),
            'reference_urls': sorted({record.source_url for record in left[key]}),
            'candidate_urls': sorted({record.source_url for record in right[key]}),
        }
        if first.payload() == second.payload():
            matches.append(evidence)
        else:
            conflicts.append(
                {
                    'kind': 'source_mismatch',
                    **evidence,
                    'reference': first.payload(),
                    'candidate': second.payload(),
                    'different_fields': [
                        name for name in first.payload() if first.payload()[name] != second.payload()[name]
                    ],
                }
            )
    return {
        'status': 'conflict' if conflicts else 'verified_sample' if common else 'not_verified',
        'reference_label': reference_label,
        'candidate_label': candidate_label,
        'compared': len(common),
        'matched': len(matches),
        'conflicts': conflicts,
        'matches': matches,
        'coverage': {
            'reference_records': len(reference),
            'candidate_records': len(candidate),
            'reference_unique_valid': len(left),
            'candidate_unique_valid': len(right),
            'reference_exact_duplicates': left_duplicates,
            'candidate_exact_duplicates': right_duplicates,
            'quarantined_keys': [list(key) for key in sorted(quarantined)],
            'reference_compared_fraction': len(common) / len(left) if left else None,
            'candidate_compared_fraction': len(common) / len(right) if right else None,
            'missing_in_candidate': [list(key) for key in sorted(left.keys() - right.keys() - quarantined)],
            'missing_in_reference': [list(key) for key in sorted(right.keys() - left.keys() - quarantined)],
        },
        'scope_note': 'Agreement covers only compared records. It does not establish full-history quality or forecast skill.',
    }


def _open_snapshot(path: Path) -> sqlite3.Connection:
    # No repository constructor: constructors can create/migrate databases.
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def snapshot_summary(path: Path, game: str, as_of: date) -> dict[str, object]:
    table = {'xsmn': 'draws', 'power655': 'power655_draws'}[game]
    with closing(_open_snapshot(path)) as connection:
        count, earliest, latest = connection.execute(
            f'SELECT COUNT(*), MIN(draw_date), MAX(draw_date) FROM {table}'
        ).fetchone()
    return {
        'path': str(path.resolve()),
        'records': count,
        'earliest': earliest,
        'latest': latest,
        'age_days': (as_of - date.fromisoformat(latest)).days if latest else None,
        'as_of': as_of.isoformat(),
    }


def read_xsmn_snapshot(path: Path, limit: int = 6, station_code: str = 'HCM') -> list[SourceRecord]:
    if not 1 <= limit <= 20:
        raise ValueError('Sample size must be between 1 and 20')
    with closing(_open_snapshot(path)) as connection:
        rows = connection.execute(
            'SELECT id, draw_date, station_code, source_url FROM draws WHERE station_code=? '
            'ORDER BY draw_date DESC LIMIT ?',
            (station_code, limit),
        ).fetchall()
        records = []
        for identifier, draw_date, station, url in rows:
            prizes: dict[str, list[str]] = {}
            for code, number in connection.execute(
                'SELECT prize_code, number FROM prize_results WHERE draw_id=? ORDER BY prize_code, ordinal',
                (identifier,),
            ):
                prizes.setdefault(code, []).append(number)
            records.append(make_xsmn_record(date.fromisoformat(draw_date), station, prizes, url))
        return records


def read_power_snapshot(path: Path, limit: int = 6) -> list[SourceRecord]:
    if not 1 <= limit <= 20:
        raise ValueError('Sample size must be between 1 and 20')
    with closing(_open_snapshot(path)) as connection:
        rows = connection.execute(
            'SELECT draw_id, draw_date, main_numbers, bonus_number, source_url FROM power655_draws '
            'ORDER BY draw_date DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [make_power_record(i, date.fromisoformat(d), n.split(), b, u) for i, d, n, b, u in rows]


def audit_power_ledger(ledger_path: Path, power_db_path: Path) -> dict[str, object]:
    """Compare recorded actuals against an explicitly supplied read-only snapshot.

    The ledger often lacks draw IDs. Such rows are joined by date, with the ID
    inference recorded in the report. A missing reference is never a match.
    """
    ledger_bytes = ledger_path.read_bytes()
    ledger = json.loads(ledger_bytes.decode('utf-8'))
    if not isinstance(ledger, dict) or not isinstance(ledger.get('predictions'), list):
        raise SourceValidationError('Ledger must contain a predictions list')
    entries = [entry for entry in ledger['predictions'] if isinstance(entry, dict) and entry.get('game') == 'power655']
    reference, candidate, unverified, pending, ledger_rows, id_conflicts = {}, [], [], [], [], []
    with closing(_open_snapshot(power_db_path)) as connection:
        for position, entry in enumerate(entries):
            identifier = str(entry.get('id', f'power-entry-{position}'))
            pointer = f'{ledger_path.resolve()}#predictions/{identifier}'
            actual = entry.get('actual')
            if actual is None:
                pending.append(identifier)
                continue
            try:
                if not isinstance(actual, dict):
                    raise SourceValidationError('Actual result must be an object')
                target_date = date.fromisoformat(entry.get('target_date', ''))
                rows = connection.execute(
                    'SELECT draw_id, draw_date, main_numbers, bonus_number, source_url FROM power655_draws '
                    'WHERE draw_date=?',
                    (target_date.isoformat(),),
                ).fetchall()
                if len(rows) != 1:
                    unverified.append(
                        {
                            'ledger_id': identifier,
                            'date': str(target_date),
                            'reason': 'missing_reference' if not rows else 'ambiguous_reference_date',
                            'reference_rows': len(rows),
                        }
                    )
                    continue
                draw_id, draw_date, numbers, bonus, url = rows[0]
                baseline = make_power_record(draw_id, date.fromisoformat(draw_date), numbers.split(), bonus, url)
                reference[baseline.key] = baseline
                mains = actual.get('main_numbers')
                if isinstance(mains, str):
                    mains = mains.split()
                if not isinstance(mains, (list, tuple)):
                    raise SourceValidationError('Actual main_numbers must be six strings')
                recorded_id = actual.get('draw_id') or entry.get('draw_id')
                if recorded_id is not None and str(recorded_id).lstrip('0') != baseline.draw_id.lstrip('0'):
                    id_conflicts.append(
                        {
                            'kind': 'source_mismatch',
                            'key': list(baseline.key),
                            'different_fields': ['draw_id'],
                            'reference_urls': [url],
                            'candidate_urls': [pointer],
                            'reference': {'draw_id': baseline.draw_id},
                            'candidate': {'draw_id': recorded_id},
                        }
                    )
                observed = make_power_record(
                    baseline.draw_id,
                    target_date,
                    mains,
                    actual.get('bonus_number'),
                    pointer,
                )
                candidate.append(observed)
                ledger_rows.append(
                    {
                        'ledger_id': identifier,
                        'date': str(target_date),
                        'draw_id': baseline.draw_id,
                        'draw_id_inferred_by_date': recorded_id is None,
                        'reference_source_url': url,
                        'ledger_pointer': pointer,
                        'actual_source_url': actual.get('source_url'),
                    }
                )
            except (TypeError, ValueError) as exc:
                unverified.append({'ledger_id': identifier, 'reason': 'invalid_record', 'error': str(exc)})
    report = compare_sources(
        list(reference.values()), candidate, 'provided Power snapshot', 'prediction ledger actuals'
    )
    report['conflicts'].extend(id_conflicts)
    id_conflict_keys = {tuple(conflict['key']) for conflict in id_conflicts}
    report['matches'] = [match for match in report['matches'] if tuple(match['key']) not in id_conflict_keys]
    report['matched'] = len(report['matches'])
    if report['conflicts']:
        report['status'] = 'conflict'
    elif unverified and report['compared']:
        report['status'] = 'partial_verified'
    report.update(
        {
            'ledger_path': str(ledger_path.resolve()),
            'reference_database': str(power_db_path.resolve()),
            'ledger_sha256': hashlib.sha256(ledger_bytes).hexdigest(),
            'reference_database_sha256': hashlib.sha256(power_db_path.read_bytes()).hexdigest(),
            'reference_verification': 'Provided snapshot only; no live request is made by this ledger comparison',
            'ledger_entries': ledger_rows,
            'unverified_entries': unverified,
            'pending_entries': pending,
        }
    )
    report['coverage']['ledger_power_entries'] = len(entries)
    report['coverage']['ledger_evaluated_entries'] = len(entries) - len(pending)
    report['coverage']['ledger_pending_entries'] = len(pending)
    report['scope_note'] += (
        ' Existing ledger outcomes mean a later snapshot comparison is supplementary historical evaluation, '
        'not guaranteed previously unseen holdout evidence.'
    )
    return report
