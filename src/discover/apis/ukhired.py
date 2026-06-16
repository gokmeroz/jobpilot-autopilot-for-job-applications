"""
UKHired job board RSS adapter.

UKHired (ukhired.co.uk) lists UK jobs that offer Skilled Worker visa sponsorship.
Strong signal for UK relocation candidates — every listing is a sponsor-willing employer.
Feed: https://ukhired.co.uk/feed/
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from src.models import Job, RemoteType
from src.normalize import build_job_key, infer_remote

log = logging.getLogger(__name__)

_RSS_URL = "https://ukhired.co.uk/feed/"
SOURCE = "ukhired"
SOURCE_TIER = 1  # UK sponsorship jobs — highest priority for UK relocation

_HEADERS = {
    "User-Agent": "JobPilot/1.0 (+https://github.com/jobpilot)",
    "Accept": "application/rss+xml, application/xml, text/xml",
}

_NAMESPACES = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
}

_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)]]>", re.DOTALL)

# "Role at Company" or "Role – Company" title patterns
_TITLE_AT_RE = re.compile(r"^(.+?)\s+(?:at|@|–|-)\s+(.+)$", re.IGNORECASE)


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
        return parsedate_to_datetime(date_str.strip()).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(date_str.strip())
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _text(item: ET.Element, tag: str) -> str:
    el = item.find(tag)
    if el is None:
        for ns_prefix, ns_uri in _NAMESPACES.items():
            el = item.find(f"{{{ns_uri}}}{tag}")
            if el is not None:
                break
    if el is None:
        return ""
    raw = (el.text or "").strip()
    return _unwrap_cdata(raw)


def _parse_item(item: ET.Element) -> Job | None:
    try:
        raw_title = _text(item, "title")
        if not raw_title:
            return None

        # Try to split "Role at Company" patterns
        m = _TITLE_AT_RE.match(raw_title)
        if m:
            title   = m.group(1).strip()
            company = m.group(2).strip()
        else:
            title   = raw_title.strip()
            company = ""

        # Fallback: company from dc:creator or category
        if not company:
            company = _text(item, "creator") or _text(item, "author") or "Unknown"

        # Apply URL — prefer <link> over <guid>
        link_el = item.find("link")
        apply_url = ""
        if link_el is not None:
            apply_url = (link_el.tail or link_el.text or "").strip()
        if not apply_url:
            guid_el = item.find("guid")
            apply_url = (guid_el.text if guid_el is not None else "").strip()

        if not apply_url or not title:
            return None

        description = _strip_tags(_text(item, "description") or _text(item, "encoded"))
        pub_date    = _parse_pubdate(_text(item, "pubDate") or _text(item, "date"))

        # All UKHired jobs are UK-based
        country = "GB"
        # Check for remote mentions in description
        remote  = infer_remote("United Kingdom", description[:400])

        yoe_matches = _YOE_RE.findall(description)
        yoe_max     = max(int(m[0]) for m in yoe_matches) if yoe_matches else None

        return Job(
            job_key           = build_job_key(company, title, country),
            title             = title,
            company           = company,
            country           = country,
            location          = "United Kingdom",
            remote            = remote,
            posted_at         = pub_date,
            timestamp_trusted = pub_date is not None,
            source            = SOURCE,
            source_tier       = SOURCE_TIER,
            ats               = None,
            apply_url         = apply_url,
            salary            = None,
            language          = "EN",
            description       = description,
            yoe_max           = yoe_max,
            visa_signal       = True,  # every UKHired listing is a sponsor-willing employer
        )
    except Exception as exc:
        log.warning("ukhired: failed to parse item: %s", exc)
        return None


def fetch() -> list[Job]:
    """Fetch UK visa-sponsorship jobs from UKHired RSS feed."""
    try:
        resp = requests.get(_RSS_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log.error("ukhired: feed fetch failed: %s", exc)
        return []

    items = root.findall(".//item")
    seen: set[str] = set()
    jobs: list[Job] = []

    for item in items:
        job = _parse_item(item)
        if job and job.job_key not in seen:
            seen.add(job.job_key)
            jobs.append(job)

    log.info("ukhired: %d jobs from RSS feed", len(jobs))
    return jobs
