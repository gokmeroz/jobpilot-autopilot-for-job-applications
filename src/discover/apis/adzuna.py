"""
Adzuna job board API adapter.

Official API with free tier. Covers DE, NL, IE, GB, US with salary data.
Docs: https://developer.adzuna.com/docs/search

Requires env vars: ADZUNA_APP_ID, ADZUNA_APP_KEY
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from src.config import env, load
from src.models import Job
from src.normalize import build_job_key, country_from_location, fingerprint_ats, infer_remote

log = logging.getLogger(__name__)

_BASE = "https://api.adzuna.com/v1/api/jobs/{country_code}/search/{page}"
SOURCE = "adzuna"
SOURCE_TIER = 2

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship|right\s+to\s+work\s+not\s+required)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_DE_WORDS = re.compile(r"\b(und|oder|mit|für|die|der|das|ein|eine|als|bei|von|zu)\b", re.I)

# (Adzuna country code, ISO-2 for our pipeline, search keywords)
_TARGETS: list[tuple[str, str, str]] = [
    ("de", "DE", "junior software engineer"),
    ("de", "DE", "graduate software engineer"),
    ("de", "DE", "backend developer junior"),
    ("nl", "NL", "junior software engineer"),
    ("nl", "NL", "backend developer junior"),
    ("ie", "IE", "junior software engineer"),
    ("gb", "GB", "junior software engineer"),
    ("gb", "GB", "backend developer junior"),
    ("us", "US", "junior software engineer remote"),
    ("us", "US", "junior backend developer remote"),
    ("us", "US", "AI engineer entry level remote"),
]


def _parse_salary(raw: dict) -> str | None:
    lo = raw.get("salary_min")
    hi = raw.get("salary_max")
    if lo and hi:
        return f"£/€/$ {int(lo):,}–{int(hi):,}/yr"
    if lo:
        return f"£/€/$ {int(lo):,}+/yr"
    return None


def _parse_job(raw: dict, iso2: str) -> Job | None:
    try:
        title   = (raw.get("title") or "").strip()
        company = (raw.get("company") or {}).get("display_name", "").strip()
        apply_url = (raw.get("redirect_url") or "").strip()

        if not title or not company or not apply_url:
            return None

        description = (raw.get("description") or "").strip()
        loc_obj     = raw.get("location") or {}
        location    = (loc_obj.get("display_name") or "").strip()

        country = country_from_location(location) if location else iso2
        if country == "UNKNOWN":
            country = iso2

        remote_type = infer_remote(location, description[:200])

        created = raw.get("created")
        posted_at = None
        if created:
            try:
                posted_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
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
            ats               = fingerprint_ats(apply_url),
            apply_url         = apply_url,
            salary            = _parse_salary(raw),
            language          = language,
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = bool(_VISA_RE.search(description)),
        )
    except Exception as exc:
        log.warning("adzuna: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


def _fetch_target(adzuna_cc: str, iso2: str, keywords: str,
                  app_id: str, app_key: str,
                  max_age_hours: float, request_delay: float) -> list[Job]:
    jobs: list[Job] = []
    page = 1

    while True:
        url = _BASE.format(country_code=adzuna_cc, page=page)
        try:
            resp = requests.get(
                url,
                params={
                    "app_id":        app_id,
                    "app_key":       app_key,
                    "what":          keywords,
                    "results_per_page": 20,
                    "max_days_old":  max(1, int(max_age_hours / 24)),
                    "content-type":  "application/json",
                },
                timeout=20,
            )
            if resp.status_code == 429:
                log.warning("adzuna: rate limited (%s/%s)", adzuna_cc, keywords)
                break
            resp.raise_for_status()
            results = resp.json().get("results") or []
        except Exception as exc:
            log.error("adzuna: %s '%s' page %d failed: %s", adzuna_cc, keywords, page, exc)
            break

        if not results:
            break

        for raw in results:
            job = _parse_job(raw, iso2)
            if job:
                jobs.append(job)

        # Adzuna paginates but results_per_page=20 and we only want recent — one page is enough
        break

    return jobs


def fetch(
    targets: list[tuple[str, str, str]] | None = None,
    request_delay: float = 0.5,
) -> list[Job]:
    """
    Fetch jobs from Adzuna. Requires ADZUNA_APP_ID and ADZUNA_APP_KEY env vars.
    Silently returns [] if keys are missing.
    """
    app_id  = env("ADZUNA_APP_ID")
    app_key = env("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        log.info("adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping")
        return []

    cfg = load("config")
    max_age_hours: float = cfg["gate"]["max_age_hours"]
    matrix = targets or _TARGETS

    log.info("adzuna: running %d queries", len(matrix))

    seen: set[str] = set()
    all_jobs: list[Job] = []

    for adzuna_cc, iso2, keywords in matrix:
        jobs = _fetch_target(adzuna_cc, iso2, keywords, app_id, app_key,
                             max_age_hours, request_delay)
        for job in jobs:
            if job.job_key not in seen:
                seen.add(job.job_key)
                all_jobs.append(job)
        if request_delay > 0:
            time.sleep(request_delay)

    log.info("adzuna: %d unique jobs from %d queries", len(all_jobs), len(matrix))
    return all_jobs
