from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import PRIZE_DISPLAY_ORDER, PRIZE_SPECS
from .models import Draw, PrizeResult

RESULT_COLUMNS = [
    'draw_date',
    'station_code',
    'station_name',
    'prize_code',
    'ordinal',
    'number',
    'source_url',
    'fetched_at',
]


class SQLiteRepository:
    def __init__(self, path: str | Path = 'data/xsmn.sqlite3') -> None:
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
                CREATE TABLE IF NOT EXISTS draws (
                    id INTEGER PRIMARY KEY,
                    draw_date TEXT NOT NULL,
                    station_code TEXT NOT NULL,
                    station_name TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT,
                    parser_version TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(draw_date, station_code)
                );

                CREATE TABLE IF NOT EXISTS prize_results (
                    id INTEGER PRIMARY KEY,
                    draw_id INTEGER NOT NULL REFERENCES draws(id) ON DELETE CASCADE,
                    prize_code TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    number TEXT NOT NULL,
                    UNIQUE(draw_id, prize_code, ordinal)
                );

                CREATE INDEX IF NOT EXISTS idx_draws_date ON draws(draw_date);
                CREATE INDEX IF NOT EXISTS idx_results_prize ON prize_results(prize_code, number);
                """
            )

    def upsert_draw(self, draw: Draw) -> None:
        self.upsert_draws((draw,))

    def _write_draw(self, connection: sqlite3.Connection, draw: Draw) -> None:
        fetched_at = draw.fetched_at.isoformat() if draw.fetched_at else None
        connection.execute(
            """
            INSERT INTO draws (
                draw_date, station_code, station_name, source_url, fetched_at, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(draw_date, station_code) DO UPDATE SET
                station_name = excluded.station_name,
                source_url = excluded.source_url,
                fetched_at = excluded.fetched_at,
                parser_version = excluded.parser_version,
                revision = draws.revision + 1
            """,
            (
                draw.draw_date.isoformat(),
                draw.station_code,
                draw.station_name,
                draw.source_url,
                fetched_at,
                draw.parser_version,
            ),
        )
        draw_id = connection.execute(
            'SELECT id FROM draws WHERE draw_date = ? AND station_code = ?',
            (draw.draw_date.isoformat(), draw.station_code),
        ).fetchone()['id']
        connection.execute('DELETE FROM prize_results WHERE draw_id = ?', (draw_id,))
        connection.executemany(
            """
            INSERT INTO prize_results (draw_id, prize_code, ordinal, number)
            VALUES (?, ?, ?, ?)
            """,
            [(draw_id, item.prize_code, item.ordinal, item.number) for item in draw.results],
        )

    def upsert_draws(self, draws: Iterable[Draw]) -> int:
        batch = tuple(draws)
        self.initialize()
        with closing(self._connect()) as connection, connection:
            for draw in batch:
                self._write_draw(connection, draw)
        return len(batch)

    def get_draw(self, draw_date: date, station_code: str) -> Draw | None:
        self.initialize()
        with closing(self._connect()) as connection:
            draw_row = connection.execute(
                """
                SELECT * FROM draws WHERE draw_date = ? AND station_code = ?
                """,
                (draw_date.isoformat(), station_code),
            ).fetchone()
            if draw_row is None:
                return None
            result_rows = connection.execute(
                """
                SELECT prize_code, ordinal, number
                FROM prize_results
                WHERE draw_id = ?
                ORDER BY id
                """,
                (draw_row['id'],),
            ).fetchall()

        return Draw(
            draw_date=date.fromisoformat(draw_row['draw_date']),
            station_code=draw_row['station_code'],
            station_name=draw_row['station_name'],
            results=tuple(PrizeResult(row['prize_code'], row['ordinal'], row['number']) for row in result_rows),
            source_url=draw_row['source_url'],
            fetched_at=datetime.fromisoformat(draw_row['fetched_at']) if draw_row['fetched_at'] else None,
            parser_version=draw_row['parser_version'],
        )

    def load_results(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        station_codes: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        self.initialize()
        conditions: list[str] = []
        parameters: list[str] = []
        if start_date:
            conditions.append('d.draw_date >= ?')
            parameters.append(start_date.isoformat())
        if end_date:
            conditions.append('d.draw_date <= ?')
            parameters.append(end_date.isoformat())
        codes = tuple(station_codes or ())
        if codes:
            placeholders = ', '.join('?' for _ in codes)
            conditions.append(f'd.station_code IN ({placeholders})')
            parameters.extend(codes)
        where_clause = f'WHERE {" AND ".join(conditions)}' if conditions else ''
        query = f"""
            SELECT
                d.draw_date,
                d.station_code,
                d.station_name,
                r.prize_code,
                r.ordinal,
                r.number,
                d.source_url,
                d.fetched_at
            FROM draws d
            JOIN prize_results r ON r.draw_id = d.id
            {where_clause}
            ORDER BY d.draw_date, d.station_code, r.id
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        if not rows:
            return pd.DataFrame(columns=RESULT_COLUMNS)
        frame = pd.DataFrame([dict(row) for row in rows], columns=RESULT_COLUMNS)
        frame['draw_date'] = pd.to_datetime(frame['draw_date'])
        return frame

    def date_bounds(self) -> tuple[date | None, date | None]:
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT MIN(draw_date) AS first, MAX(draw_date) AS last FROM draws').fetchone()
        return (
            date.fromisoformat(row['first']) if row['first'] else None,
            date.fromisoformat(row['last']) if row['last'] else None,
        )

    def count_draws(self) -> int:
        self.initialize()
        with closing(self._connect()) as connection:
            return int(connection.execute('SELECT COUNT(*) AS total FROM draws').fetchone()['total'])

    def available_draws(self) -> pd.DataFrame:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT draw_date, station_code, station_name, source_url
                FROM draws ORDER BY draw_date DESC, station_name
                """
            ).fetchall()
        frame = pd.DataFrame([dict(row) for row in rows])
        if not frame.empty:
            frame['draw_date'] = pd.to_datetime(frame['draw_date'])
        return frame


def ordered_results_table(draw: Draw) -> pd.DataFrame:
    labels = {code: index for index, code in enumerate(PRIZE_DISPLAY_ORDER)}
    rows = [
        {
            'Mã giải': item.prize_code,
            'Giải': PRIZE_SPECS[item.prize_code].label,
            'Thứ tự': item.ordinal,
            'Kết quả': item.number,
        }
        for item in draw.results
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame['_order'] = frame['Mã giải'].map(labels)
    return frame.sort_values(['_order', 'Thứ tự']).drop(columns=['_order'])
