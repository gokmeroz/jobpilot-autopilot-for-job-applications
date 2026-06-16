"""Tests for src/ledger.py — uses a tmp SQLite database, never touches data/ledger.sqlite."""
from __future__ import annotations

from pathlib import Path

import pytest

import src.ledger as ledger
from src.models import Status
from tests.conftest import make_job


@pytest.fixture(autouse=True)
def tmp_ledger(tmp_path, monkeypatch):
    """Redirect ledger to a fresh temp database for every test."""
    db = tmp_path / "test_ledger.sqlite"
    monkeypatch.setattr(ledger, "_db_path", lambda: db)
    ledger.init()
    return db


# ── init ─────────────────────────────────────────────────────────────────────

def test_init_creates_db(tmp_path):
    db = tmp_path / "fresh.sqlite"
    import src.ledger as _l
    _l._db_path = lambda: db
    _l.init()
    assert db.exists()


# ── upsert + is_duplicate ─────────────────────────────────────────────────────

def test_new_job_is_not_duplicate():
    job = make_job(apply_url="https://example.com/jobs/1")
    assert not ledger.is_duplicate(job)


def test_job_is_duplicate_after_upsert():
    job = make_job(apply_url="https://example.com/jobs/2")
    ledger.upsert(job, "run-001")
    assert ledger.is_duplicate(job)


def test_duplicate_detected_by_url_hash():
    job_a = make_job(apply_url="https://example.com/jobs/3")
    job_b = make_job(
        job_key="different__key__de",
        apply_url="https://example.com/jobs/3",  # same URL
    )
    ledger.upsert(job_a, "run-001")
    assert ledger.is_duplicate(job_b)


def test_upsert_is_idempotent():
    job = make_job(apply_url="https://example.com/jobs/4")
    ledger.upsert(job, "run-001")
    ledger.upsert(job, "run-002")  # should not raise
    assert ledger.is_duplicate(job)


# ── filter_new ────────────────────────────────────────────────────────────────

def test_filter_new_splits_correctly():
    old = make_job(apply_url="https://example.com/jobs/10")
    new = make_job(
        job_key="new__job__gb",
        apply_url="https://example.com/jobs/11",
    )
    ledger.upsert(old, "run-001")

    fresh, dupes = ledger.filter_new([old, new])
    assert len(fresh) == 1
    assert len(dupes) == 1
    assert fresh[0].job_key == new.job_key
    assert dupes[0].job_key == old.job_key


def test_filter_new_all_new():
    jobs = [
        make_job(job_key=f"job{i}__x__de", apply_url=f"https://example.com/jobs/{i}")
        for i in range(3)
    ]
    fresh, dupes = ledger.filter_new(jobs)
    assert len(fresh) == 3
    assert len(dupes) == 0


def test_filter_new_all_dupes():
    jobs = [
        make_job(job_key=f"job{i}__x__de", apply_url=f"https://example.com/jobs/{i}")
        for i in range(3)
    ]
    for job in jobs:
        ledger.upsert(job, "run-001")

    fresh, dupes = ledger.filter_new(jobs)
    assert len(fresh) == 0
    assert len(dupes) == 3


# ── mark_status ───────────────────────────────────────────────────────────────

def test_mark_status_updates_record():
    job = make_job(apply_url="https://example.com/jobs/20")
    ledger.upsert(job, "run-001")
    ledger.mark_status(job, Status.applied, "run-001")

    keys = ledger.known_keys()
    assert job.job_key in keys


# ── record_batch ──────────────────────────────────────────────────────────────

def test_record_batch_skips_failed():
    good = make_job(apply_url="https://example.com/jobs/30", status=Status.scored)
    bad  = make_job(
        job_key="failed__job__de",
        apply_url="https://example.com/jobs/31",
        status=Status.failed,
    )
    ledger.record_batch([good, bad], "run-001")

    assert ledger.is_duplicate(good)
    assert not ledger.is_duplicate(bad)
