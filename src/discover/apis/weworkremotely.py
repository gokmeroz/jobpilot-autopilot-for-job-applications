"""
WeWorkRemotely RSS adapter.

Public RSS feeds — no auth required.
All jobs are remote-first; strong signal for Remote USA and Remote Worldwide.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from src.models import Job, RemoteType
from src.normalize import build_job_key, country_from_location, fingerprint_ats

log = logging.getLogger(__name__)

SOURCE = "weworkremotely"
SOURCE_TIER = 2
REQUEST_DELAY = 1.0  # WWR is strict about rate limits

_FEEDS: dict[str, str] = {
    "full-stack":  "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "backend":     "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "frontend":    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "devops":      "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
}

_VISA_RE = re.compile(
    r"\b(visa\s+sponsor|relocation\s+(support|assistance)|work\s+permit|"
    r"we\s+sponsor|sponsorship)\b",
    re.IGNORECASE,
)
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)]]>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _unwrap_cdata(text: str) -> str:
    m = _CDATA_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _strip_tags(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s{2,}", " ", text).strip()


def _parse_pubdate(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return None


def _parse_item(item: ET.Element) -> Job | None:
    try:
        def text(tag: str) -> str:
            el = item.find(tag)
            if el is None:
                return ""
            raw = (el.text or "").strip()
            return _unwrap_cdata(raw)

        # WWR title format: "Company Name: Job Title"
        full_title = text("title")
        if ": " in full_title:
            company, title = full_title.split(": ", 1)
        else:
            title   = full_title
            company = ""

        company = company.strip()
        title   = title.strip()

        # The <link> tag in RSS is the URL following the <guid> text node workaround
        link_el = item.find("link")
        # ElementTree puts RSS <link> text as tail of the previous element
        apply_url = ""
        if link_el is not None:
            apply_url = (link_el.tail or link_el.text or "").strip()
        if not apply_url:
            guid_el = item.find("guid")
            apply_url = (guid_el.text if guid_el is not None else "").strip()

        if not title or not company or not apply_url:
            return None

        description = _strip_tags(text("description"))
        region      = text("region") or "Worldwide"
        pub_date    = _parse_pubdate(text("pubDate"))

        country     = country_from_location(region) if region else "REMOTE"
        visa_signal = bool(_VISA_RE.search(description))
        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = region or None,
            remote            = RemoteType.remote,
            posted_at         = pub_date,
            timestamp_trusted = pub_date is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = fingerprint_ats(apply_url),
            apply_url         = apply_url,
            salary            = None,
            language          = "EN",
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = visa_signal,
        )
    except Exception as exc:
        log.warning("weworkremotely: failed to parse item: %s", exc)
        return None


def _fetch_feed(name: str, url: str) -> list[Job]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "JobPilot/1.0 (+https://github.com/jobpilot)"},
            timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log.error("weworkremotely: failed to fetch feed '%s': %s", name, exc)
        return []

    items = root.findall(".//item")
    jobs  = [j for item in items if (j := _parse_item(item))]
    log.info("weworkremotely: feed=%s → %d jobs", name, len(jobs))
    return jobs


def fetch(feeds: dict[str, str] | None = None) -> list[Job]:
    """Fetch jobs from WeWorkRemotely RSS feeds."""
    targets = feeds or _FEEDS
    seen: set[str] = set()
    all_jobs: list[Job] = []

    for i, (name, url) in enumerate(targets.items()):
        for job in _fetch_feed(name, url):
            if job.job_key not in seen:
                seen.add(job.job_key)
                all_jobs.append(job)

        if i < len(targets) - 1:
            time.sleep(REQUEST_DELAY)

    log.info("weworkremotely: %d unique jobs across %d feeds", len(all_jobs), len(targets))
    return all_jobs
