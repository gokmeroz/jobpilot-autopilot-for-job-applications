"""
Lever Job Board API adapter.

Public endpoint — no auth required.
GET https://api.lever.co/v0/postings/{slug}?mode=json

Returns all open postings for a company in one call (no pagination).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from src.config import load
from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

_BASE = "https://api.lever.co/v0/postings/{slug}?mode=json"
SOURCE = "lever"
SOURCE_TIER = 1
REQUEST_DELAY = 0.4

# Company display name → Lever board slug
COMPANIES: dict[str, str] = {
    "Figma":        "figma",
    "Airtable":     "airtable",
    "Webflow":      "webflow",
    "Brex":         "brex",
    "Loom":         "loom",
    "Sourcegraph":  "sourcegraph",
    "Netlify":      "netlify",
    "Zapier":       "zapier",
    "Scale AI":     "scaleai",
    "Anduril":      "anduril",
    "Descript":     "descript",
    "Pitch":        "pitch-2",
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


def _parse_job(raw: dict, company: str) -> Job | None:
    try:
        title     = (raw.get("text") or "").strip()
        apply_url = (raw.get("hostedUrl") or raw.get("applyUrl") or "").strip()

        if not title or not apply_url:
            return None

        categories = raw.get("categories") or {}
        location   = (categories.get("location") or "").strip()
        commitment = (categories.get("commitment") or "")

        lists_html = " ".join(
            (item.get("content") or "") for item in (raw.get("lists") or [])
        )
        description = _strip_tags(
            (raw.get("description") or "") + " " + lists_html
        )

        # Lever timestamps are milliseconds since epoch
        posted_ts = raw.get("createdAt")
        posted_at = (
            datetime.fromtimestamp(posted_ts / 1000, tz=timezone.utc)
            if posted_ts else None
        )

        country     = country_from_location(location) if location else "REMOTE"
        remote_type = infer_remote(location, commitment, description[:200])
        language    = "DE" if len(_DE_WORDS.findall(description[:500])) >= 4 else "EN"
        visa_signal = bool(_VISA_RE.search(description))
        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

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
            ats               = "lever",
            apply_url         = apply_url,
            salary            = None,
            language          = language,
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("lever: failed to parse job '%s': %s", raw.get("text"), exc)
        return None


def _fetch_company(company: str, slug: str) -> list[Job]:
    url = _BASE.format(slug=slug)
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            log.warning("lever: board '%s' not found (404) — skipping", slug)
            return []
        resp.raise_for_status()
        raw_jobs: list[dict] = resp.json()
    except requests.RequestException as exc:
        log.error("lever: failed to fetch %s (%s): %s", company, slug, exc)
        return []

    jobs = [j for raw in raw_jobs if (j := _parse_job(raw, company))]
    log.info("lever: %s → %d jobs", company, len(jobs))
    return jobs


def fetch(
    companies: dict[str, str] | None = None,
    request_delay: float = REQUEST_DELAY,
) -> list[Job]:
    """Fetch open jobs from all target companies on Lever."""
    cfg = load("config")
    max_age_hours: float = cfg["gate"]["max_age_hours"]
    targets = companies or COMPANIES

    all_jobs: list[Job] = []

    for i, (company, slug) in enumerate(targets.items()):
        jobs = _fetch_company(company, slug)

        fresh = [
            j for j in jobs
            if not j.timestamp_trusted
            or j.age_hours() is None
            or (j.age_hours() or 0) <= max_age_hours
        ]
        if len(jobs) - len(fresh):
            log.debug("lever: dropped %d stale jobs from %s", len(jobs) - len(fresh), company)

        all_jobs.extend(fresh)

        if i < len(targets) - 1 and request_delay > 0:
            time.sleep(request_delay)

    log.info("lever: %d fresh jobs across %d companies", len(all_jobs), len(targets))
    return all_jobs
