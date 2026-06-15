from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from src.config import load
from src.models import Job, Status

log = logging.getLogger(__name__)

# Must match at least one of these to be considered a tech/SWE role.
# Checked against the job title (case-insensitive).
# "engineer" alone is intentionally NOT here — it matches sysadmin, project,
# business-systems, requirements, and hardware roles. Must be qualified.
_TECH_TITLE_RE = re.compile(
    r"\b("
    # explicit software engineer / developer
    r"software\s+(?:engineer|developer)|"
    # domain-qualified engineer / developer
    r"(?:backend|back[\s\-]?end|frontend|front[\s\-]?end|"
    r"fullstack|full[\s\-]?stack|platform|cloud|mobile|"
    r"infrastructure|embedded|applications?)\s+(?:engineer|developer)|"
    # technology-prefixed engineer / developer (language / framework)
    r"(?:node(?:\.js)?|react(?:\.js)?|next(?:\.js)?|vue(?:\.js)?|angular|"
    r"typescript|javascript|python|java(?:script)?|kotlin|swift|ruby|rails|"
    r"golang|go|rust|php|django|fastapi|spring|laravel|elixir|scala|clojure)"
    r"\s+(?:engineer|developer)|"
    # devops / SRE
    r"(?:devops|dev[\s\-]ops)\b|\bsre\b|site\s+reliability\s+engineer|"
    # AI / ML / LLM
    r"(?:ai|ml|llm|machine[\s\-]?learning|deep[\s\-]?learning|"
    r"generative[\s\-]ai|applied[\s\-]ai|data)\s+(?:engineer|scientist)|"
    r"ai\s+[\w\-]+\s+engineer|"      # AI [qualifier] Engineer: AI Integration, AI Application…
    # product / solutions
    r"product\s+engineer|solutions?\s+engineer|"
    # developer-adjacent
    r"developer\s+advocate|technical\s+consultant|"
    # bare "developer" / "programmer" — almost always SWE in practice
    r"developer|programmer|coder|"
    # German SWE terms (NOT systemingenieur / systemadmin / systemintegration)
    r"softwareentwicklung|softwareentwickler|entwickler|programmierer|fachinformatiker"
    r")\b",
    re.IGNORECASE,
)

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


# Title keywords that indicate industrial / defence / ERP domains the
# candidate explicitly does not target — checked after _gate_role passes.
_DOMAIN_DENYLIST_RE = re.compile(
    r"\b("
    r"scada|"
    r"oil\s+[&+]?\s*gas|"
    r"(?:radar|sonar|lidar)\s+software|radar\s+engineer|"
    r"machinebouw|"
    r"sps[\s\-]entwickler|sps[\s\-]developer|"   # SPS = PLC (industrial)
    r"leittechnik|steuerungstechnik|"
    r"delphi\s+(?:software\w*|developer|entwickler)|"
    r"ms[\s\-]dynamics|dynamics[\s\-](?:ax|nav|bc)\b|dynamics\s+business\s+central|"
    r"oracle\s+apex|"
    r"odoo\b|"
    r"servicenow\b|"
    r"navision\b"
    r")\b",
    re.IGNORECASE,
)


def _gate_role(job: Job) -> str | None:
    """Return a reason string if the title has no tech/SWE signal."""
    if _TECH_TITLE_RE.search(job.title):
        return None
    return f"non-tech title: '{job.title}'"


def _gate_domain(job: Job) -> str | None:
    """Reject industrial/ERP/defence domain titles that don't match the candidate's target stack."""
    m = _DOMAIN_DENYLIST_RE.search(job.title)
    if m:
        return f"domain denylist match '{m.group()}' in title: '{job.title}'"
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
        _gate_role(job),
        _gate_domain(job),
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
