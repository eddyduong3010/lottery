"""Bounded, read-only source audit; no writes to lottery databases.

    python analysis/source_audit.py --live --sample-draws 6 --output /tmp/source-audit.json

Without --live, only local snapshots and explicitly supplied HTML files are read.
The audit compares source records, never forecast accuracy or profitability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from source_validation import (
    HCM_URL,
    audit_power_ledger,
    compare_sources,
    parse_hcm_date_options,
    parse_hcm_html,
    power_records,
    read_power_snapshot,
    read_xsmn_snapshot,
    snapshot_summary,
)
from vietlott_power655.config import DETAIL_URL_TEMPLATE, HISTORY_URL, XOSO_HISTORY_URL
from vietlott_power655.scraper import parse_detail_html, parse_history_html, parse_xoso_history_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Fetch bounded public GET samples; never fetch all history')
    parser.add_argument('--sample-draws', type=int, default=6, choices=range(1, 21), metavar='1..20')
    parser.add_argument('--xsmn-db', type=Path, default=ROOT / 'data/xsmn.sqlite3')
    parser.add_argument('--power-db', type=Path, default=ROOT / 'data/vietlott_power655.sqlite3')
    parser.add_argument('--as-of', type=date.fromisoformat, default=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, help='Defaults to source-evidence beside the JSON output')
    parser.add_argument('--hcm-html', type=Path, action='append', default=[], help='Previously saved official HTML')
    parser.add_argument('--power-html', type=Path, help='Previously saved official Power history HTML')
    parser.add_argument('--secondary-power-html', type=Path, help='Previously saved xoso.com.vn Power history HTML')
    parser.add_argument('--ledger', type=Path, help='Audit Power actuals in a prediction ledger against --power-db')
    args = parser.parse_args(argv)
    evidence_dir = args.evidence_dir or args.output.parent / 'source-evidence'
    now = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat()
    evidence: list[dict] = []

    def read_file(path: Path, url: str) -> str:
        raw = path.read_bytes()
        evidence.append(
            {
                'url': url,
                'path': str(path.resolve()),
                'sha256': hashlib.sha256(raw).hexdigest(),
                'status': 'saved_response',
                'read_at': now,
            }
        )
        return raw.decode('utf-8')

    def fetch(url: str, filename: str) -> str:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        path = evidence_dir / filename
        entry = {'url': url, 'path': str(path.resolve()), 'attempted_at': now}
        evidence.append(entry)
        try:
            response = subprocess.run(
                [
                    'curl',
                    '--silent',
                    '--show-error',
                    '--fail',
                    '--location',
                    '--max-time',
                    '20',
                    '--proto',
                    '=https',
                    '--proto-redir',
                    '=https',
                    '--output',
                    str(path),
                    '--write-out',
                    '%{http_code}',
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            entry['http_status'] = response.stdout.strip()
            if response.returncode:
                raise RuntimeError(response.stderr.strip() or f'curl failed with exit {response.returncode}')
            raw = path.read_bytes()
            entry.update(status='downloaded', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            return raw.decode('utf-8')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            entry.update(status='unavailable', error=str(exc))
            raise

    hcm_local = read_xsmn_snapshot(args.xsmn_db, args.sample_draws)
    power_local = read_power_snapshot(args.power_db, args.sample_draws)
    report: dict = {
        'schema_version': 1,
        'generated_at': now,
        'mode': 'live_sample' if args.live else 'offline',
        'sample_draws_requested': args.sample_draws,
        'database_access': 'sqlite_mode_ro',
        'freshness': {
            'xsmn_snapshot': snapshot_summary(args.xsmn_db, 'xsmn', args.as_of),
            'power_snapshot': snapshot_summary(args.power_db, 'power655', args.as_of),
        },
        'checks': {},
        'evidence': evidence,
        'scope_note': 'Bounded source agreement only; unqueried history and unavailable sources are not verified.',
        'forecast_accuracy': 'not_evaluated',
    }
    hcm_official, hcm_errors = [], []
    for path in args.hcm_html:
        try:
            hcm_official.extend(parse_hcm_html(read_file(path, HCM_URL)))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            hcm_errors.append({'path': str(path), 'error': str(exc)})
    if args.live:
        try:
            home_html = fetch(HCM_URL, 'hcm-home.html')
            options = parse_hcm_date_options(home_html)
            recent = parse_hcm_html(home_html)
            report['freshness']['hcm_official_latest'] = max(record.draw_date for record in recent).isoformat()
            for record in hcm_local:
                url = options.get(record.draw_date)
                if not url:
                    hcm_errors.append({'date': str(record.draw_date), 'error': 'Date is absent from official menu'})
                    continue
                try:
                    html = fetch(url, f'hcm-{record.draw_date}.html')
                    hcm_official.extend(parse_hcm_html(html, url, expected_date=record.draw_date))
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                    hcm_errors.append({'date': str(record.draw_date), 'url': url, 'error': str(exc)})
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            hcm_errors.append({'url': HCM_URL, 'error': str(exc)})

    def complete_check(reference, candidate, reference_label, candidate_label, errors):
        check = compare_sources(reference, candidate, reference_label, candidate_label)
        check['unavailable'] = errors
        if errors and check['status'] == 'verified_sample':
            check['status'] = 'partial_verified'
        return check

    report['checks']['hcm_official_vs_snapshot'] = complete_check(
        hcm_official,
        hcm_local,
        'XSKT HCM official',
        'packaged XSMN snapshot',
        hcm_errors,
    )
    official_recent, secondary_recent, official_sample = [], [], []
    power_errors, secondary_errors, sample_errors = [], [], []
    if args.power_html or args.live:
        try:
            html = (
                read_file(args.power_html, HISTORY_URL)
                if args.power_html
                else fetch(HISTORY_URL, 'power-official.html')
            )
            official_recent = power_records(parse_history_html(html, HISTORY_URL))
            latest = max(official_recent, key=lambda record: record.draw_date)
            report['freshness']['power_official_latest'] = {
                'date': str(latest.draw_date),
                'draw_id': latest.draw_id,
                'source_url': latest.source_url,
                'snapshot_draw_id_gap': int(latest.draw_id) - max(int(record.draw_id) for record in power_local)
                if power_local
                else None,
            }
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            power_errors.append({'url': HISTORY_URL, 'error': str(exc)})
    if args.secondary_power_html or args.live:
        try:
            html = (
                read_file(args.secondary_power_html, XOSO_HISTORY_URL)
                if args.secondary_power_html
                else fetch(
                    XOSO_HISTORY_URL,
                    'power-secondary.html',
                )
            )
            secondary_recent = power_records(parse_xoso_history_html(html, XOSO_HISTORY_URL))
            if not secondary_recent:
                raise ValueError('No complete secondary Power records')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            secondary_errors.append({'url': XOSO_HISTORY_URL, 'error': str(exc)})
    report['checks']['power_official_vs_secondary'] = complete_check(
        official_recent,
        secondary_recent,
        'Vietlott official',
        'xoso.com.vn',
        power_errors + secondary_errors,
    )
    if args.live:
        for record in power_local:
            url = DETAIL_URL_TEMPLATE.format(draw_id=record.draw_id)
            try:
                html = fetch(url, f'power-{record.draw_id}.html')
                official_sample.extend(power_records([parse_detail_html(html, url)]))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                sample_errors.append({'draw_id': record.draw_id, 'url': url, 'error': str(exc)})
    else:
        official_sample = official_recent
    power_check = complete_check(
        official_sample,
        power_local,
        'Vietlott official',
        'packaged Power snapshot',
        sample_errors,
    )
    power_check['independent_publisher_check'] = False
    power_check['scope_note'] += ' Packaged Power records originate from Vietlott; this is same-publisher revalidation.'
    report['checks']['power_official_vs_snapshot'] = power_check
    if not args.live and not (args.hcm_html or args.power_html or args.secondary_power_html):
        for check in report['checks'].values():
            check['reason'] = 'Live fetching was not requested and no saved source response was supplied'
    if args.ledger:
        report['checks']['power_ledger_vs_snapshot'] = audit_power_ledger(args.ledger, args.power_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        json.dumps(
            {
                name: {'status': check['status'], 'compared': check['compared'], 'matched': check['matched']}
                for name, check in report['checks'].items()
            },
            ensure_ascii=False,
        )
    )
    print(f'Report: {args.output.resolve()}')
    return 1 if any(check['conflicts'] for check in report['checks'].values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
