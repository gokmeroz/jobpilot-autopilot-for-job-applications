"""
Listing verification — is the posting still open, and is apply_url the employer's?

  open        ATS API source (Greenhouse/Ashby/Lever boards list only live jobs),
              or the apply page loaded without a closed marker
  closed      HTTP 404/410 or a "no longer accepting" style marker
  unverified  could not check (LinkedIn/SEEK login walls, bot blocks, timeouts)

Closed jobs are excluded from the shortlist; unverified jobs stay but are
labelled so they are never presented as verified vacancies.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from src.models import Job

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
_API_SOURCES = {"greenhouse", "ashby", "lever"}
_UNCHECKABLE = ("linkedin.com", "seek.com.au", "indeed.", "glassdoor.")
_CLOSED = re.compile(
    r"no\s+longer\s+(?:accepting|available|open|active)|job\s+(?:not\s+found|has\s+expired|is\s+closed)|"
    r"position\s+has\s+been\s+filled|this\s+(?:job|role|position|posting)\s+(?:has\s+been\s+)?(?:closed|filled|removed)|"
    r"page\s+(?:you\s+(?:are|were)\s+looking\s+for\s+)?(?:could\s+not\s+be\s+found|doesn.t\s+exist)",
    re.IGNORECASE,
)


def check(job: Job) -> str:
    if job.source in _API_SOURCES:
        return "open (live on ATS board API)"
    url = job.apply_url or ""
    if any(d in url for d in _UNCHECKABLE):
        return "unverified (board requires login / blocks bots)"
    try:
        r = requests.get(url, headers=_UA, timeout=15, allow_redirects=True)
    except requests.RequestException as exc:
        return f"unverified ({type(exc).__name__})"
    if r.status_code in (404, 410):
        return f"closed (HTTP {r.status_code})"
    if r.status_code >= 400:
        return f"unverified (HTTP {r.status_code})"
    m = _CLOSED.search(r.text[:200_000])
    if m:
        return f"closed ('{m.group(0)[:40]}')"
    return "open (apply page live)"


def verify_batch(jobs: list[Job], workers: int = 8) -> list[Job]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, jobs))
    out = [j.model_copy(update={"verification": v}) for j, v in zip(jobs, results)]
    closed = sum(1 for j in out if j.verification.startswith("closed"))
    log.info("verify: %d checked — %d closed, %d unverified", len(out), closed,
             sum(1 for j in out if j.verification.startswith("unverified")))
    return out


def is_closed(job: Job) -> bool:
    return bool(job.verification and job.verification.startswith("closed"))
