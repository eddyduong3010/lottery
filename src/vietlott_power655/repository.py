from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS
from .models import Power655Draw, PrizeTierResult


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
            for draw in batch:
                self._write_draw(connection, draw)
        return len(batch)

    def upsert_draw(self, draw: Power655Draw) -> None:
        self.upsert_draws((draw,))

    def _write_draw(self, connection: sqlite3.Connection, draw: Power655Draw) -> None:
        fetched_at = draw.fetched_at.isoformat() if draw.fetched_at else None
        connection.execute(
            """
            INSERT INTO power655_draws (
                draw_id, draw_date, main_numbers, bonus_number, source_url, fetched_at, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(draw_id) DO UPDATE SET
                draw_date = excluded.draw_date,
                main_numbers = excluded.main_numbers,
                bonus_number = excluded.bonus_number,
                source_url = excluded.source_url,
                fetched_at = excluded.fetched_at,
                parser_version = excluded.parser_version,
                revision = power655_draws.revision + 1
            """,
            (
                draw.draw_id,
                draw.draw_date.isoformat(),
                ' '.join(draw.main_numbers),
                draw.bonus_number,
                draw.source_url,
                fetched_at,
                draw.parser_version,
            ),
        )
        draw_pk = connection.execute('SELECT id FROM power655_draws WHERE draw_id = ?', (draw.draw_id,)).fetchone()[
            'id'
        ]
        connection.execute('DELETE FROM power655_prizes WHERE draw_pk = ?', (draw_pk,))
        connection.executemany(
            """
            INSERT INTO power655_prizes (draw_pk, tier_code, winner_count, payout_vnd)
            VALUES (?, ?, ?, ?)
            """,
            [(draw_pk, prize.tier_code, prize.winner_count, prize.payout_vnd) for prize in draw.prizes],
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
