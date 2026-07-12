from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from vietlott_power655.ingestion import IngestionReport as Power655IngestionReport
from vietlott_power655.ingestion import sync_missing_results as sync_power655_missing
from vietlott_power655.repository import SQLiteRepository as Power655Repository
from vietlott_power655.scraper import VietlottPower655Client
from xsmn.ingestion import IngestionReport as XSMNIngestionReport
from xsmn.ingestion import sync_missing_results as sync_xsmn_missing
from xsmn.repository import SQLiteRepository as XSMNRepository
from xsmn.scraper import XosoComClient


@dataclass(frozen=True, slots=True)
class StartupSyncReport:
    xsmn: XSMNIngestionReport
    power655: Power655IngestionReport


def sync_all_missing(
    xsmn_database: str,
    power655_database: str,
    xsmn_bootstrap_days: int = 30,
) -> StartupSyncReport:
    with ThreadPoolExecutor(max_workers=2) as executor:
        xsmn_future = executor.submit(
            sync_xsmn_missing,
            XSMNRepository(xsmn_database),
            XosoComClient(),
            xsmn_bootstrap_days,
        )
        power655_future = executor.submit(
            sync_power655_missing,
            Power655Repository(power655_database),
            VietlottPower655Client(),
        )
        xsmn_report = xsmn_future.result()
        power655_report = power655_future.result()
    return StartupSyncReport(xsmn=xsmn_report, power655=power655_report)
