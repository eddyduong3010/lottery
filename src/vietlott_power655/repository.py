from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS
from .models import Power655Draw, PrizeTierResult


class OutcomeConflictError(ValueError):
    """A published draw is immutable; corrections require an explicit reviewed flow."""


class SQLiteRepository:
    def __init__(self, path: str | Path = 'data/vietlott_power655.sqlite3') -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS power655_draws (
                    id INTEGER PRIMARY KEY,
                    draw_id TEXT NOT NULL UNIQUE,
                    draw_date TEXT NOT NULL,
                    main_numbers TEXT NOT NULL,
                    bonus_number TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT,
                    parser_version TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS power655_prizes (
                    id INTEGER PRIMARY KEY,
                    draw_pk INTEGER NOT NULL REFERENCES power655_draws(id) ON DELETE CASCADE,
                    tier_code TEXT NOT NULL,
                    winner_count INTEGER,
                    payout_vnd INTEGER,
                    UNIQUE(draw_pk, tier_code)
                );

                CREATE INDEX IF NOT EXISTS idx_power655_draw_date ON power655_draws(draw_date);
                """
            )

    def upsert_draws(self, draws: Iterable[Power655Draw]) -> int:
        batch = tuple(draws)
        self.initialize()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            for draw in batch:
                self._write_draw(connection, draw)
        return len(batch)

    def upsert_draw(self, draw: Power655Draw) -> None:
        self.upsert_draws((draw,))

    def _write_draw(self, connection: sqlite3.Connection, draw: Power655Draw) -> None:
        existing = connection.execute('SELECT * FROM power655_draws WHERE draw_id = ?', (draw.draw_id,)).fetchone()
        same_date = connection.execute(
            'SELECT draw_id FROM power655_draws WHERE draw_date = ? AND draw_id != ?',
            (draw.draw_date.isoformat(), draw.draw_id),
        ).fetchone()
        if same_date:
            raise OutcomeConflictError(
                f'Ngày {draw.draw_date} đã thuộc kỳ {same_date["draw_id"]}, không phải {draw.draw_id}'
            )
        # IDs are chronological; reject wrong-date observations even when the date is unused.
        neighbors = connection.execute(
            'SELECT draw_id,draw_date FROM power655_draws WHERE '
            'draw_id=(SELECT MAX(draw_id) FROM power655_draws WHERE draw_id < ?) OR '
            'draw_id=(SELECT MIN(draw_id) FROM power655_draws WHERE draw_id > ?)',
            (draw.draw_id, draw.draw_id),
        ).fetchall()
        if any(
            (row['draw_id'] < draw.draw_id and row['draw_date'] >= draw.draw_date.isoformat())
            or (row['draw_id'] > draw.draw_id and row['draw_date'] <= draw.draw_date.isoformat())
            for row in neighbors
        ):
            raise OutcomeConflictError(f'Kỳ {draw.draw_id}: ngày quay không theo thứ tự ID đã lưu')
        if existing is not None:
            if (existing['draw_date'], existing['main_numbers'], existing['bonus_number']) != (
                draw.draw_date.isoformat(),
                ' '.join(draw.main_numbers),
                draw.bonus_number,
            ):
                raise OutcomeConflictError(f'Kỳ {draw.draw_id}: kết quả mâu thuẫn với dữ liệu đã lưu')
            rows = {
                row['tier_code']: row
                for row in connection.execute(
                    'SELECT * FROM power655_prizes WHERE draw_pk = ?',
                    (existing['id'],),
                )
            }
            for prize in draw.prizes:
                row = rows[prize.tier_code]
                for field in ('winner_count', 'payout_vnd'):
                    known, incoming = row[field], getattr(prize, field)
                    if known is not None and incoming is not None and known != incoming:
                        raise OutcomeConflictError(f'Kỳ {draw.draw_id}: thông tin giải {prize.tier_code} mâu thuẫn')
                # Enrich unknown fields, but never replace an established fact with None/defaults.
                connection.execute(
                    'UPDATE power655_prizes SET winner_count=COALESCE(winner_count, ?), '
                    'payout_vnd=COALESCE(payout_vnd, ?) WHERE id=?',
                    (prize.winner_count, prize.payout_vnd, row['id']),
                )
            return
        cursor = connection.execute(
            'INSERT INTO power655_draws '
            '(draw_id, draw_date, main_numbers, bonus_number, source_url, fetched_at, parser_version) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (
                draw.draw_id,
                draw.draw_date.isoformat(),
                ' '.join(draw.main_numbers),
                draw.bonus_number,
                draw.source_url,
                draw.fetched_at.isoformat() if draw.fetched_at else None,
                draw.parser_version,
            ),
        )
        connection.executemany(
            'INSERT INTO power655_prizes (draw_pk,tier_code,winner_count,payout_vnd) VALUES (?,?,?,?)',
            [(cursor.lastrowid, p.tier_code, p.winner_count, p.payout_vnd) for p in draw.prizes],
        )

    def get_draw(self, draw_id: str) -> Power655Draw | None:
        self.initialize()
        with closing(self._connect()) as connection:
            draw_row = connection.execute('SELECT * FROM power655_draws WHERE draw_id = ?', (draw_id,)).fetchone()
            if draw_row is None:
                return None
            prize_rows = connection.execute(
                """
                SELECT tier_code, winner_count, payout_vnd
                FROM power655_prizes
                WHERE draw_pk = ?
                ORDER BY id
                """,
                (draw_row['id'],),
            ).fetchall()
        return Power655Draw(
            draw_id=draw_row['draw_id'],
            draw_date=date.fromisoformat(draw_row['draw_date']),
            main_numbers=tuple(draw_row['main_numbers'].split()),
            bonus_number=draw_row['bonus_number'],
            prizes=tuple(
                PrizeTierResult(row['tier_code'], row['winner_count'], row['payout_vnd']) for row in prize_rows
            ),
            source_url=draw_row['source_url'],
            fetched_at=datetime.fromisoformat(draw_row['fetched_at']) if draw_row['fetched_at'] else None,
            parser_version=draw_row['parser_version'],
        )

    def load_draws(self) -> pd.DataFrame:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT draw_id, draw_date, main_numbers, bonus_number, source_url, fetched_at
                FROM power655_draws
                ORDER BY draw_date, draw_id
                """
            ).fetchall()
        frame = pd.DataFrame([dict(row) for row in rows])
        if not frame.empty:
            frame['draw_date'] = pd.to_datetime(frame['draw_date'])
        return frame

    def date_bounds(self) -> tuple[date | None, date | None]:
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute(
                'SELECT MIN(draw_date) AS first, MAX(draw_date) AS last FROM power655_draws'
            ).fetchone()
        return (
            date.fromisoformat(row['first']) if row['first'] else None,
            date.fromisoformat(row['last']) if row['last'] else None,
        )

    def draw_ids(self) -> set[str]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute('SELECT draw_id FROM power655_draws').fetchall()
        return {row['draw_id'] for row in rows}


def prize_table(draw: Power655Draw) -> pd.DataFrame:
    rows = []
    prize_map = {prize.tier_code: prize for prize in draw.prizes}
    for code in PRIZE_DISPLAY_ORDER:
        spec = PRIZE_SPECS[code]
        prize = prize_map[code]
        rows.append(
            {
                'Hạng giải': spec.label,
                'Điều kiện': spec.match_description,
                'Số lượng giải': prize.winner_count,
                'Giá trị giải': prize.payout_vnd,
            }
        )
    return pd.DataFrame(rows)
