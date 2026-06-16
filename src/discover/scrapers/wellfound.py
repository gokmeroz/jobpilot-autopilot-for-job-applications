"""
Wellfound (formerly AngelList Talent) job discovery via Apify.

Uses the crawlerbros/wellfound-scraper actor (configurable via
apify.wellfound_actor_id in config.yaml).

Actor input:  { startUrls: [{url: ...}], maxItems: N }
Actor output: { type, jobId, title, companyName, jobUrl, compensation,
                remote, locations, postedAt (unix ts string), scrapedAt }

apply_url = Wellfound job page (jobUrl); the candidate clicks Apply there
to reach the actual ATS form.

Startup-heavy signal: equity + compensation transparency, funding-stage context.
Requires env var: APIFY_TOKEN
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from src.config import load
from src.discover.scrapers.apify_runner import run_actor
from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

SOURCE      = "wellfound"
SOURCE_TIER = 2  # Startup-focused; equity + salary transparency

_DEFAULT_ACTOR = "crawlerbros/wellfound-scraper"
_BASE          = "https://wellfound.com/jobs"

# ATS fingerprinting — not available in Wellfound actor output (jobUrl is a
# wellfound.com URL), but keeping map for future enrichment.
_ATS_MAP: dict[str, str] = {
    "greenhouse.io":       "greenhouse",
    "lever.co":            "lever",
    "ashbyhq.com":         "ashby",
    "workable.com":        "workable",
    "smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com":   "workday",
}

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship)\b",
    re.IGNORECASE,
)

# Wellfound job search URLs — one per target market
# Format: https://wellfound.com/jobs?<params>
_SEARCH_URLS: list[str] = [
    # Remote worldwide
    f"{_BASE}?remote=true",
    # EU locations (Wellfound uses city/country text in location filter)
    f"{_BASE}?{urlencode({'location': 'Germany'})}",
    f"{_BASE}?{urlencode({'location': 'Netherlands'})}",
    f"{_BASE}?{urlencode({'location': 'Ireland'})}",
    f"{_BASE}?{urlencode({'location': 'United Kingdom'})}",
]


def _parse_posted_at(raw: dict) -> datetime | None:
    # Actor returns postedAt as a Unix timestamp string
    ts = raw.get("postedAt")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, TypeError):
            pass
    scraped = raw.get("scrapedAt")
    if scraped:
        try:
            dt = datetime.fromisoformat(scraped.rstrip("Z"))
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _parse_location(raw: dict) -> tuple[str, str]:
    """Return (display string, ISO country code)."""
    locations: list = raw.get("locations") or []
    if not locations:
        if raw.get("remote"):
            return "Remote", "REMOTE"
        return "", "REMOTE"
    loc_str = locations[0] if isinstance(locations[0], str) else str(locations[0])
    country = country_from_location(loc_str)
    return loc_str, country


def _parse_salary(raw: dict) -> str | None:
    comp = (raw.get("compensation") or "").strip()
    return comp or None


def _fingerprint_ats(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        for domain, ats in _ATS_MAP.items():
            if domain in host:
                return ats
    except Exception:
        pass
    return None


def _parse_job(raw: dict) -> Job | None:
    try:
        if raw.get("type") != "wellfound_job":
            return None

        title   = (raw.get("title") or "").strip()
        company = (raw.get("companyName") or "").strip()
        # jobUrl is the Wellfound listing page — the candidate applies from there
        apply_url = (raw.get("jobUrl") or "").strip()

        if not title or not company or not apply_url:
            return None

        location_str, country = _parse_location(raw)
        posted_at = _parse_posted_at(raw)
        salary    = _parse_salary(raw)

        is_remote = bool(raw.get("remote"))
        if is_remote:
            remote = RemoteType.remote
        else:
            remote = infer_remote(location_str, "")

        # No description in this actor's output — visa_signal from title/salary only
        visa_signal = bool(_VISA_RE.search(title + " " + (salary or "")))

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = location_str or None,
            remote            = remote,
            posted_at         = posted_at,
            timestamp_trusted = posted_at is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = _fingerprint_ats(apply_url),
            apply_url         = apply_url,
            salary            = salary,
            language          = "EN",
            description       = "",
            yoe_max           = None,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("wellfound: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


def fetch(search_urls: list[str] | None = None) -> list[Job]:
    """
    Discover Wellfound jobs via Apify actor.

    Runs the crawlerbros/wellfound-scraper actor against a set of Wellfound
    job search URLs covering remote + EU relocation markets.

    Requires APIFY_TOKEN env var. Override actor via apify.wellfound_actor_id
    in config.yaml.
    """
    cfg      = load("config")
    apify    = cfg.get("apify", {})
    actor_id = apify.get("wellfound_actor_id", _DEFAULT_ACTOR)
    timeout  = apify.get("timeout_secs", 300)

    urls = search_urls or _SEARCH_URLS
    log.info("wellfound: starting Apify run — %d URLs, actor=%s", len(urls), actor_id)

    actor_input = {
        "startUrls": [{"url": u} for u in urls],
        "maxItems":  len(urls) * 25,
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
