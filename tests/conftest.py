"""Shared fixtures for all test modules."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models import Job, RemoteType, Route, Status
from src.normalize import build_job_key


def make_job(**overrides) -> Job:
    """Return a minimal valid Job, applying any overrides."""
    defaults = dict(
        job_key     = build_job_key("Acme", "Software Engineer", "DE"),
        title       = "Software Engineer",
        company     = "Acme",
        country     = "DE",
        location    = "Berlin, Germany",
        remote      = RemoteType.onsite,
        posted_at   = datetime.now(timezone.utc),
        timestamp_trusted = True,
        source      = "test",
        source_tier = 1,
        ats         = "greenhouse",
        apply_url   = "https://boards.greenhouse.io/acme/jobs/123",
        language    = "EN",
        status      = Status.new,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture
def job():
    return make_job()
