"""
JSearch (RapidAPI) adapter.

Aggregates Google for Jobs → LinkedIn, Indeed, Glassdoor, ZipRecruiter in one call.
Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch

Requires env var: JSEARCH_API_KEY  (RapidAPI key)
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from src.config import env, load
from src.models import Job
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

_BASE = "https://jsearch.p.rapidapi.com/search"
SOURCE = "jsearch"
SOURCE_TIER = 2

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship|right\s+to\s+work\s+not\s+required)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_DE_WORDS = re.compile(r"\b(und|oder|mit|für|die|der|das|ein|eine|als|bei|von|zu)\b", re.I)

# Search queries: (query_string, country_hint)
# country_hint is used as fallback when JSearch doesn't return a country
_QUERIES: list[tuple[str, str]] = [
    ("junior software engineer Germany",        "DE"),
    ("graduate software engineer Germany",      "DE"),
    ("junior backend developer Germany",        "DE"),
    ("junior software engineer Netherlands",    "NL"),
    ("junior backend developer Netherlands",    "NL"),
    ("junior software engineer Ireland",        "IE"),
    ("junior software engineer United Kingdom", "GB"),
    ("junior backend developer United Kingdom", "GB"),
    ("junior software engineer remote",         "REMOTE"),
    ("backend developer junior remote",         "REMOTE"),
    ("AI engineer entry level remote",          "REMOTE"),
    ("full stack developer junior remote",      "REMOTE"),
]


def _parse_salary(raw: dict) -> str | None:
    lo = raw.get("job_min_salary")
    hi = raw.get("job_max_salary")
    cur = raw.get("job_salary_currency") or "USD"
    period = (raw.get("job_salary_period") or "YEAR").upper()
    if lo and hi:
        return f"{cur} {int(lo):,}–{int(hi):,}/{period[:2]}"
    if lo:
        return f"{cur} {int(lo):,}+/{period[:2]}"
    return None


def _parse_job(raw: dict, country_hint: str) -> Job | None:
    try:
        title     = (raw.get("job_title") or "").strip()
        company   = (raw.get("employer_name") or "").strip()
        apply_url = (raw.get("job_apply_link") or "").strip()

        if not title or not company or not apply_url:
            return None

        description = (raw.get("job_description") or "").strip()
        location    = ", ".join(filter(None, [
            raw.get("job_city"), raw.get("job_state"), raw.get("job_country")
        ]))
        is_remote   = bool(raw.get("job_is_remote"))
        raw_country = raw.get("job_country") or ""

        country = (
            country_from_location(raw_country or location)
            if (raw_country or location)
            else country_hint
        )
        if country == "UNKNOWN":
            country = country_hint

        remote_type = infer_remote("remote" if is_remote else location, description[:200])

        posted_str = raw.get("job_posted_at_datetime_utc")
        posted_at  = None
        if posted_str:
            try:
                posted_at = datetime.fromisoformat(posted_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None
        language    = "DE" if len(_DE_WORDS.findall(description[:500])) >= 4 else "EN"

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
            ats               = None,
            apply_url         = apply_url,
            salary            = _parse_salary(raw),
            language          = language,
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = bool(_VISA_RE.search(description)),
        )
    except Exception as exc:
        log.warning("jsearch: failed to parse job '%s': %s", raw.get("job_title"), exc)
        return None


def _fetch_query(query: str, country_hint: str, api_key: str,
                 num_pages: int = 1, request_delay: float = 0.5) -> list[Job]:
    jobs: list[Job] = []
    for page in range(1, num_pages + 1):
        try:
            resp = requests.get(
                _BASE,
                headers={
                    "X-RapidAPI-Key": api_key,
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
                params={
                    "query":       query,
                    "page":        page,
                    "num_pages":   1,
                    "date_posted": "3days",
                },
                timeout=20,
            )
            if resp.status_code == 429:
                log.warning("jsearch: rate limited on query '%s' page %d", query, page)
                break
            resp.raise_for_status()
            data = resp.json().get("data") or []
        except Exception as exc:
            log.error("jsearch: query '%s' page %d failed: %s", query, page, exc)
            break

        for raw in data:
            job = _parse_job(raw, country_hint)
            if job:
                jobs.append(job)

        if not data:
            break
        if page < num_pages and request_delay > 0:
            time.sleep(request_delay)

    return jobs


def fetch(
    queries: list[tuple[str, str]] | None = None,
    num_pages: int = 1,
    request_delay: float = 0.6,
) -> list[Job]:
    """
    Fetch jobs from JSearch (RapidAPI). Requires JSEARCH_API_KEY env var.
    Silently returns [] if the key is missing.
    """
    api_key = env("JSEARCH_API_KEY")
    if not api_key:
        log.info("jsearch: JSEARCH_API_KEY not set — skipping")
        return []

    matrix = queries or _QUERIES
    log.info("jsearch: running %d queries", len(matrix))

    seen: set[str] = set()
    all_jobs: list[Job] = []

    for query, country_hint in matrix:
        jobs = _fetch_query(query, country_hint, api_key, num_pages, request_delay)
        for job in jobs:
            if job.job_key not in seen:
                seen.add(job.job_key)
                all_jobs.append(job)
        if request_delay > 0:
            time.sleep(request_delay)

    log.info("jsearch: %d unique jobs from %d queries", len(all_jobs), len(matrix))
    return all_jobs
