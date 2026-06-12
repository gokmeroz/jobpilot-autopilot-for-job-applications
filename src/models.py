"""Typed data contracts. Every stage reads/writes these models as JSONL."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256

from pydantic import BaseModel, Field


class RemoteType(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    unknown = "unknown"


class Route(str, Enum):
    auto = "auto"
    manual = "manual"


class Status(str, Enum):
    new = "new"
    gated_out = "gated_out"
    scored = "scored"
    planned = "planned"
    applied = "applied"
    queued = "queued"
    skipped = "skipped"
    failed = "failed"


class Score(BaseModel):
    role: float
    stack: float
    seniority: float
    visa: float
    culture: float
    total: float
    rationale: str = ""


class Job(BaseModel):
    job_key: str
    title: str
    company: str
    country: str = "REMOTE"               # ISO2 or REMOTE
    location: str | None = None
    remote: RemoteType = RemoteType.unknown

    posted_at: datetime | None = None
    timestamp_trusted: bool = False        # only tier 1/2 adapters set True

    source: str
    source_tier: int
    ats: str | None = None
    apply_url: str
    salary: str | None = None
    language: str = "EN"
    description: str = ""

    yoe_max: int | None = None
    visa_signal: bool = False

    score: Score | None = None
    route: Route | None = None
    cover_letter: str | None = None        # path chosen at plan stage
    status: Status = Status.new
    gate_reason: str | None = None
    fail_reason: str | None = None

    def url_hash(self) -> str:
        return sha256(self.apply_url.encode()).hexdigest()[:16]

    def age_hours(self, now: datetime | None = None) -> float | None:
        if self.posted_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        posted = self.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return (now - posted).total_seconds() / 3600


class ApplicationRecord(BaseModel):
    job_key: str
    url_hash: str
    company: str
    title: str
    country: str
    status: Status
    run_id: str
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cover_letter: str | None = None
    evidence_dir: str | None = None
    reason: str | None = None
