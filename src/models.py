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
    """0–100 fit score (weights: tech 30, experience 25, seniority 20,
    alignment 15, location 10). Visa sponsorship is NOT inside it — see
    Sponsorship. Legacy fields (role/stack/visa/culture) stay for old
    review files replayed with --resume-review."""
    total: float
    rationale: str = ""
    tech: float = 0.0
    experience: float = 0.0
    seniority: float = 0.0
    alignment: float = 0.0
    location: float = 0.0
    role: float = 0.0
    stack: float = 0.0
    visa: float = 0.0
    culture: float = 0.0


class FitDetail(BaseModel):
    fit_class: str = ""                    # Strong / Good / Stretch / Not recommended
    career_path: str = ""                  # ai_ml / backend_fullstack / forward_deployed / graduate
    matching_skills: list[str] = Field(default_factory=list)
    missing_mandatory: list[str] = Field(default_factory=list)
    missing_preferred: list[str] = Field(default_factory=list)
    experience_required: str = ""
    strongest_project: str = ""
    positioning: str = ""
    risks: list[str] = Field(default_factory=list)
    hard_blocker: str | None = None        # incompatible mandatory condition
    language_requirement: str = ""


class Sponsorship(BaseModel):
    category: str = "C"                    # A explicit / B possible / C unknown / D restricted / "-" not needed
    evidence: str = ""
    evidence_url: str | None = None
    evidence_type: str = ""                # posting / sponsor_register / directory / board / llm_quote
    offshore: str = "Unknown"              # Confirmed / Possible / Unknown / Restricted
    visa_pathway: str = ""
    salary_eligibility: str = ""           # AU only: meets CSIT / below CSIT / unverified
    verified_at: str = ""


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
    fit: FitDetail | None = None
    sponsorship: Sponsorship | None = None
    recommended_resume: str | None = None  # "ai" | "swe"
    verification: str | None = None        # open / closed / unverified (+ reason)
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
