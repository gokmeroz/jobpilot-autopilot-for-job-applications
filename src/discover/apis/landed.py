"""
Landed — tech jobs tagged with visa sponsorship, per country.
https://www.landed.jobs/visa-sponsorship/<country>

The listing page carries a schema.org ItemList; each job page carries a
JobPosting (title, company, datePosted, location) plus the employer's original
ATS link, which we use as apply_url so the expander/apply flow see the real
board. Being listed on Landed is treated as a lead, not as sponsorship proof.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

from src.models import Job
from src.normalize import build_job_key, fingerprint_ats, infer_remote

log = logging.getLogger(__name__)

SOURCE = "landed"
SOURCE_TIER = 2
_PAGES = {"AU": "australia", "GB": "uk", "IE": "ireland", "DE": "germany",
          "NL": "netherlands", "CA": "canada", "US": "usa"}
_UA = {"User-Agent": "Mozilla/5.0 (JobPilot job search)"}
_LD_RE = re.compile(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', re.S)
_ATS_LINK_RE = re.compile(r'href="(https?://(?:job-boards\.|boards\.)?(?:greenhouse\.io|jobs\.ashbyhq\.com|jobs\.lever\.co|[\w.-]*myworkdayjobs\.com|[\w.-]*smartrecruiters\.com|apply\.workable\.com)[^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")


def _ld(html: str) -> list[dict]:
    out = []
    for block in _LD_RE.findall(html):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        out += d if isinstance(d, list) else [d]
    return out


def _job_page(url: str, iso: str) -> Job | None:
    html = requests.get(url, headers=_UA, timeout=20).text
    post = next((d for d in _ld(html) if d.get("@type") == "JobPosting"), None)
    if not post:
        return None
    title = (post.get("title") or "").strip()
    company = ((post.get("hiringOrganization") or {}).get("name") or "").strip()
    if not title or not company:
        return None
    addr = ((post.get("jobLocation") or {}).get("address") or {}) if isinstance(post.get("jobLocation"), dict) else {}
    location = ", ".join(filter(None, [addr.get("addressLocality"), addr.get("addressCountry")])) or iso
    country = (addr.get("addressCountry") or iso).upper()
    posted = None
    if post.get("datePosted"):
        try:
            posted = datetime.fromisoformat(post["datePosted"]).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    ats = _ATS_LINK_RE.search(html)
    apply_url = ats.group(1) if ats else url
    desc = _TAG_RE.sub(" ", post.get("description") or "")
    return Job(
        job_key=build_job_key(company, title, country),
        title=title, company=company, country=country, location=location,
        remote=infer_remote(location, desc[:300]), posted_at=posted,
        timestamp_trusted=posted is not None, source=SOURCE, source_tier=SOURCE_TIER,
        ats=fingerprint_ats(apply_url), apply_url=apply_url, description=desc,
    )


def fetch(markets: list[str] | None = None, delay: float = 0.3) -> list[Job]:
    jobs: list[Job] = []
    for iso, slug in _PAGES.items():
        if markets is not None and iso not in markets:
            continue
        try:
            html = requests.get(f"https://www.landed.jobs/visa-sponsorship/{slug}", headers=_UA, timeout=20).text
        except requests.RequestException as exc:
            log.warning("landed: %s list failed: %s", slug, exc)
            continue
        urls = [it.get("url") for d in _ld(html) if d.get("@type") == "ItemList"
                for it in d.get("itemListElement", []) if it.get("url")]
        for url in urls:
            try:
                job = _job_page(url, iso)
            except requests.RequestException as exc:
                log.debug("landed: %s failed: %s", url, exc)
                continue
            if job:
                jobs.append(job)
            time.sleep(delay)
    log.info("landed: %d jobs", len(jobs))
    return jobs
