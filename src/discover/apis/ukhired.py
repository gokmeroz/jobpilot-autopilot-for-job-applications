"""
UKHired adapter — UK visa-sponsoring companies via LinkedIn.

UKHired (ukhired.co.uk) maintains a curated list of ~1,900 UK employers that
sponsor the Skilled Worker visa. It has no direct job listings feed; instead
it exposes an API that generates LinkedIn job search URLs pre-filtered to those
sponsor companies.

This adapter:
  1. Fetches Tier-1 tech companies from api.ukhired.co.uk/linkedin/companies
  2. Batches them (API limit: 10 per request) and calls /linkedin/linkedin_url
     to generate LinkedIn search URLs scoped to UK visa sponsors
  3. Runs the Apify LinkedIn scraper on those URLs (reuses APIFY_TOKEN)
  4. Returns Job objects from UK sponsor companies only

Requires env var: APIFY_TOKEN
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from src.config import load
from src.discover.scrapers.apify_runner import run_actor
from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

_API_BASE   = "https://api.ukhired.co.uk"
_BATCH_SIZE = 10   # API limit: max 10 company names per URL request
SOURCE      = "ukhired"
SOURCE_TIER = 1    # Tier 1 — guaranteed UK visa sponsors

_HEADERS = {
    "User-Agent": "JobPilot/1.0 (+https://github.com/jobpilot)",
    "Accept": "application/json",
}

# Categories to include (8 = Technology; 2 = Consulting; 9 = Healthcare omitted)
_TARGET_CATEGORIES = {8}   # Technology
# Tiers to include from UKHired's classification
_TARGET_TIERS      = {"Tier1", "Tier2"}

# Role keywords — multiple queries so we cover both eng + AI roles
_ROLE_KEYWORDS = [
    "junior software engineer",
    "graduate software engineer",
    "backend engineer entry level",
    "AI engineer junior",
]

# ATS fingerprinting (same as LinkedIn adapter)
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
_RELATIVE_RE = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago", re.IGNORECASE)
_UNIT_HOURS = {"second": 1/3600, "minute": 1/60, "hour": 1, "day": 24, "week": 168, "month": 720}


# ---------------------------------------------------------------------------
# UKHired company fetcher
# ---------------------------------------------------------------------------

def _fetch_tech_companies() -> list[str]:
    """Return names of Tier1/2 tech companies from UKHired, sorted by tier."""
    try:
        resp = requests.get(
            f"{_API_BASE}/linkedin/companies",
            params={"page": 1, "pageSize": 2000},
            headers=_HEADERS,
            verify=False,
            timeout=15,
        )
        resp.raise_for_status()
        companies = resp.json().get("companies") or []
    except Exception as exc:
        log.error("ukhired: companies fetch failed: %s", exc)
        return []

    # Filter to relevant categories and tiers
    filtered = [
        c for c in companies
        if c.get("category_id") in _TARGET_CATEGORIES
        and c.get("tier") in _TARGET_TIERS
    ]
    # Tier1 first, then Tier2
    filtered.sort(key=lambda c: (0 if c.get("tier") == "Tier1" else 1, c.get("name", "")))
    log.info("ukhired: %d relevant tech companies (%s filtered)", len(filtered), len(companies))
    return [c["name"] for c in filtered]


# ---------------------------------------------------------------------------
# LinkedIn URL generator
# ---------------------------------------------------------------------------

def _generate_linkedin_url(company_batch: list[str], role: str, country: str = "United Kingdom") -> str | None:
    """Call UKHired API to generate a LinkedIn search URL for a company batch."""
    try:
        resp = requests.post(
            f"{_API_BASE}/linkedin/linkedin_url",
            json={
                "company_names": ",".join(company_batch),
                "country": country,
                "role": role,
            },
            headers=_HEADERS,
            verify=False,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.warning("ukhired: URL generation error: %s", data["error"])
            return None
        url = data.get("linkedin_job_search_url", "")
        # Add entry-level filter (f_E=2 = Entry level, f_JT=F = Full-time, f_TPR=r86400 = last 24h)
        if url:
            url += "&f_E=2%2C3&f_JT=F&f_TPR=r86400"
        return url or None
    except Exception as exc:
        log.warning("ukhired: URL generation failed for batch: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Job parser (same structure as LinkedIn adapter)
# ---------------------------------------------------------------------------

def _fingerprint_ats(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        for domain, ats in _ATS_MAP.items():
            if domain in host:
                return ats
    except Exception:
        pass
    return None


def _parse_posted_at(raw_date: str | None) -> datetime | None:
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw_date.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(raw_date.strip())
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    m = _RELATIVE_RE.search(raw_date)
    if m:
        hours = int(m.group(1)) * _UNIT_HOURS.get(m.group(2).lower(), 0)
        return datetime.now(timezone.utc) - timedelta(hours=hours)
    return None


def _get(raw: dict, *keys: str) -> str:
    for key in keys:
        val = raw.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _parse_job(raw: dict) -> Job | None:
    try:
        title     = _get(raw, "title")
        company   = _get(raw, "companyName")
        apply_url = _get(raw, "applyUrl") or _get(raw, "link")

        if not title or not company or not apply_url:
            return None

        location    = _get(raw, "location")
        description = _get(raw, "descriptionText", "descriptionHtml")
        posted_at   = _parse_posted_at(_get(raw, "postedAt"))
        country     = country_from_location(location) if location else "GB"
        remote_type = infer_remote(location, description[:300])
        ats         = _fingerprint_ats(apply_url)
        visa_signal = True   # every result is from a confirmed UK Skilled Worker sponsor
        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = location or "United Kingdom",
            remote            = remote_type,
            posted_at         = posted_at,
            timestamp_trusted = posted_at is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = ats,
            apply_url         = apply_url,
            salary            = _get(raw, "salary") or None,
            language          = "EN",
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("ukhired: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch(max_companies: int = 40) -> list[Job]:
    """
    Discover UK jobs at visa-sponsoring companies via UKHired + LinkedIn/Apify.

    Fetches tech company names from UKHired's curated sponsor list, generates
    LinkedIn search URLs (UKHired API), and runs the Apify LinkedIn actor.

    Args:
        max_companies: How many Tier1/2 tech sponsors to include (max 10 per
                       LinkedIn URL due to API constraint; batched automatically).

    Requires: APIFY_TOKEN env var.
    """
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    cfg      = load("config")
    apify    = cfg.get("apify", {})
    actor_id = apify.get("actor_id", "curious_coder/linkedin-jobs-scraper")
    timeout  = apify.get("timeout_secs", 300)

    # 1. Get UK visa-sponsoring tech companies
    company_names = _fetch_tech_companies()[:max_companies]
    if not company_names:
        log.warning("ukhired: no companies fetched — skipping")
        return []

    # 2. Generate LinkedIn search URLs (batch of 10 per API call, one per role keyword)
    batches = [company_names[i:i + _BATCH_SIZE] for i in range(0, len(company_names), _BATCH_SIZE)]
    linkedin_urls: list[str] = []

    for batch in batches:
        for role in _ROLE_KEYWORDS[:2]:   # limit to 2 roles per batch to keep Apify cost low
            url = _generate_linkedin_url(batch, role)
            if url:
                linkedin_urls.append(url)

    if not linkedin_urls:
        log.error("ukhired: failed to generate any LinkedIn URLs")
        return []

    log.info("ukhired: generated %d LinkedIn URLs across %d batches", len(linkedin_urls), len(batches))

    # 3. Run Apify LinkedIn scraper on the generated URLs
    actor_input = {
        "urls": linkedin_urls,
        "scrapeCompany": False,
        "count": max(50, len(linkedin_urls) * 25),
    }

    try:
        raw_items = run_actor(actor_id, actor_input, timeout_secs=timeout)
    except RuntimeError as exc:
        log.error("ukhired: Apify actor run failed: %s", exc)
        return []

    seen: set[str] = set()
    all_jobs: list[Job] = []

    for raw in raw_items:
        job = _parse_job(raw)
        if job and job.job_key not in seen:
            seen.add(job.job_key)
            all_jobs.append(job)

    log.info("ukhired: %d unique jobs from %d raw items", len(all_jobs), len(raw_items))
    return all_jobs
