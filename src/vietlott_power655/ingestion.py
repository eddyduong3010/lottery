from __future__ import annotations

from dataclasses import dataclass

from .repository import SQLiteRepository
from .scraper import VietlottPower655Client


@dataclass(frozen=True, slots=True)
class IngestionReport:
    requested_draws: int
    stored_draws: int
    failed_message: str | None = None


def ingest_latest(
    repository: SQLiteRepository,
    client: VietlottPower655Client,
    limit: int = 8,
    include_details: bool = True,
) -> IngestionReport:
    try:
        draws = client.fetch_latest_draws(limit=limit, include_details=include_details)
    except Exception as exc:
        return IngestionReport(limit, 0, str(exc))
    return IngestionReport(limit, repository.upsert_draws(draws), None)
