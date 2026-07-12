from __future__ import annotations

from dataclasses import dataclass

from .repository import SQLiteRepository
from .scraper import VietlottPower655Client


@dataclass(frozen=True, slots=True)
class IngestionReport:
    requested_draws: int
    stored_draws: int
    failed_message: str | None = None
    first_draw_id: str | None = None
    last_draw_id: str | None = None
    missing_draw_ids: tuple[str, ...] = ()


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


def ingest_all(
    repository: SQLiteRepository,
    client: VietlottPower655Client,
    request_delay_seconds: float = 0.05,
) -> IngestionReport:
    try:
        draws = client.fetch_all_draws(request_delay_seconds=request_delay_seconds)
    except Exception as exc:
        return IngestionReport(0, 0, str(exc))
    numeric_ids = sorted(int(draw.draw_id) for draw in draws)
    existing_ids = set(numeric_ids)
    missing = tuple(
        f'{draw_id:05d}' for draw_id in range(numeric_ids[0], numeric_ids[-1] + 1) if draw_id not in existing_ids
    )
    stored = repository.upsert_draws(draws)
    return IngestionReport(
        requested_draws=len(draws),
        stored_draws=stored,
        first_draw_id=draws[0].draw_id,
        last_draw_id=draws[-1].draw_id,
        missing_draw_ids=missing,
    )


def sync_missing_results(
    repository: SQLiteRepository,
    client: VietlottPower655Client,
    bulk_threshold: int = 32,
) -> IngestionReport:
    """Fetch only unpublished local gaps; use paginated bulk history for a large bootstrap."""
    try:
        latest_page = client.fetch_history()
        latest_id = max(int(draw.draw_id) for draw in latest_page)
        latest_by_id = {draw.draw_id: draw for draw in latest_page}
        existing = repository.draw_ids()
        missing_ids = [f'{draw_id:05d}' for draw_id in range(1, latest_id + 1) if f'{draw_id:05d}' not in existing]
        if len(missing_ids) > bulk_threshold:
            draws = client.fetch_all_draws()
            draws.extend(latest_page)
        else:
            draws = [latest_by_id.get(draw_id) or client.fetch_detail(draw_id) for draw_id in missing_ids]
    except Exception as exc:
        return IngestionReport(0, 0, str(exc))
    stored = repository.upsert_draws(draws)
    return IngestionReport(
        requested_draws=len(missing_ids),
        stored_draws=stored,
        first_draw_id=draws[0].draw_id if draws else None,
        last_draw_id=draws[-1].draw_id if draws else None,
        missing_draw_ids=(),
    )
