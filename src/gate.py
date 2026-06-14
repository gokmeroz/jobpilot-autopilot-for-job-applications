from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from src.config import load
from src.models import Job, Status

log = logging.getLogger(__name__)

# Title fragments that indicate a clearly senior role.
# Checked as whole words (case-insensitive) against the job title.
_SENIOR_PATTERNS = re.compile(
    r"\b("
    r"senior|sr\.?|staff|lead|principal|architect|"
    r"head of|vp|vice president|director|manager|"
    r"engineering manager|em\b"
    r")\b",
    re.IGNORECASE,
)

# Explicit junior signals — if present we skip the senior-title check.
_JUNIOR_PATTERNS = re.compile(
    r"\b(junior|jr\.?|graduate|grad|new.?grad|associate|entry.?level|trainee|apprentice)\b",
    re.IGNORECASE,
)


def _gate_age(job: Job, max_age_hours: float) -> str | None:
    """Return a reason string if the job is too old, else None."""
    if not job.timestamp_trusted or job.posted_at is None:
        return None  # unknown age → let it through
    age = job.age_hours()
    if age is not None and age > max_age_hours:
        return f"too old ({age:.0f}h > {max_age_hours}h)"
    return None


def _gate_yoe(job: Job, max_yoe: int) -> str | None:
    """Return a reason string if the role requires more YOE than allowed."""
    if job.yoe_max is not None and job.yoe_max > max_yoe:
        return f"yoe_max={job.yoe_max} exceeds limit={max_yoe}"
    return None


def _gate_seniority(job: Job) -> str | None:
    """Return a reason string if the title is clearly senior and has no junior qualifier."""
    if _JUNIOR_PATTERNS.search(job.title):
        return None  # explicit junior signal overrides any senior word
    if _SENIOR_PATTERNS.search(job.title):
        return f"senior title: '{job.title}'"
    return None


def _gate_language(job: Job, allowed: list[str], german_ok: bool) -> str | None:
    """Return a reason string if the posting language is not acceptable."""
    lang = (job.language or "EN").upper()

    if lang in [a.upper() for a in allowed]:
        return None

    # German postings are acceptable if german_ok and company is in DE
    if german_ok and lang == "DE" and job.country == "DE":
        return None

    return f"language '{lang}' not in allowed {allowed}"


def check(job: Job) -> Job:
    """Run all gate checks on a single job. Returns the job with status updated."""
    cfg = load("config")["gate"]

    checks = [
        _gate_age(job, cfg["max_age_hours"]),
        _gate_yoe(job, cfg["max_yoe"]),
        _gate_seniority(job),
        _gate_language(job, cfg["languages"], cfg.get("german_ok", False)),
    ]

    for reason in checks:
        if reason:
            log.debug("gated out %s @ %s — %s", job.title, job.company, reason)
            return job.model_copy(update={
                "status": Status.gated_out,
                "gate_reason": reason,
            })

    return job


def run(jobs: list[Job]) -> tuple[list[Job], list[Job]]:
    """
    Gate a list of jobs.
    Returns (passed, rejected) — both lists preserve original order within each group.
    """
    passed: list[Job] = []
    rejected: list[Job] = []

    for job in jobs:
        result = check(job)
        if result.status == Status.gated_out:
            rejected.append(result)
        else:
            passed.append(result)

    log.info(
        "gate: %d passed, %d rejected (of %d total)",
        len(passed), len(rejected), len(jobs),
    )
    return passed, rejected
