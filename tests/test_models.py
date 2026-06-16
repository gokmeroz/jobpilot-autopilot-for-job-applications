"""Tests for src/models.py — Job helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import make_job


class TestUrlHash:
    def test_is_16_chars(self):
        job = make_job(apply_url="https://example.com/jobs/1")
        assert len(job.url_hash()) == 16

    def test_deterministic(self):
        job = make_job(apply_url="https://example.com/jobs/1")
        assert job.url_hash() == job.url_hash()

    def test_different_urls_different_hashes(self):
        job_a = make_job(apply_url="https://example.com/jobs/1")
        job_b = make_job(apply_url="https://example.com/jobs/2")
        assert job_a.url_hash() != job_b.url_hash()

    def test_same_url_same_hash(self):
        job_a = make_job(apply_url="https://example.com/jobs/99")
        job_b = make_job(job_key="other__key__gb", apply_url="https://example.com/jobs/99")
        assert job_a.url_hash() == job_b.url_hash()


class TestAgeHours:
    def test_age_calculated_correctly(self):
        posted = datetime.now(timezone.utc) - timedelta(hours=24)
        job = make_job(posted_at=posted, timestamp_trusted=True)
        age = job.age_hours()
        assert age is not None
        assert 23.9 < age < 24.1

    def test_none_when_no_posted_at(self):
        job = make_job(posted_at=None, timestamp_trusted=False)
        assert job.age_hours() is None

    def test_custom_now_param(self):
        posted = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        now    = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        job = make_job(posted_at=posted)
        assert job.age_hours(now=now) == 24.0

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 1, 1, 0, 0, 0)  # no tzinfo
        now   = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        job = make_job(posted_at=naive)
        assert job.age_hours(now=now) == 12.0
