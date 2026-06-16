"""
Relocate.me job board API adapter.

Public JSON API — no auth required.
All jobs include relocation packages — direct signal for EU/UK relocation targets.
API: https://relocate.me/api/v1/jobs
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, infer_remote

log = logging.getLogger(__name__)

_BASE = "https://relocate.me/api/v1/jobs"
SOURCE = "relocateme"
SOURCE_TIER = 1  # Tier 1 — relocation-specific, highest priority market signal

_HEADERS = {
    "User-Agent": "JobPilot/1.0 (+https://github.com/jobpilot)",
    "Accept": "application/json",
}

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance|package)|work\s+permit|"
    r"we\s+sponsor|sponsorship)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)


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


def _parse_salary(raw: dict) -> str | None:
    sal = raw.get("salary") or {}
    if not isinstance(sal, dict):
        return None
    lo = sal.get("from") or sal.get("min")
    hi = sal.get("to") or sal.get("max")
    currency = (sal.get("currency") or "EUR").upper()
    if lo and hi:
        return f"{currency} {int(lo):,}–{int(hi):,}/yr"
    if lo:
        return f"{currency} {int(lo):,}+/yr"
    return None


def _parse_location(raw: dict) -> tuple[str, str]:
    """Return (display_string, ISO country code)."""
    loc = raw.get("location") or {}
    if isinstance(loc, dict):
        city = (loc.get("city") or "").strip()
        country_obj = loc.get("country") or {}
        if isinstance(country_obj, dict):
            country_name = (country_obj.get("title") or country_obj.get("name") or "").strip()
            iso = (country_obj.get("iso_code") or country_obj.get("code") or "").strip().upper()
        else:
            country_name = str(country_obj).strip()
            iso = ""
        display = ", ".join(filter(None, [city, country_name]))
        return display, iso or country_from_location(display)

    if isinstance(loc, str) and loc:
        return loc, country_from_location(loc)

    # Flat country field as fallback
    country_raw = raw.get("country") or ""
    if country_raw:
        return str(country_raw), country_from_location(str(country_raw))
    return "", "REMOTE"


def _parse_posted_at(raw: dict) -> datetime | None:
    for field in ("published_at", "created_at", "date", "updated_at"):
        val = raw.get(field)
        if val:
            try:
                dt = datetime.fromisoformat(str(val))
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _parse_job(raw: dict) -> Job | None:
    try:
        title = (raw.get("title") or "").strip()

        company_raw = raw.get("company") or {}
        if isinstance(company_raw, dict):
            company = (company_raw.get("title") or company_raw.get("name") or "").strip()
        else:
            company = str(company_raw).strip()

        apply_url = (raw.get("apply_url") or raw.get("url") or raw.get("link") or "").strip()

        if not title or not company or not apply_url:
            return None

        description = _strip_html(raw.get("description") or raw.get("body") or "")
        location_str, country = _parse_location(raw)
        posted_at = _parse_posted_at(raw)
        salary = _parse_salary(raw)

        visa_signal = bool(raw.get("visa_support") or raw.get("visa_sponsorship"))
        if not visa_signal:
            visa_signal = bool(_VISA_RE.search(description))

        yoe_matches = _YOE_RE.findall(description)
        yoe_max = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

        job_type = (raw.get("type") or raw.get("job_type") or "").lower()
        if "remote" in job_type:
            remote = RemoteType.remote
        elif location_str:
            remote = infer_remote(location_str, description[:300])
        else:
            remote = RemoteType.onsite

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
            ats               = None,
            apply_url         = apply_url,
            salary            = salary,
            language          = "EN",
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = True,  # every relocate.me listing includes relocation support
        )
    except Exception as exc:
        log.warning("relocateme: failed to parse job '%s': %s", raw.get("title"), exc)
        return None


def _fetch_page(page: int, per_page: int = 20) -> tuple[list[dict], int]:
    try:
        resp = requests.get(
            _BASE,
            headers=_HEADERS,
            params={"page": page, "per_page": per_page},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.error("relocateme: page %d fetch failed: %s", page, exc)
        return [], 1

    raw = payload.get("data") or []
    meta = payload.get("meta") or {}
    last_page = int(meta.get("last_page") or meta.get("total_pages") or 1)
    return raw, last_page


def fetch(max_pages: int = 5, request_delay: float = 0.5) -> list[Job]:
    """Fetch relocation jobs from Relocate.me (all include relocation packages)."""
    seen: set[str] = set()
    all_jobs: list[Job] = []
    page = 1

    while page <= max_pages:
        raw_jobs, last_page = _fetch_page(page)
        if not raw_jobs:
            break

        for raw in raw_jobs:
            job = _parse_job(raw)
            if job and job.job_key not in seen:
                seen.add(job.job_key)
                all_jobs.append(job)

        log.info("relocateme: page %d/%d → %d jobs so far", page, last_page, len(all_jobs))

        if page >= last_page:
            break
        page += 1
        if request_delay > 0:
            time.sleep(request_delay)

    log.info("relocateme: %d unique jobs total", len(all_jobs))
    return all_jobs
