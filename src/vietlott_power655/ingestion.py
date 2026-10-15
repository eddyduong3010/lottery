from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import time

from .calendar import latest_available_date
from .models import Power655Draw
from .repository import OutcomeConflictError, SQLiteRepository
from .scraper import FetchBatch, VietlottConflictError, VietlottPower655Client, _merge_draws, validate_public_draw


@dataclass(frozen=True, slots=True)
class IngestionReport:
    requested_draws: int
    stored_draws: int
    failed_message: str | None = None
    first_draw_id: str | None = None
    last_draw_id: str | None = None
    missing_draw_ids: tuple[str, ...] = ()
    conflict_draw_ids: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    source_errors: tuple[str, ...] = ()
    status: str = 'complete'
    request_count: int = 0
    checked_at: float = field(default_factory=time)
    checked_draw_ids: tuple[str, ...] = ()


def _store_batch(repository, batch: FetchBatch, requested: set[str]) -> IngestionReport:
    observations: dict[str, Power655Draw] = {}
    conflicts: set[str] = set(batch.conflict_draw_ids)
    errors = list(batch.errors)
    # Quarantine every conflicting ID before any writes; last publisher never wins.
    for draw in batch.draws:
        try:
            _merge_draws(observations, [draw])
        except VietlottConflictError as exc:
            conflicts.add(draw.draw_id)
            errors.append(str(exc))
    by_date: dict[object, str] = {}
    for draw in observations.values():
        previous_id = by_date.get(draw.draw_date)
        if previous_id is not None and previous_id != draw.draw_id:
            conflicts.update((previous_id, draw.draw_id))
        by_date[draw.draw_date] = draw.draw_id
    stored, accepted, sources = 0, set(), set()
    # Fake/legacy clients remain usable; production repository enforces facts atomically.
    if not hasattr(repository, 'get_draw'):
        selected = [d for d in observations.values() if d.draw_id in requested and d.draw_id not in conflicts]
        stored = repository.upsert_draws(selected)
        accepted = {d.draw_id for d in selected}
        sources = {d.source_url for d in selected if d.source_url}
    else:
        for draw_id, draw in observations.items():
            if draw_id in conflicts:
                continue
            try:
                validate_public_draw(draw)
                existing = repository.get_draw(draw_id)
                repository.upsert_draw(draw)
                stored += existing is None
                accepted.add(draw_id)
                if draw.source_url:
                    sources.add(draw.source_url)
            except OutcomeConflictError as exc:
                conflicts.add(draw_id)
                errors.append(str(exc))
            except ValueError as exc:
                errors.append(f'#{draw_id}: {exc}')
    pending = (requested - accepted) | set(batch.missing_draw_ids) | conflicts
    messages = []
    if batch.latest_draw_id is None:
        messages.append('Không xác minh được kỳ mới nhất từ các nguồn; chưa biết đầy đủ ID còn thiếu')
    elif batch.draws and max(d.draw_date for d in batch.draws) < latest_available_date():
        messages.append('Nguồn trả dữ liệu cũ hơn kỳ gần nhất dự kiến; không coi là đồng bộ hiện hành')
    if pending:
        messages.append(f'Còn {len(pending)} kỳ chưa lưu: ' + ', '.join(sorted(pending)))
    if conflicts or batch.conflicts:
        messages.extend(batch.conflicts or ('Có kết quả mâu thuẫn, đã cách ly và không ghi đè',))
    if messages and errors:
        messages.append(errors[-1])
    status = (
        'partial'
        if stored and messages
        else 'unavailable'
        if batch.latest_draw_id is None
        else 'stale'
        if messages
        else 'degraded'
        if errors
        else 'complete'
    )
    if conflicts or batch.conflicts:
        status = 'conflict'
    ids = sorted(accepted)
    return IngestionReport(
        len(requested),
        stored,
        '; '.join(messages) or None,
        ids[0] if ids else None,
        ids[-1] if ids else None,
        tuple(sorted(pending)),
        tuple(sorted(conflicts)),
        tuple(sorted(sources)),
        tuple(errors),
        status,
        batch.request_count,
        checked_draw_ids=tuple(sorted(accepted)),
    )


def reconcile_sync_status(previous: IngestionReport | None, current: IngestionReport) -> IngestionReport:
    """A recent-only check cannot erase older unresolved gaps or quarantined observations."""
    if previous is None:
        return current
    checked = (
        set(current.checked_draw_ids) - set(current.conflict_draw_ids)
        if current.checked_at >= previous.checked_at
        else set()
    )
    pending = set(current.missing_draw_ids) | (set(previous.missing_draw_ids) - checked)
    conflicts = set(current.conflict_draw_ids) | (set(previous.conflict_draw_ids) - checked)
    pending |= conflicts
    if not pending and not conflicts:
        return current  # Confirmed recovery clears historical transport errors.
    messages = [current.failed_message] if current.failed_message else []
    messages.append(f'Còn {len(pending)} kỳ chưa được xác minh lại: ' + ', '.join(sorted(pending)))
    return replace(
        current,
        failed_message='; '.join(messages),
        missing_draw_ids=tuple(sorted(pending)),
        conflict_draw_ids=tuple(sorted(conflicts)),
        status='conflict' if conflicts else 'partial',
        checked_at=max(previous.checked_at, current.checked_at),
    )


def ingest_latest(
    repository: SQLiteRepository, client: VietlottPower655Client, limit: int = 8, include_details: bool = True
) -> IngestionReport:
    try:
        if hasattr(client, 'fetch_recent_batch'):
            batch = client.fetch_recent_batch(limit, include_details)
        else:
            draws = client.fetch_latest_draws(limit=limit, include_details=include_details)
            batch = FetchBatch(tuple(draws), (), latest_draw_id=max((d.draw_id for d in draws), default=None))
    except Exception as exc:
        return IngestionReport(limit, 0, str(exc), status='unavailable')
    latest = int(batch.latest_draw_id) if batch.latest_draw_id else 0
    requested = {f'{i:05d}' for i in range(1, latest + 1)} - repository.draw_ids()
    return _store_batch(repository, batch, requested)


def ingest_all(
    repository: SQLiteRepository, client: VietlottPower655Client, request_delay_seconds: float = 0.05
) -> IngestionReport:
    try:
        draws = client.fetch_all_draws(request_delay_seconds=request_delay_seconds)
        latest = max(int(d.draw_id) for d in draws)
    except Exception as exc:
        return IngestionReport(0, 0, str(exc), status='unavailable')
    existing = repository.draw_ids()
    requested = {f'{i:05d}' for i in range(1, latest + 1)} - existing
    return _store_batch(repository, FetchBatch(tuple(draws), (), latest_draw_id=f'{latest:05d}'), requested)


def sync_missing_results(
    repository: SQLiteRepository, client: VietlottPower655Client, bulk_threshold: int = 32
) -> IngestionReport:
    """Resume known gaps in one bounded attempt; valid partial results survive outages."""
    del bulk_threshold  # Startup no longer silently downloads an entire archive.
    existing = repository.draw_ids()
    if hasattr(client, 'fetch_missing_draws'):
        batch = client.fetch_missing_draws(existing)
        latest = int(batch.latest_draw_id) if batch.latest_draw_id else 0
        requested = {f'{i:05d}' for i in range(1, latest + 1)} - existing
    else:
        try:
            history = client.fetch_history()
            latest = max(int(d.draw_id) for d in history)
            requested = {f'{i:05d}' for i in range(1, latest + 1)} - existing
            found = {d.draw_id: d for d in history}
            errors = []
            for draw_id in sorted(requested - set(found)):
                try:
                    found[draw_id] = client.fetch_detail(draw_id)
                except Exception as exc:
                    errors.append(f'#{draw_id}: {exc}')
            batch = FetchBatch(
                tuple(found.values()),
                tuple(sorted(requested - set(found))),
                tuple(errors),
                latest_draw_id=f'{latest:05d}',
            )
        except Exception as exc:
            return IngestionReport(0, 0, str(exc), status='unavailable')
    return _store_batch(repository, batch, requested)
