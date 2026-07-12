from __future__ import annotations

import argparse
import sys
from pathlib import Path

from prediction_history import update_prediction_history
from vietlott_power655.repository import SQLiteRepository as Power655Repository
from xsmn.repository import SQLiteRepository as XSMNRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Lưu và đối chiếu lịch sử dự đoán xổ số')
    parser.add_argument('--xsmn-database', type=Path, default=Path('data/xsmn.sqlite3'))
    parser.add_argument('--power655-database', type=Path, default=Path('data/vietlott_power655.sqlite3'))
    parser.add_argument('--history', type=Path, default=Path('data/prediction_history.json'))
    return parser


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    args = build_parser().parse_args()
    xsmn_results = XSMNRepository(args.xsmn_database).load_results()
    power655_draws = Power655Repository(args.power655_database).load_draws()
    history = update_prediction_history(args.history, xsmn_results, power655_draws)
    evaluated = sum(1 for record in history['predictions'] if record.get('actual'))
    print(f'Đã lưu {len(history["predictions"])} dự đoán; {evaluated} kỳ đã có kết quả đối chiếu.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
