from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from xsmn.calendar import latest_available_date
from xsmn.ingestion import ingest_missing_range_parallel
from xsmn.repository import SQLiteRepository


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Ngày phải có định dạng YYYY-MM-DD') from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Tải kết quả xổ số miền Nam vào SQLite')
    parser.add_argument('--database', type=Path, default=Path('data/xsmn.sqlite3'))
    parser.add_argument('--days', type=int, default=30, help='Số ngày gần nhất nếu không truyền --from-date')
    parser.add_argument('--from-date', type=parse_date)
    parser.add_argument('--to-date', type=parse_date)
    parser.add_argument('--workers', type=int, default=12, help='Số luồng tải song song, tối đa 24')
    return parser


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = build_parser().parse_args()
    end_date = args.to_date or latest_available_date()
    start_date = args.from_date or end_date - timedelta(days=max(args.days, 1) - 1)
    repository = SQLiteRepository(args.database)

    def show_progress(index: int, total: int, selected_date: date) -> None:
        print(f'[{index:>3}/{total}] Đang tải {selected_date:%d-%m-%Y}...')

    report = ingest_missing_range_parallel(
        repository,
        start_date,
        end_date,
        workers=max(1, min(args.workers, 24)),
        on_progress=show_progress,
    )
    print(f'Đã lưu {report.stored_draws} kỳ đài trong {report.requested_days} ngày.')
    if report.failed_dates:
        print('Các ngày chưa tải được:')
        for selected_date, error in report.failed_dates:
            print(f'  - {selected_date:%d-%m-%Y}: {error}')
    return 1 if report.failed_dates and report.stored_draws == 0 else 0


if __name__ == '__main__':
    raise SystemExit(main())
