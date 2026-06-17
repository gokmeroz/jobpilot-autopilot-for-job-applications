"""Google Sheets integration — read existing keys and append new job rows."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from src.config import env
from src.models import Job, RemoteType, Status
from src.normalize import build_job_key

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _client() -> gspread.Client:
    sa_path = env("GOOGLE_SA_JSON_PATH", required=True)
    creds = Credentials.from_service_account_file(sa_path, scopes=_SCOPES)
    return gspread.authorize(creds)


def _worksheet(cfg: dict) -> gspread.Worksheet:
    sheet_id = env("SHEET_ID", required=True)
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    return sh.worksheet(cfg["sheet"]["worksheet"])


def _invert_country_display(cfg: dict) -> dict[str, str]:
    """Return display_value -> ISO2 mapping (e.g. 'Germany' -> 'DE')."""
    return {v: k for k, v in cfg["sheet"]["country_display"].items()}


def existing_keys(cfg: dict) -> set[str]:
    """Read all rows and rebuild job_keys to block re-application."""
    try:
        ws = _worksheet(cfg)
        rows = ws.get_all_records()
    except Exception as exc:
        logger.warning("sheet.existing_keys failed — treating sheet as empty: %s", exc)
        return set()

    display_to_iso2 = _invert_country_display(cfg)
    columns = cfg["sheet"]["columns"]

    company_col = columns[columns.index("COMPANY")] if "COMPANY" in columns else "COMPANY"
    position_col = columns[columns.index("POSITION")] if "POSITION" in columns else "POSITION"
    country_col = columns[columns.index("COUNTRY")] if "COUNTRY" in columns else "COUNTRY"

    keys: set[str] = set()
    for row in rows:
        company = str(row.get(company_col, "")).strip()
        position = str(row.get(position_col, "")).strip()
        country_display = str(row.get(country_col, "")).strip()

        if not company or not position:
            continue

        iso2 = display_to_iso2.get(country_display, country_display)
        keys.add(build_job_key(company, position, iso2))

    logger.info("sheet: loaded %d existing keys", len(keys))
    return keys


def append_jobs(jobs: list[Job], cfg: dict) -> int:
    """Append jobs to the sheet, skipping any already present. Returns count appended."""
    if not jobs:
        return 0

    existing = existing_keys(cfg)
    sheet_cfg = cfg["sheet"]
    country_display = sheet_cfg["country_display"]
    stage_applied = sheet_cfg["stage_applied"]
    stage_queued = sheet_cfg["stage_queued"]
    situation = sheet_cfg["situation_default"]

    def _type(job: Job) -> str:
        if job.remote == RemoteType.remote:
            return "Remote"
        if job.remote == RemoteType.hybrid:
            return "Hybrid"
        return "On-Site"

    def _stage(job: Job) -> str:
        if job.status == Status.applied:
            return stage_applied
        return stage_queued

    today = datetime.now(timezone.utc).strftime("%-m/%-d/%Y")

    rows: list[list[str]] = []
    skipped = 0
    for job in jobs:
        if job.job_key in existing:
            skipped += 1
            continue
        country = country_display.get(job.country, job.country)
        rows.append([
            today,
            country,
            job.company,
            job.title,
            job.location or "",
            _type(job),
            job.salary or "",
            "ENGLISH" if (job.language or "EN").upper() == "EN" else job.language.upper(),
            _stage(job),
            situation,
        ])

    if not rows:
        logger.info("sheet.append_jobs: nothing new to append (skipped %d)", skipped)
        return 0

    try:
        ws = _worksheet(cfg)
        # Anchor on column A (DATE) — always populated for real data rows.
        # Cheaper than get_all_values() and immune to ghost rows created by
        # sheet formatting (borders, colours) that make empty rows look non-empty.
        col_a = ws.col_values(1)
        last_row = max(
            (i + 1 for i, v in enumerate(col_a) if v.strip()),
            default=1,
        )
        next_row = last_row + 1
        cells = []
        for row_offset, row_data in enumerate(rows):
            for col_offset, value in enumerate(row_data):
                cells.append(gspread.Cell(next_row + row_offset, col_offset + 1, value))
        ws.update_cells(cells, value_input_option="USER_ENTERED")
    except Exception as exc:
        logger.error("sheet.append_jobs failed: %s", exc)
        raise

    logger.info("sheet.append_jobs: appended %d rows, skipped %d duplicates", len(rows), skipped)
    return len(rows)


def sweep(cfg: dict, ghost_after_days: int) -> int:
    # TODO #8 — needs applied-date column mapping; ask user before implementing
    raise NotImplementedError(
        "sweep() is not yet implemented. "
        "It requires mapping the applied-date column before stale rows can be ghosted."
    )
