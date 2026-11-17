"""
SEEK Australia via Apify (config apify.seek_actor_id, default websift/seek-job-scraper).

Input fields used (checked against the actor's input schema): searchTerm,
maxResults, sortBy=ListedDate, dateRange (days), country=australia,
workTypes=[fulltime], developers-programmers / engineering-software.

Queries = paid roles + AU sponsorship queries (search_terms.seek_terms()).
A SEEK hit is never treated as sponsorship evidence by itself — the posting
text is classified later by src/sponsorship.py.

Requires env var: APIFY_TOKEN
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime

from src.config import load
from src.discover.scrapers.apify_runner import affordable_results, run_actor
from src.models import Job
from src.normalize import build_job_key, fingerprint_ats, infer_remote

log = logging.getLogger(__name__)

SOURCE = "seek"
SOURCE_TIER = 2

_TAG_RE = re.compile(r"<[^>]+>")
_YOE_RE = re.compile(r"(\d+)\+?\s*years?\s+of\s+(professional\s+)?experience", re.IGNORECASE)


def _na(v):
    return None if v in (None, "", "N/A") else v


def _parse(raw: dict) -> Job | None:
    title = (raw.get("title") or "").strip()
    adv = raw.get("advertiser") or {}
    prof = raw.get("companyProfile") or {}
    company = (_na(prof.get("name")) or _na(adv.get("name")) or "").strip()
    link = _na(raw.get("jobLink")) or ""
    if not (title and company and link):
        return None
    content = raw.get("content") or {}
    html = content.get("unEditedContent") or " ".join(content.get("sections") or [])
    desc = _TAG_RE.sub(" ", html)
    loc = raw.get("joblocationInfo") or {}
    location = loc.get("displayLocation") or loc.get("location") or "Australia"
    posted = None
    if raw.get("listedAt"):
        try:
            posted = datetime.fromisoformat(raw["listedAt"].replace("Z", "+00:00"))
        except ValueError:
            pass
    apply_url = link
    ext = _na(raw.get("applyLink"))
    if raw.get("isExternalApply") and ext and fingerprint_ats(ext):
        apply_url = ext
    yoe = [int(m[0]) for m in _YOE_RE.findall(desc)]
    return Job(
        job_key=build_job_key(company, title, "AU"),
        title=title, company=company, country="AU", location=location,
        remote=infer_remote(str(raw.get("workArrangements") or ""), location),
        posted_at=posted, timestamp_trusted=posted is not None,
        source=SOURCE, source_tier=SOURCE_TIER,
        ats=fingerprint_ats(apply_url), apply_url=apply_url,
        salary=_na(raw.get("salary")), language="EN", description=desc,
        yoe_max=max(yoe) if yoe else None,
    )


def fetch(markets: list[str] | None = None) -> list[Job]:
    if markets is not None and "AU" not in markets:
        log.info("seek: Australia not in this run's markets — skipping")
        return []
    from src.discover import search_terms
    cfg = load("config")
    apify = cfg.get("apify", {})
    actor = apify.get("seek_actor_id", "websift/seek-job-scraper")
    days = max(1, math.ceil(float(cfg["gate"]["max_age_hours"]) / 24))
    terms = search_terms.seek_terms()
    per_term = int(apify.get("seek_max_results", 40))
    total = affordable_results(actor, "seek", per_term * len(terms))
    per_term = min(per_term, total // max(1, len(terms)))
    if per_term < 5:
        log.warning("seek: Apify budget exhausted — skipping")
        return []
    jobs: dict[str, Job] = {}
    for term in terms:
        inp = {
            "searchTerm": term, "maxResults": per_term,
            "sortBy": "ListedDate", "dateRange": days, "country": "australia",
            "workTypes": ["fulltime"], "developers-programmers": True, "engineering-software": True,
        }
        try:
            items = run_actor(actor, inp, timeout_secs=apify.get("timeout_secs", 300))
        except RuntimeError as exc:
            log.error("seek: %s", exc)
            break   # budget/auth failure — later terms would fail the same way
        for raw in items:
            job = _parse(raw)
            if job:
                jobs.setdefault(job.job_key, job)
    log.info("seek: %d unique jobs", len(jobs))
    return list(jobs.values())
