"""
Arbeitnow public job-board API adapter.

No auth required. Sorted newest-first. Pagination via ?page=N.
Docs: https://www.arbeitnow.com/api/job-board-api
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
from src.normalize import build_job_key, country_from_location, fingerprint_ats, infer_remote

log = logging.getLogger(__name__)

_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
SOURCE = "arbeitnow"
SOURCE_TIER = 2

# job_types values that reliably indicate mid/senior experience required
_SENIOR_TYPES = {
    "mid",
    "berufserfahren",
    "professional / experienced",
    "senior",
    "leitend",           # German for "leading"
    "führungskraft",     # German for "executive"
}

# Signals in tags that suggest the company sponsors / relocates
_VISA_TAGS = {"visa", "sponsorship", "relocation", "work permit", "work visa"}

# German-language detection: common short function words
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

def _parse_remote(remote_flag: bool, location: str, tags: list[str]) -> RemoteType:
    if remote_flag:
        return RemoteType.remote
    tag_text = " ".join(tags)
    return infer_remote(location, tag_text)


def _yoe_from_types(job_types: list[str]) -> int | None:
    """Return a yoe_max hint if job_types signal non-entry-level, else None."""
    lowered = {t.lower() for t in job_types}
    if lowered & _SENIOR_TYPES:
        return 3   # will be gated out (gate.max_yoe = 1)
    return None


def _visa_from_tags(tags: list[str]) -> bool:
    lowered = {t.lower() for t in tags}
    return bool(lowered & _VISA_TAGS)


def _detect_language(title: str, description: str) -> str:
    sample = f"{title} {description[:400]}"
    de_hits = len(_DE_WORDS.findall(sample))
    return "DE" if de_hits >= 4 else "EN"


def _parse_job(raw: dict) -> Job | None:
    try:
        company = (raw.get("company_name") or "").strip()
        title   = (raw.get("title") or "").strip()
        location = (raw.get("location") or "").strip()

        if not company or not title:
            return None

        description = _strip_html(raw.get("description") or "")
        tags        = raw.get("tags") or []
        job_types   = raw.get("job_types") or []
        remote_flag = bool(raw.get("remote"))
        apply_url   = (raw.get("url") or "").strip()
        created_at  = raw.get("created_at")

        if not apply_url:
            return None

        country     = country_from_location(location) if location else "REMOTE"
        remote_type = _parse_remote(remote_flag, location, tags)
        posted_at   = datetime.fromtimestamp(created_at, tz=timezone.utc) if created_at else None
        language    = _detect_language(title, description)

        job_key = build_job_key(company, title, country)

        return Job(
            job_key          = job_key,
            title            = title,
            company          = company,
            country          = country,
            location         = location or None,
            remote           = remote_type,
            posted_at        = posted_at,
            timestamp_trusted= posted_at is not None,
            source           = SOURCE,
            source_tier      = SOURCE_TIER,
            ats              = fingerprint_ats(apply_url),
            apply_url        = apply_url,
            salary           = None,
            language         = language,
            description      = description,
            yoe_max          = _yoe_from_types(job_types),
            visa_signal      = _visa_from_tags(tags),
        )
    except Exception as exc:
        log.warning("failed to parse arbeitnow job: %s — %s", raw.get("slug"), exc)
        return None


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

def fetch(max_pages: int | None = None, request_delay: float = 0.5) -> list[Job]:
    """
    Fetch jobs from Arbeitnow. Stops early when jobs exceed max_age_hours.

    Args:
        max_pages: override the page cap (defaults to config gate.max_age_hours guard).
        request_delay: seconds to sleep between pages (be a polite client).

    Returns:
        List of Job objects, unfiltered (gate.run handles filtering).
    """
    cfg = load("config")
    max_age_hours: float = cfg["gate"]["max_age_hours"]
    cutoff = max_pages or 10  # safety cap — arbeitnow has hundreds of pages

    jobs: list[Job] = []
    page = 1

    while page <= cutoff:
        try:
            resp = requests.get(_BASE_URL, params={"page": page}, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.error("arbeitnow page %d fetch failed: %s", page, exc)
            break

        raw_jobs: list[dict] = payload.get("data") or []
        if not raw_jobs:
            break

        meta = payload.get("meta") or {}
        last_page: int = meta.get("last_page", page)

        hit_cutoff = False
        for raw in raw_jobs:
            created_at = raw.get("created_at")
            if created_at:
                age_hours = (time.time() - created_at) / 3600
                if age_hours > max_age_hours:
                    hit_cutoff = True
                    break

            job = _parse_job(raw)
            if job:
                jobs.append(job)

        log.info("arbeitnow page %d/%d — %d jobs so far", page, last_page, len(jobs))

        if hit_cutoff or page >= last_page:
            break

        page += 1
        if request_delay > 0:
            time.sleep(request_delay)

    log.info("arbeitnow: fetched %d jobs total across %d page(s)", len(jobs), page)
    return jobs
