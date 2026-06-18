"""
Greenhouse Job Board API adapter.

Scrapes the public boards API for each target company — no auth required.
API: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

One request per company; no pagination (all open jobs returned at once).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

from src.config import load
from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

_BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
SOURCE = "greenhouse"
SOURCE_TIER = 1
REQUEST_DELAY = 0.4   # seconds between company requests

# Fallback company list — used only if config/sources.yaml is missing or empty.
# Prefer editing config/sources.yaml over this dict.
COMPANIES: dict[str, str] = {
    "Stripe":       "stripe",
    "Figma":        "figma",
}

# Keywords in content that suggest visa/relocation support
_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship|right\s+to\s+work\s+not\s+required)\b",
    re.IGNORECASE,
)

# YOE extraction — "X+ years", "X years of experience"
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)

# German function words for language detection
_DE_WORDS = re.compile(r"\b(und|oder|mit|für|die|der|das|ein|eine|als|bei|von|zu)\b", re.I)


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------

def _parse_posted_at(raw: dict) -> datetime | None:
    # first_published is when the role opened; updated_at is last edit.
    # Prefer first_published for age checks.
    for key in ("first_published", "updated_at"):
        val = raw.get(key)
        if val:
            try:
                return datetime.fromisoformat(val).astimezone(timezone.utc)
            except ValueError:
                pass
    return None


def _parse_location(raw: dict) -> tuple[str, str]:
    """Return (location_display, country_iso2)."""
    loc_obj = raw.get("location") or {}
    location = (loc_obj.get("name") or "").strip()

    # Greenhouse sometimes puts remote signals in the location field
    if not location:
        return "", "REMOTE"

    country = country_from_location(location)
    return location, country


def _parse_salary(raw: dict) -> str | None:
    """Extract salary range from pay_transparency metadata if present."""
    meta = raw.get("metadata") or []
    for item in meta:
        if isinstance(item, dict) and "pay" in (item.get("name") or "").lower():
            val = item.get("value")
            if val:
                return str(val)
    return None


def _yoe_from_description(description: str) -> int | None:
    """Extract the highest YOE requirement mentioned in plain-text description."""
    matches = _YOE_RE.findall(description)
    if not matches:
        return None
    return max(int(m[0]) for m in matches)


def _detect_language(raw: dict, description: str) -> str:
    # Greenhouse provides a language field on some boards
    api_lang = (raw.get("language") or "").lower()
    if api_lang.startswith("de"):
        return "DE"
    if api_lang and not api_lang.startswith("en"):
        return api_lang.upper()[:2]
    # Fallback: count German function words
    sample = description[:500]
    return "DE" if len(_DE_WORDS.findall(sample)) >= 4 else "EN"


def _parse_remote(location: str, description: str) -> RemoteType:
    return infer_remote(location, description[:300])


# ---------------------------------------------------------------------------
# Job parser
# ---------------------------------------------------------------------------

def _parse_job(raw: dict, company: str) -> Job | None:
    try:
        title      = (raw.get("title") or "").strip()
        apply_url  = (raw.get("absolute_url") or "").strip()
        company    = (raw.get("company_name") or company).strip()

        if not title or not apply_url:
            return None

        html_content = raw.get("content") or ""
        description  = _strip_html(html_content)
        location, country = _parse_location(raw)
        posted_at    = _parse_posted_at(raw)
        language     = _detect_language(raw, description)
        remote_type  = _parse_remote(location, description)
        salary       = _parse_salary(raw)
        yoe_max      = _yoe_from_description(description)
        visa_signal  = bool(_VISA_RE.search(description))

        return Job(
            job_key          = build_job_key(company, title, country),
            title            = title,
            company          = company,
            country          = country,
            location         = location or None,
            remote           = remote_type,
            posted_at        = posted_at,
            timestamp_trusted= False,   # ATS boards show currently-open jobs; post date ≠ expiry
            source           = SOURCE,
            source_tier      = SOURCE_TIER,
            ats              = "greenhouse",
            apply_url        = apply_url,
            salary           = salary,
            language         = language,
            description      = description,
            yoe_max          = yoe_max,
            visa_signal      = visa_signal,
        )
    except Exception as exc:
        log.warning("failed to parse greenhouse job '%s': %s", raw.get("title"), exc)
        return None


# ---------------------------------------------------------------------------
# Per-company fetcher
# ---------------------------------------------------------------------------

def _fetch_company(company: str, token: str) -> list[Job]:
    url = _BASE.format(token=token)
    try:
        resp = requests.get(
            url,
            params={"content": "true", "pay_transparency": "true"},
            timeout=15,
        )
        if resp.status_code == 404:
            log.warning("greenhouse: board '%s' not found (404) — skipping", token)
            return []
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        log.error("greenhouse: failed to fetch %s (%s): %s", company, token, exc)
        return []

    raw_jobs: list[dict] = payload.get("jobs") or []
    jobs: list[Job] = []
    for raw in raw_jobs:
        job = _parse_job(raw, company)
        if job:
            jobs.append(job)

    log.info("greenhouse: %s → %d jobs", company, len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch(
    companies: dict[str, str] | None = None,
    request_delay: float = REQUEST_DELAY,
) -> list[Job]:
    """
    Fetch open jobs from all target companies on Greenhouse.

    Args:
        companies: override the default COMPANIES dict {display_name: board_token}.
        request_delay: seconds between requests (polite crawling).

    Returns:
        All jobs combined, unfiltered (gate.run handles age/seniority/language).
    """
    sources = load("sources")
    targets = companies or sources.get("greenhouse") or COMPANIES

    all_jobs: list[Job] = []

    for i, (company, token) in enumerate(targets.items()):
        jobs = _fetch_company(company, token)
        all_jobs.extend(jobs)

        if i < len(targets) - 1 and request_delay > 0:
            time.sleep(request_delay)

    log.info("greenhouse: %d fresh jobs across %d companies", len(all_jobs), len(targets))
    return all_jobs
