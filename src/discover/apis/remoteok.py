"""
RemoteOK JSON API adapter.

Public API — no auth required, but needs a User-Agent header.
GET https://remoteok.com/api

Returns a JSON array; first element is a legal notice, rest are job objects.
All jobs are remote. Good for worldwide/US remote signal.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, fingerprint_ats

log = logging.getLogger(__name__)

_URL = "https://remoteok.com/api"
SOURCE = "remoteok"
SOURCE_TIER = 2

_HEADERS = {
    "User-Agent": "JobPilot/1.0 (job search automation; contact@jobpilot.dev)",
    "Accept": "application/json",
}

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance)|work\s+permit|"
    r"we\s+sponsor|sponsorship)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)

# Tags that indicate senior/non-entry-level
_SENIOR_TAGS = {"senior", "lead", "principal", "staff", "vp", "director", "manager"}

# Tags relevant to the candidate's stack
_RELEVANT_TAGS = {
    "node", "nodejs", "express", "nestjs",
    "react", "typescript", "nextjs",
    "python", "fastapi", "flask", "django",
    "java", "spring",
    "mongodb", "postgres", "postgresql",
    "aws", "docker",
    "openai", "llm", "ai", "ml",
    "saas", "startup",
}


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(html: str) -> str:
    s = _Stripper()
    s.feed(html)
    return re.sub(r"\s{2,}", " ", s.get_text())


def _parse_salary(raw: dict) -> str | None:
    lo = raw.get("salary_min")
    hi = raw.get("salary_max")
    if lo and hi:
        return f"USD {lo:,}–{hi:,}/yr"
    if lo:
        return f"USD {lo:,}+/yr"
    return None


def _parse_posted_at(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _has_relevant_tag(tags: list[str]) -> bool:
    lowered = {t.lower() for t in tags}
    return bool(lowered & _RELEVANT_TAGS)


def _parse_job(raw: dict) -> Job | None:
    try:
        # Skip the legal notice object (no "position" field)
        title   = (raw.get("position") or "").strip()
        company = (raw.get("company") or "").strip()
        apply_url = (raw.get("apply_url") or raw.get("url") or "").strip()

        if not title or not company or not apply_url:
            return None

        tags        = raw.get("tags") or []
        description = _strip_html(raw.get("description") or "")
        location    = (raw.get("location") or "Worldwide").strip()

        # Skip if tags clearly signal senior roles
        tag_set = {t.lower() for t in tags}
        if tag_set & _SENIOR_TAGS and "junior" not in tag_set and "entry" not in tag_set:
            return None

        # Soft relevance filter — keep if any stack tag matches or tags are absent
        if tags and not _has_relevant_tag(tags):
            return None

        posted_at   = _parse_posted_at(raw.get("date"))
        country     = country_from_location(location)
        salary      = _parse_salary(raw)
        visa_signal = bool(_VISA_RE.search(description))
        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = location or None,
            remote            = RemoteType.remote,
            posted_at         = posted_at,
            timestamp_trusted = posted_at is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = fingerprint_ats(apply_url),
            apply_url         = apply_url,
            salary            = salary,
            language          = "EN",
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("remoteok: failed to parse job '%s': %s", raw.get("position"), exc)
        return None


def fetch() -> list[Job]:
    """Fetch all current jobs from RemoteOK."""
    try:
        resp = requests.get(_URL, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        payload: list[dict] = resp.json()
    except Exception as exc:
        log.error("remoteok: fetch failed: %s", exc)
        return []

    # First element is legal notice — skip it
    raw_jobs = payload[1:] if payload else []
    jobs = [j for raw in raw_jobs if isinstance(raw, dict) and (j := _parse_job(raw))]
    log.info("remoteok: %d relevant jobs (from %d total)", len(jobs), len(raw_jobs))
    return jobs
