"""
Wellfound (formerly AngelList Talent) job discovery via Apify.

Wellfound has no public API — uses the Apify actor configured at
apify.wellfound_actor_id in config.yaml. Defaults to 'bebity/wellfound-jobs-scraper'.

All queries target entry-level/junior roles in EU and remote markets.
Startup-heavy signal: every job here is from a startup or growth company.

Requires env var: APIFY_TOKEN
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from src.config import load
from src.discover.scrapers.apify_runner import run_actor
from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

SOURCE = "wellfound"
SOURCE_TIER = 2  # Startup-focused; equity + salary transparency

_DEFAULT_ACTOR = "bebity/wellfound-jobs-scraper"

# ATS fingerprinting — same map as LinkedIn adapter
_ATS_MAP: dict[str, str] = {
    "greenhouse.io":       "greenhouse",
    "lever.co":            "lever",
    "ashbyhq.com":         "ashby",
    "workable.com":        "workable",
    "smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com":   "workday",
    "jobvite.com":         "jobvite",
    "icims.com":           "icims",
}

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship|right\s+to\s+work\s+not\s+required)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)

# Search queries targeting entry-level roles in priority markets
_SEARCH_QUERIES: list[dict] = [
    # EU relocation targets
    {"query": "junior software engineer",   "location": "Germany",     "remote": False},
    {"query": "backend engineer",           "location": "Germany",     "remote": False},
    {"query": "junior software engineer",   "location": "Netherlands", "remote": False},
    {"query": "junior software engineer",   "location": "Ireland",     "remote": False},
    {"query": "junior software engineer",   "location": "United Kingdom", "remote": False},
    # Remote worldwide
    {"query": "software engineer",          "location": "",            "remote": True},
    {"query": "backend engineer",           "location": "",            "remote": True},
    {"query": "fullstack engineer",         "location": "",            "remote": True},
    {"query": "AI engineer entry level",    "location": "",            "remote": True},
]


def _fingerprint_ats(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        for domain, ats in _ATS_MAP.items():
            if domain in host:
                return ats
    except Exception:
        pass
    return None


def _parse_posted_at(raw: dict) -> datetime | None:
    for field in ("postedAt", "posted_at", "createdAt", "created_at", "date"):
        val = raw.get(field)
        if val and isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val)
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _get(raw: dict, *keys: str) -> str:
    for key in keys:
        val = raw.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _parse_salary(raw: dict) -> str | None:
    # Wellfound often returns equity + salary as a combined string
    for field in ("salary", "compensation", "salaryRange", "equity"):
        val = raw.get(field)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _parse_job(raw: dict) -> Job | None:
    try:
        title   = _get(raw, "title", "jobTitle", "position")
        company = _get(raw, "companyName", "company", "organizationName")
        apply_url = _get(raw, "applyUrl", "apply_url", "url", "link", "jobUrl")

        if not title or not company or not apply_url:
            return None

        location    = _get(raw, "location", "jobLocation")
        description = _get(raw, "descriptionText", "description", "body")
        posted_at   = _parse_posted_at(raw)
        country     = country_from_location(location) if location else "REMOTE"
        remote_type = infer_remote(location, description[:300])
        ats         = _fingerprint_ats(apply_url)
        visa_signal = bool(_VISA_RE.search(description))
        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None
        salary      = _parse_salary(raw)

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = location or None,
            remote            = remote_type,
            posted_at         = posted_at,
            timestamp_trusted = posted_at is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = ats,
            apply_url         = apply_url,
            salary            = salary,
            language          = "EN",
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("wellfound: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


def fetch(queries: list[dict] | None = None) -> list[Job]:
    """
    Discover Wellfound jobs via Apify actor.

    Requires APIFY_TOKEN env var. Set apify.wellfound_actor_id in config.yaml
    to override the default actor ('bebity/wellfound-jobs-scraper').
    """
    cfg      = load("config")
    apify    = cfg.get("apify", {})
    actor_id = apify.get("wellfound_actor_id", _DEFAULT_ACTOR)
    timeout  = apify.get("timeout_secs", 300)

    search_list = queries or _SEARCH_QUERIES
    log.info("wellfound: starting Apify run — %d queries, actor=%s", len(search_list), actor_id)

    actor_input = {
        "searches": search_list,
        "maxItems": max(25, len(search_list) * 25),
    }

    try:
        raw_items = run_actor(actor_id, actor_input, timeout_secs=timeout)
    except RuntimeError as exc:
        log.error("wellfound: actor run failed: %s", exc)
        return []

    seen: set[str] = set()
    all_jobs: list[Job] = []

    for raw in raw_items:
        job = _parse_job(raw)
        if job and job.job_key not in seen:
            seen.add(job.job_key)
            all_jobs.append(job)

    log.info("wellfound: %d unique jobs from %d raw items", len(all_jobs), len(raw_items))
    return all_jobs
