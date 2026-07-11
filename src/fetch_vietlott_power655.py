from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vietlott_power655.ingestion import ingest_latest
from vietlott_power655.repository import SQLiteRepository
from vietlott_power655.scraper import VietlottPower655Client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Tải kết quả Vietlott Power 6/55 vào SQLite')
    parser.add_argument('--database', type=Path, default=Path('data/vietlott_power655.sqlite3'))
    parser.add_argument('--limit', type=int, default=8, help='Số kỳ gần nhất trên trang lịch sử công khai')
    parser.add_argument('--skip-details', action='store_true', help='Chỉ lưu bộ số, không tải bảng giải từng kỳ')
    return parser


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = build_parser().parse_args()
    report = ingest_latest(
        SQLiteRepository(args.database),
        VietlottPower655Client(),
        limit=max(args.limit, 1),
        include_details=not args.skip_details,
    )
    if report.failed_message:
        print(f'Chưa tải được Power 6/55: {report.failed_message}')
        return 1
    print(f'Đã lưu {report.stored_draws} kỳ Power 6/55.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
