from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from src.config import ROOT, load
from src.models import Job, Status

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key      TEXT NOT NULL,
    url_hash     TEXT NOT NULL,
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    country      TEXT NOT NULL,
    status       TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (job_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_url_hash ON jobs (url_hash);
"""


def _db_path() -> Path:
    cfg = load("config")
    p = ROOT / cfg["paths"]["ledger"]
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def _conn():
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> None:
    """Create tables if they don't exist yet."""
    with _conn() as con:
        con.executescript(_DDL)
    log.debug("ledger initialised at %s", _db_path())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def is_duplicate(job: Job) -> bool:
    """True if we've already seen this job_key or url_hash."""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM jobs WHERE job_key = ? OR url_hash = ? LIMIT 1",
            (job.job_key, job.url_hash()),
        ).fetchone()
    return row is not None


def known_keys() -> set[str]:
    """Return all job_keys ever recorded — for fast bulk pre-filtering."""
    with _conn() as con:
        rows = con.execute("SELECT job_key FROM jobs").fetchall()
    return {r["job_key"] for r in rows}


def known_url_hashes() -> set[str]:
    """Return all url_hashes ever recorded."""
    with _conn() as con:
        rows = con.execute("SELECT url_hash FROM jobs").fetchall()
    return {r["url_hash"] for r in rows}


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert(job: Job, run_id: str) -> None:
    """
    Insert the job if new; if we've seen the job_key before, update
    last_seen and status only (never overwrite first_seen).
    """
    now = _now()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO jobs (job_key, url_hash, company, title, country,
                              status, run_id, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_key) DO UPDATE SET
                last_seen = excluded.last_seen,
                status    = excluded.status,
                run_id    = excluded.run_id
            """,
            (
                job.job_key,
                job.url_hash(),
                job.company,
                job.title,
                job.country,
                job.status.value,
                run_id,
                now,
                now,
            ),
        )


def mark_status(job: Job, status: Status, run_id: str) -> None:
    """Update the status of an already-recorded job."""
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET status = ?, last_seen = ?, run_id = ? WHERE job_key = ?",
            (status.value, _now(), run_id, job.job_key),
        )


# ---------------------------------------------------------------------------
# Bulk helpers
# ---------------------------------------------------------------------------

def filter_new(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    """
    Split a list into (new_jobs, duplicate_jobs) using a single DB round-trip.
    Much faster than calling is_duplicate() in a loop.
    """
    keys = known_keys()
    hashes = known_url_hashes()

    new: list[Job] = []
    dupes: list[Job] = []

    for job in jobs:
        if job.job_key in keys or job.url_hash() in hashes:
            dupes.append(job)
        else:
            new.append(job)

    log.info(
        "dedupe: %d new, %d duplicate (of %d total)",
        len(new), len(dupes), len(jobs),
    )
    return new, dupes


def record_batch(jobs: list[Job], run_id: str) -> None:
    """Upsert every job in the list — call this after scoring/gating.
    Jobs with Status.failed are skipped so they can be retried next run."""
    for job in jobs:
        if job.status != Status.failed:
            upsert(job, run_id)
