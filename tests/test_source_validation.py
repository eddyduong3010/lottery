from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

import pytest

from source_validation import (
    HCM_PRIZES,
    SourceValidationError,
    audit_power_ledger,
    compare_sources,
    make_power_record,
    make_xsmn_record,
    parse_hcm_date_options,
    parse_hcm_html,
    read_power_snapshot,
)

LABELS = [
    'Giải tám',
    'Giải bảy',
    'Giải sáu',
    'Giải năm',
    'Giải tư',
    'Giải ba',
    'Giải nhì',
    'Giải nhất',
    'Giải đặc biệt',
]


def prize_values():
    return {code: tuple(str(i).zfill(width) for i in range(count)) for code, (count, width) in HCM_PRIZES.items()}


def official_table(values=None, date_text='Thứ Bảy, ngày 11-7-2026'):
    values = values or prize_values()
    rows = ''.join(
        f'<tr><td>{label} 100.000đ</td><td>{" - ".join(values[code])}</td></tr>'
        for label, code in zip(LABELS, HCM_PRIZES, strict=True)
    )
    return f'<table class="table table-bordered"><thead><tr><th><span class="date">{date_text}</span></th></tr></thead><tbody>{rows}</tbody></table>'


def power(draw_id='01370', draw_date=date(2026, 7, 11), bonus='40', mains=('09', '17', '20', '33', '41', '42')):
    return make_power_record(draw_id, draw_date, mains, bonus, 'https://example.test/result')


def test_hcm_parser_keeps_zeroes_and_accepts_exact_duplicate_layout():
    table = official_table()
    draws = parse_hcm_html(table + table, expected_date=date(2026, 7, 11))
    assert len(draws) == 1
    assert dict(draws[0].prizes)['g8'] == ('00',)
    assert dict(draws[0].prizes)['db'] == ('000000',)


def test_hcm_parser_rejects_duplicate_layout_with_conflicting_result():
    with pytest.raises(SourceValidationError, match='Conflicting duplicate'):
        parse_hcm_html(official_table() + official_table().replace('000000', '123456'))


def test_hcm_parser_rejects_wrong_date_and_missing_result():
    with pytest.raises(SourceValidationError, match='differs from requested'):
        parse_hcm_html(official_table(), expected_date=date(2026, 7, 10))
    with pytest.raises(SourceValidationError, match='count or digit width'):
        parse_hcm_html(official_table().replace('000000', '00000'))


def test_hcm_date_menu_uses_observed_ids_and_flexible_day_month():
    html = '<select id="dkw-nav-kqxs-search"><option value="">- Kết quả -</option><option value="1191">11-7-2026</option><option value="1189">04-07-2026</option></select>'
    options = parse_hcm_date_options(html)
    assert options[date(2026, 7, 11)] == 'https://www.xskthcm.com/xskt?id=1191'
    assert options[date(2026, 7, 4)].endswith('id=1189')


def test_prize_comparison_ignores_order_but_not_multiplicity():
    values = prize_values()
    left = make_xsmn_record(date(2026, 7, 11), 'HCM', values, 'official')
    reordered = {code: tuple(reversed(numbers)) for code, numbers in values.items()}
    right = make_xsmn_record(date(2026, 7, 11), 'HCM', reordered, 'secondary')
    assert compare_sources([left], [right])['matched'] == 1
    reordered['g6'] = ('0000', '0000', '0002')
    changed = make_xsmn_record(date(2026, 7, 11), 'HCM', reordered, 'secondary')
    result = compare_sources([left], [changed])
    assert result['status'] == 'conflict'
    assert result['conflicts'][0]['different_fields'] == ['prizes']


def test_power_normalizes_draw_id_and_main_order_only():
    result = compare_sources([power()], [power(draw_id='1370', mains=('42', '41', '33', '20', '17', '09'))])
    assert result['compared'] == result['matched'] == 1
    assert result['status'] == 'verified_sample'


@pytest.mark.parametrize(
    'changed,field',
    [
        (power(draw_date=date(2026, 7, 10)), 'date'),
        (power(bonus='39'), 'bonus_number'),
        (power(mains=('08', '17', '20', '33', '41', '42')), 'main_numbers'),
    ],
)
def test_power_date_main_and_bonus_mismatches_are_conflicts(changed, field):
    result = compare_sources([power()], [changed])
    assert result['compared'] == 1 and result['matched'] == 0
    assert result['conflicts'][0]['different_fields'] == [field]


def test_duplicate_records_are_counted_once_and_conflicting_keys_quarantined():
    result = compare_sources([power(), power()], [power()])
    assert result['compared'] == result['matched'] == 1
    assert result['coverage']['reference_exact_duplicates'] == 1
    result = compare_sources([power(), power(bonus='39')], [power()])
    assert result['compared'] == result['matched'] == 0
    assert result['status'] == 'conflict'
    assert result['coverage']['quarantined_keys'] == [['power655', '1370']]


def test_missing_or_empty_sources_are_not_verified():
    result = compare_sources([power()], [])
    assert result['status'] == 'not_verified'
    assert result['matched'] == 0
    assert result['coverage']['missing_in_candidate'] == [['power655', '1370']]


def test_power_rejects_duplicate_bonus_and_unpadded_values():
    with pytest.raises(SourceValidationError):
        power(bonus='09')
    with pytest.raises(SourceValidationError):
        power(mains=('9', '17', '20', '33', '41', '42'))


def test_snapshot_reader_does_not_modify_sqlite(tmp_path):
    database = tmp_path / 'power.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE power655_draws (draw_id TEXT, draw_date TEXT, main_numbers TEXT, bonus_number TEXT, source_url TEXT)'
        )
        connection.execute(
            'INSERT INTO power655_draws VALUES (?,?,?,?,?)',
            ('01370', '2026-07-11', '09 17 20 33 41 42', '40', 'official'),
        )
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assert read_power_snapshot(database)[0].main_numbers[0] == '09'
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with pytest.raises(sqlite3.OperationalError):
        read_power_snapshot(tmp_path / 'missing.sqlite3')
    assert not (tmp_path / 'missing.sqlite3').exists()


def test_ledger_audit_reports_mismatch_missing_reference_and_preserves_files(tmp_path):
    database, ledger = tmp_path / 'power.sqlite3', tmp_path / 'ledger.json'
    official_url = 'https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/655?id=01399&nocatche=1'
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE power655_draws (draw_id TEXT, draw_date TEXT, main_numbers TEXT, bonus_number TEXT, source_url TEXT)'
        )
        connection.execute(
            'INSERT INTO power655_draws VALUES (?,?,?,?,?)',
            ('01399', '2026-09-17', '06 11 25 27 37 45', '15', official_url),
        )
    ledger.write_text(
        json.dumps(
            {
                'predictions': [
                    {
                        'game': 'power655',
                        'id': 'conflicting',
                        'target_date': '2026-09-17',
                        'actual': {'main_numbers': '06 11 25 27 37 46', 'bonus_number': '15'},
                    },
                    {
                        'game': 'power655',
                        'id': 'missing',
                        'target_date': '2026-09-19',
                        'actual': {'main_numbers': '06 11 25 27 37 46', 'bonus_number': '15'},
                    },
                    {'game': 'power655', 'id': 'pending', 'target_date': '2026-09-22', 'actual': None},
                ]
            }
        )
    )
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (database, ledger)}
    report = audit_power_ledger(ledger, database)
    assert report['status'] == 'conflict'
    assert report['compared'] == 1 and report['matched'] == 0
    conflict = report['conflicts'][0]
    assert conflict['different_fields'] == ['main_numbers']
    assert conflict['reference_urls'] == [official_url]
    assert conflict['reference']['main_numbers'][-1] == '45'
    assert conflict['candidate']['main_numbers'][-1] == '46'
    assert report['unverified_entries'][0]['reason'] == 'missing_reference'
    assert report['pending_entries'] == ['pending']
    assert report['ledger_entries'][0]['draw_id_inferred_by_date'] is True
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


def test_ledger_without_reference_is_not_verified(tmp_path):
    database, ledger = tmp_path / 'power.sqlite3', tmp_path / 'ledger.json'
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE power655_draws (draw_id TEXT, draw_date TEXT, main_numbers TEXT, bonus_number TEXT, source_url TEXT)'
        )
    ledger.write_text(
        json.dumps(
            {
                'predictions': [
                    {
                        'game': 'power655',
                        'target_date': '2026-09-17',
                        'actual': {'main_numbers': '06 11 25 27 37 46', 'bonus_number': '15'},
                    },
                ]
            }
        )
    )
    report = audit_power_ledger(ledger, database)
    assert report['status'] == 'not_verified'
    assert report['compared'] == report['matched'] == 0
    assert len(report['unverified_entries']) == 1
