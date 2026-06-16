"""
Relocate.me job board scraper.

Parses the public /international-jobs listing pages (static HTML — no JS required).
For each job, fetches the detail page to extract LD+JSON structured data
(title, company, location, salary, description, datePosted).

apply_url is the relocate.me job page itself — the candidate logs in there
(Google or LinkedIn OAuth) to reveal the actual employer apply link.

Note: Relocate.me is a curated board with ~25–50 relocation-package jobs at a
time. Jobs may be older than 48 h; the gate filters those out automatically.
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

_BASE    = "https://relocate.me"
_LISTING = f"{_BASE}/international-jobs"
SOURCE   = "relocateme"
SOURCE_TIER = 1  # Tier 1 — every listing includes a relocation package

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobPilot/1.0; +https://github.com/jobpilot)",
    "Accept":     "text/html,application/xhtml+xml",
}

_YOE_RE   = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_TAG_RE   = re.compile(r"<[^>]+>")
_JOB_LINK = re.compile(r'href="(/[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+-\d+)"')
_LD_JSON  = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.DOTALL)
_SALARY_RE = re.compile(r"\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?|\d+[kK](?:\s*[-–]\s*\d+[kK])?", re.IGNORECASE)


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


def _get_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        log.warning("relocateme: GET %s failed: %s", url, exc)
        return None


def _parse_listing_page(html: str) -> list[str]:
    """Extract job detail paths from listing HTML."""
    paths = _JOB_LINK.findall(html)
    # Filter out non-job pages (guide pages, etc.)
    return [p for p in dict.fromkeys(paths) if "/job-search" not in p]


def _next_page_path(html: str) -> str | None:
    """Return the href of the Next button, if present."""
    m = re.search(r'href="(/international-jobs\?page=\d+)"[^>]*>(?:Next|›|»)', html)
    return m.group(1) if m else None


def _parse_detail(path: str) -> Job | None:
    """Fetch detail page and extract LD+JSON JobPosting data."""
    import json

    url = f"{_BASE}{path}"
    html = _get_html(url)
    if not html:
        return None

    # Extract all LD+JSON blocks
    for raw_block in _LD_JSON.findall(html):
        clean = re.sub(r"\s+", " ", raw_block).strip()
        try:
            data = json.loads(clean)
        except Exception:
            continue

        if data.get("@type") != "JobPosting":
            continue

        try:
            # Title: strip "| Relocation Offered" suffix
            title = re.sub(r"\s*[\|–]\s*Relocation.*$", "", data.get("title", "")).strip()
            if not title:
                return None

            org   = data.get("hiringOrganization") or {}
            company = (org.get("name") or "").strip()
            if not company:
                return None

            loc_obj  = data.get("jobLocation") or {}
            addr     = loc_obj.get("address") or {} if isinstance(loc_obj, dict) else {}
            city     = (addr.get("addressLocality") or "").strip()
            country_name = (addr.get("addressCountry") or "").strip()
            country  = country_from_location(country_name or city)
            location = ", ".join(filter(None, [city, country_name])) or None

            description_raw = data.get("description") or ""
            description = _strip_html(description_raw)

            date_str = data.get("datePosted") or data.get("validFrom")
            posted_at: datetime | None = None
            if date_str:
                try:
                    dt = datetime.fromisoformat(str(date_str))
                    posted_at = dt.replace(tzinfo=timezone.utc) if not dt.tzinfo else dt.astimezone(timezone.utc)
                except Exception:
                    pass

            # Salary from description or baseSalary field
            sal_obj = data.get("baseSalary") or {}
            salary: str | None = None
            if isinstance(sal_obj, dict):
                val = sal_obj.get("value") or {}
                if isinstance(val, dict):
                    lo = val.get("minValue")
                    hi = val.get("maxValue")
                    currency = (sal_obj.get("currency") or "USD").upper()
                    if lo and hi:
                        salary = f"{currency} {int(lo):,}–{int(hi):,}/yr"
                    elif lo:
                        salary = f"{currency} {int(lo):,}+/yr"
            if not salary:
                m = _SALARY_RE.search(description[:500])
                salary = m.group(0) if m else None

            yoe_matches = _YOE_RE.findall(description)
            yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None
            remote      = infer_remote(location or "", description[:400])

            return Job(
                job_key           = build_job_key(company, title, country),
                title             = title,
                company           = company,
                country           = country,
                location          = location,
                remote            = remote,
                posted_at         = posted_at,
                timestamp_trusted = posted_at is not None,
                source            = SOURCE,
                source_tier       = SOURCE_TIER,
                ats               = None,
                apply_url         = url,   # login-gated; candidate applies via relocate.me
                salary            = salary,
                language          = "EN",
                description       = description,
                yoe_max           = yoe_max,
                visa_signal       = True,  # all relocate.me listings include relocation support
            )
        except Exception as exc:
            log.warning("relocateme: parse error for %s: %s", path, exc)
            return None

    return None


def fetch(max_pages: int = 3, detail_delay: float = 0.4) -> list[Job]:
    """
    Scrape relocation jobs from Relocate.me.

    Fetches the listing pages (static HTML), then visits each detail page
    to extract structured data via LD+JSON. All jobs include relocation packages.

    Args:
        max_pages:    Maximum listing pages to scan (each has ~20–25 jobs).
        detail_delay: Seconds between detail page requests (polite scraping).
    """
    seen:     set[str] = set()
    all_jobs: list[Job] = []
    next_path: str | None = "/international-jobs"
    pages_fetched = 0

    while next_path and pages_fetched < max_pages:
        url  = f"{_BASE}{next_path}" if not next_path.startswith("http") else next_path
        html = _get_html(url)
        if not html:
            break

        paths = _parse_listing_page(html)
        log.info("relocateme: listing page %d — %d job paths", pages_fetched + 1, len(paths))

        for path in paths:
            if path in seen:
                continue
            seen.add(path)

            job = _parse_detail(path)
            if job:
                all_jobs.append(job)
                log.debug("relocateme: parsed '%s' @ %s", job.title, job.company)

            if detail_delay > 0:
                time.sleep(detail_delay)

        next_path = _next_page_path(html)
        pages_fetched += 1
        if next_path:
            time.sleep(1.0)  # pause between listing pages

    log.info("relocateme: %d jobs total across %d listing page(s)", len(all_jobs), pages_fetched)
    return all_jobs
