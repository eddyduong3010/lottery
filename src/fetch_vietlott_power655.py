from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vietlott_power655.ingestion import ingest_all, ingest_latest
from vietlott_power655.repository import SQLiteRepository
from vietlott_power655.scraper import VietlottPower655Client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Tải kết quả Vietlott Power 6/55 vào SQLite')
    parser.add_argument('--database', type=Path, default=Path('data/vietlott_power655.sqlite3'))
    parser.add_argument('--limit', type=int, default=8, help='Số kỳ gần nhất cần tải')
    parser.add_argument('--all', action='store_true', help='Tải toàn bộ lịch sử từ kỳ đầu tiên')
    parser.add_argument('--request-delay', type=float, default=0.05, help='Số giây nghỉ giữa hai trang lịch sử')
    parser.add_argument('--skip-details', action='store_true', help='Không tải bảng giải chi tiết cho các kỳ gần nhất')
    return parser


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = build_parser().parse_args()
    repository = SQLiteRepository(args.database)
    client = VietlottPower655Client()
    if args.all:
        print('Đang tải toàn bộ lịch sử Power 6/55 từ Vietlott...')
        report = ingest_all(repository, client, request_delay_seconds=max(args.request_delay, 0))
    else:
        report = ingest_latest(
            repository,
            client,
            limit=max(args.limit, 1),
            include_details=not args.skip_details,
        )
    if report.failed_message:
        print(f'Chưa tải được Power 6/55: {report.failed_message}')
        return 1
    print(f'Đã lưu {report.stored_draws} kỳ Power 6/55.')
    if args.all:
        print(f'Phạm vi kỳ: #{report.first_draw_id} đến #{report.last_draw_id}.')
        print(f'Số kỳ bị thiếu trong chuỗi ID: {len(report.missing_draw_ids)}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
