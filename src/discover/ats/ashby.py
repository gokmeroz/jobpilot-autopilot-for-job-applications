"""
Ashby Job Board API adapter.

Public endpoint — no auth required.
GET https://api.ashbyhq.com/posting-api/job-board/{handle}

Returns all open postings for a company in one call.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from src.config import load
from src.models import Job
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

_BASE = "https://api.ashbyhq.com/posting-api/job-board/{handle}"
SOURCE = "ashby"
SOURCE_TIER = 1
REQUEST_DELAY = 0.4

# Company display name → Ashby board handle — verified slugs only
COMPANIES: dict[str, str] = {
    "Supabase":     "supabase",
    "Linear":       "linear",
    "Vercel":       "vercel",
    "Resend":       "resend",
    "Vapi":         "vapi",
    "Raycast":      "raycast",
    # Migrated from Lever / newly added
    "PostHog":      "posthog",
    "Zapier":       "zapier",
    "Miro":         "miro",
    "Airtable":     "airtable",
    "Loom":         "loom",
}

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship|right\s+to\s+work\s+not\s+required)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_DE_WORDS = re.compile(r"\b(und|oder|mit|für|die|der|das|ein|eine|als|bei|von|zu)\b", re.I)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s{2,}", " ", text).strip()


def _parse_salary(comp: dict | None) -> str | None:
    if not comp:
        return None
    lo = comp.get("minValue")
    hi = comp.get("maxValue")
    currency = comp.get("currency", "USD")
    interval = (comp.get("interval") or "Year").lower()
    if lo and hi:
        return f"{currency} {lo:,}–{hi:,}/{interval[:2]}"
    if lo:
        return f"{currency} {lo:,}+/{interval[:2]}"
    return None


def _parse_job(raw: dict, company: str) -> Job | None:
    try:
        title     = (raw.get("title") or "").strip()
        apply_url = (raw.get("applicationFormUrl") or raw.get("jobUrl") or "").strip()

        if not title or not apply_url:
            return None

        location_name = (raw.get("locationName") or raw.get("location") or "").strip()
        is_remote     = bool(raw.get("isRemote"))

        description_html  = raw.get("descriptionHtml") or ""
        description_plain = raw.get("descriptionPlain") or ""
        description = description_plain or _strip_tags(description_html)

        published = raw.get("publishedDate") or raw.get("publishedAt")
        posted_at = None
        if published:
            try:
                posted_at = datetime.fromisoformat(published).astimezone(timezone.utc)
            except ValueError:
                pass

        country     = country_from_location(location_name) if location_name else "REMOTE"
        remote_type = infer_remote(
            "remote" if is_remote else location_name,
            description[:200],
        )
        language    = "DE" if len(_DE_WORDS.findall(description[:500])) >= 4 else "EN"
        visa_signal = bool(_VISA_RE.search(description))
        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None
        salary      = _parse_salary(raw.get("compensation"))

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = location_name or None,
            remote            = remote_type,
            posted_at         = posted_at,
            timestamp_trusted = posted_at is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = "ashby",
            apply_url         = apply_url,
            salary            = salary,
            language          = language,
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("ashby: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


def _fetch_company(company: str, handle: str) -> list[Job]:
    url = _BASE.format(handle=handle)
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            log.warning("ashby: board '%s' not found (404) — skipping", handle)
            return []
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        log.error("ashby: failed to fetch %s (%s): %s", company, handle, exc)
        return []

    # Ashby returns jobs under "jobs" or "jobPostings"
    raw_jobs: list[dict] = payload.get("jobs") or payload.get("jobPostings") or []
    jobs = [j for raw in raw_jobs if (j := _parse_job(raw, company))]
    log.info("ashby: %s → %d jobs", company, len(jobs))
    return jobs


def fetch(
    companies: dict[str, str] | None = None,
    request_delay: float = REQUEST_DELAY,
) -> list[Job]:
    """Fetch open jobs from all target companies on Ashby."""
    cfg = load("config")
    max_age_hours: float = cfg["gate"]["max_age_hours"]
    targets = companies or COMPANIES

    all_jobs: list[Job] = []

    for i, (company, handle) in enumerate(targets.items()):
        jobs = _fetch_company(company, handle)

        fresh = [
            j for j in jobs
            if not j.timestamp_trusted
            or j.age_hours() is None
            or (j.age_hours() or 0) <= max_age_hours
        ]
        if len(jobs) - len(fresh):
            log.debug("ashby: dropped %d stale jobs from %s", len(jobs) - len(fresh), company)

        all_jobs.extend(fresh)

        if i < len(targets) - 1 and request_delay > 0:
            time.sleep(request_delay)

    log.info("ashby: %d fresh jobs across %d companies", len(all_jobs), len(targets))
    return all_jobs
