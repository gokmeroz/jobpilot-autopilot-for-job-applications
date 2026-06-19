"""
Remotive remote jobs API adapter.

Public API — no auth required.
GET https://remotive.com/api/remote-jobs?category={cat}&limit=100

Docs: https://remotive.com/api/remote-jobs
All jobs are 100% remote — good signal for remote-worldwide candidates.
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

_BASE = "https://remotive.com/api/remote-jobs"
SOURCE = "remotive"
SOURCE_TIER = 2

# Categories relevant to the candidate profile
_CATEGORIES = [
    "software-dev",
    "devops-sysadmin",
    "product-management",
    "data",
]

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance)|work\s+permit|"
    r"we\s+sponsor|sponsorship)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)


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
    salary = (raw.get("salary") or "").strip()
    return salary or None


def _parse_posted_at(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        # Remotive: "2024-01-01T00:00:00"
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_job(raw: dict) -> Job | None:
    try:
        title        = (raw.get("title") or "").strip()
        company      = (raw.get("company_name") or "").strip()
        apply_url    = (raw.get("url") or "").strip()

        if not title or not company or not apply_url:
            return None

        description  = _strip_html(raw.get("description") or "")
        candidate_loc = (raw.get("candidate_required_location") or "Worldwide").strip()
        tags          = raw.get("tags") or []
        job_type      = (raw.get("job_type") or "").lower()

        # All Remotive jobs are remote; location field indicates where candidate can be
        posted_at    = _parse_posted_at(raw.get("publication_date"))
        country      = country_from_location(candidate_loc) if candidate_loc else "REMOTE"
        salary       = _parse_salary(raw)
        visa_signal  = bool(_VISA_RE.search(description))
        yoe_matches  = _YOE_RE.findall(description)
        yoe_max      = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

        # Skip part-time/contract if clearly not full-time
        if job_type and "part" in job_type:
            return None

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = candidate_loc or None,
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
        log.warning("remotive: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


def _fetch_category(category: str, limit: int = 100) -> list[Job]:
    try:
        resp = requests.get(
            _BASE,
            params={"category": category, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.error("remotive: failed to fetch category '%s': %s", category, exc)
        return []

    raw_jobs: list[dict] = payload.get("jobs") or []
    jobs = [j for raw in raw_jobs if (j := _parse_job(raw))]
    log.info("remotive: category=%s → %d jobs", category, len(jobs))
    return jobs


def fetch(categories: list[str] | None = None, limit: int = 100) -> list[Job]:
    """Fetch recent remote jobs from Remotive across relevant categories."""
    targets = categories or _CATEGORIES
    seen: set[str] = set()
    all_jobs: list[Job] = []

    for cat in targets:
        for job in _fetch_category(cat, limit):
            if job.job_key not in seen:
                seen.add(job.job_key)
                all_jobs.append(job)

    log.info("remotive: %d unique jobs across %d categories", len(all_jobs), len(targets))
    return all_jobs
