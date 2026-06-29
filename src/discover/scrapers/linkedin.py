"""
LinkedIn job discovery via Apify scraper.

Uses the curious_coder/linkedin-jobs-scraper actor (configurable via
config.yaml apify.actor_id). Runs a matrix of role+location queries
covering all target markets from CLAUDE.md, then parses results into
Job objects.

Requires env var: APIFY_TOKEN
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from src.config import load
from src.discover.scrapers.apify_runner import run_actor
from src.models import Job, RemoteType
from src.normalize import _ATS_DOMAINS, build_job_key, country_from_location, fingerprint_ats as _fingerprint_ats, infer_remote

log = logging.getLogger(__name__)

SOURCE = "linkedin"
SOURCE_TIER = 1

# (keywords, location) — location="" means LinkedIn searches globally / remote
_SEARCH_MATRIX: list[tuple[str, str]] = [
    # Germany — primary relocation target
    ("junior software engineer",         "Germany"),
    ("graduate software engineer",        "Germany"),
    ("backend developer junior",          "Germany"),
    ("full stack developer junior",       "Germany"),
    ("AI engineer entry level",           "Germany"),
    # Netherlands
    ("junior software engineer",         "Netherlands"),
    ("backend developer junior",          "Netherlands"),
    # Ireland
    ("junior software engineer",         "Ireland"),
    # United Kingdom
    ("junior software engineer",         "United Kingdom"),
    ("backend developer junior",          "United Kingdom"),
    # Remote worldwide / US remote
    ("junior software engineer remote",  ""),
    ("backend developer remote",         ""),
    ("full stack engineer remote",       ""),
    # Turkey (fallback market)
    ("junior software engineer",         "Turkey"),
]

# LinkedIn URL filter params
# f_TPR: r86400=24h, r604800=week
# f_E:   1=internship, 2=entry level, 3=associate
# f_JT:  F=full-time
_LI_BASE = "https://www.linkedin.com/jobs/search/"
_LI_PARAMS = "f_TPR=r86400&f_E=2%2C3&f_JT=F"

# Derived from normalize._ATS_DOMAINS so it stays in sync with all registered fillers.
_ATS_MAP = _ATS_DOMAINS

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship|right\s+to\s+work\s+not\s+required|"
    r"visa\s+support|relocation\s+provided)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_DE_WORDS = re.compile(r"\b(und|oder|mit|für|die|der|das|ein|eine|als|bei|von|zu)\b", re.I)

# Relative date strings LinkedIn/Apify sometimes returns instead of ISO dates
_RELATIVE_RE = re.compile(
    r"(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago",
    re.IGNORECASE,
)
_UNIT_HOURS = {
    "second": 1 / 3600,
    "minute": 1 / 60,
    "hour":   1,
    "day":    24,
    "week":   168,
    "month":  720,
}


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _parse_posted_at(raw_date: str | None) -> datetime | None:
    if not raw_date:
        return None

    # Try ISO format first
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw_date.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(raw_date.strip())
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Try relative: "3 hours ago", "1 day ago"
    m = _RELATIVE_RE.search(raw_date)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        hours = n * _UNIT_HOURS.get(unit, 0)
        return datetime.now(timezone.utc) - timedelta(hours=hours)

    if "just" in raw_date.lower() or "now" in raw_date.lower():
        return datetime.now(timezone.utc)

    return None


def _get(raw: dict, *keys: str, default: str = "") -> str:
    """Try multiple key names, return first non-empty hit."""
    for key in keys:
        val = raw.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return default



def _parse_remote_type(raw: dict, location: str, description: str) -> RemoteType:
    # Actor output has no explicit workplaceType field; infer from location string
    # (LinkedIn often puts "(Remote)" or "Anywhere" in the location when remote)
    return infer_remote(location, description[:300])


def _parse_salary(raw: dict) -> str | None:
    val = _get(raw, "salary")
    return val or None


# ---------------------------------------------------------------------------
# Job parser
# ---------------------------------------------------------------------------

def _parse_job(raw: dict) -> Job | None:
    try:
        title   = _get(raw, "title")
        company = _get(raw, "companyName")

        # Prefer external apply URL; fall back to LinkedIn job page URL
        apply_url = _get(raw, "applyUrl") or _get(raw, "link")
        if not apply_url or not title or not company:
            return None

        location    = _get(raw, "location")
        description = _get(raw, "descriptionText", "descriptionHtml")
        raw_date    = _get(raw, "postedAt")

        posted_at   = _parse_posted_at(raw_date)
        country     = country_from_location(location) if location else "REMOTE"
        remote_type = _parse_remote_type(raw, location, description)
        language    = "DE" if len(_DE_WORDS.findall(description[:500])) >= 4 else "EN"
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
            language          = language,
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("linkedin: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


# ---------------------------------------------------------------------------
# Actor input builder
# ---------------------------------------------------------------------------

def _build_search_url(keywords: str, location: str) -> str:
    from urllib.parse import urlencode
    params = {"keywords": keywords, **({} if not location else {"location": location})}
    return f"{_LI_BASE}?{urlencode(params)}&{_LI_PARAMS}"


def _build_actor_input(
    search_matrix: list[tuple[str, str]],
    pages_per_query: int,
) -> dict:
    """
    Build the input payload for curious_coder/linkedin-jobs-scraper.

    Schema (from actor build h6gmDeUthEKsvngoE):
      urls          — required, list of LinkedIn jobs search page URLs
      scrapeCompany — bool, adds extra requests per job (skip for speed)
      count         — integer, total jobs to collect across all URLs
    """
    urls = [
        _build_search_url(keywords, location)
        for keywords, location in search_matrix
    ]
    return {
        "urls": urls,
        "scrapeCompany": False,
        "count": max(10, len(urls) * 25 * pages_per_query),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch(
    search_matrix: list[tuple[str, str]] | None = None,
) -> list[Job]:
    """
    Discover LinkedIn jobs via Apify. Runs a matrix of role+location queries,
    deduplicates by job_key, and returns Job objects (unfiltered — gate handles
    age/seniority/language checks).
    """
    cfg      = load("config")
    apify    = cfg.get("apify", {})
    actor_id = apify.get("actor_id", "bebity/linkedin-jobs-scraper")
    timeout  = apify.get("timeout_secs", 300)
    pages    = apify.get("pages_per_query", 1)

    matrix   = search_matrix or _SEARCH_MATRIX
    log.info("linkedin: starting Apify run — %d queries, actor=%s", len(matrix), actor_id)

    actor_input = _build_actor_input(matrix, pages)

    try:
        raw_items = run_actor(actor_id, actor_input, timeout_secs=timeout)
    except RuntimeError as exc:
        log.error("linkedin: actor run failed: %s", exc)
        return []

    seen:     set[str] = set()
    all_jobs: list[Job] = []

    for raw in raw_items:
        job = _parse_job(raw)
        if job and job.job_key not in seen:
            seen.add(job.job_key)
            all_jobs.append(job)

    log.info(
        "linkedin: %d unique jobs parsed from %d raw items",
        len(all_jobs), len(raw_items),
    )
    return all_jobs
