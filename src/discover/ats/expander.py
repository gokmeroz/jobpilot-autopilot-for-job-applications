"""
Dynamic ATS company expander.

When JSearch / Adzuna / other job-board adapters return a job whose apply_url
points to a known ATS (Greenhouse, Ashby, Lever), this module extracts the
company token/handle from that URL and fetches ALL open jobs from that company
via the ATS API — not just the single job that appeared in search results.

This auto-discovers new companies the pipeline has never seen before, without
requiring any manual edits to sources.yaml.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

from src.models import Job

log = logging.getLogger(__name__)

# Regex patterns to extract the company token from ATS URLs
_GH_TOKEN_RE = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", re.IGNORECASE
)
_ASHBY_HANDLE_RE = re.compile(
    r"jobs\.ashbyhq\.com/([^/?#]+)", re.IGNORECASE
)
_LEVER_SLUG_RE = re.compile(
    r"jobs\.lever\.co/([^/?#]+)", re.IGNORECASE
)


def _extract_greenhouse_token(url: str) -> str | None:
    m = _GH_TOKEN_RE.search(url)
    return m.group(1).lower() if m else None


def _extract_ashby_handle(url: str) -> str | None:
    m = _ASHBY_HANDLE_RE.search(url)
    handle = m.group(1).lower() if m else None
    # Skip bare /apply paths — they are not company handles
    return None if handle in ("apply", "") else handle


def _extract_lever_slug(url: str) -> str | None:
    m = _LEVER_SLUG_RE.search(url)
    return m.group(1).lower() if m else None


def _known_tokens(sources: dict, key: str) -> set[str]:
    """Return the set of token values already in sources.yaml for a given ATS."""
    return {str(v).lower() for v in (sources.get(key) or {}).values()}


def expand(jobs: list[Job], request_delay: float = 0.5) -> list[Job]:
    """
    For each job whose apply_url points to Greenhouse / Ashby / Lever, extract
    the company token and fetch all open jobs from that company via the ATS API.

    Only fetches companies not already covered by sources.yaml so we don't
    double-hit the same board.

    Returns the original jobs list plus any additional jobs discovered.
    """
    from src.config import load
    from src.discover.ats.greenhouse import _fetch_company as gh_fetch
    from src.discover.ats.ashby import _fetch_company as ashby_fetch
    from src.discover.ats.lever import _fetch_company as lever_fetch

    sources = load("sources")
    known_gh    = _known_tokens(sources, "greenhouse")
    known_ashby = _known_tokens(sources, "ashby")
    known_lever = _known_tokens(sources, "lever")

    # Tokens we've queued this run (avoid fetching the same company twice)
    queued_gh:    set[str] = set()
    queued_ashby: set[str] = set()
    queued_lever: set[str] = set()

    extra: list[Job] = []

    for job in jobs:
        url = job.apply_url or ""

        if "greenhouse.io" in url:
            token = _extract_greenhouse_token(url)
            if token and token not in known_gh and token not in queued_gh:
                queued_gh.add(token)
                display = job.company or token
                log.info("expander: new Greenhouse company '%s' (%s) — fetching all jobs", display, token)
                new_jobs = gh_fetch(display, token)
                extra.extend(new_jobs)
                if request_delay > 0:
                    time.sleep(request_delay)

        elif "ashbyhq.com" in url:
            handle = _extract_ashby_handle(url)
            if handle and handle not in known_ashby and handle not in queued_ashby:
                queued_ashby.add(handle)
                display = job.company or handle
                log.info("expander: new Ashby company '%s' (%s) — fetching all jobs", display, handle)
                new_jobs = ashby_fetch(display, handle)
                extra.extend(new_jobs)
                if request_delay > 0:
                    time.sleep(request_delay)

        elif "lever.co" in url:
            slug = _extract_lever_slug(url)
            if slug and slug not in known_lever and slug not in queued_lever:
                queued_lever.add(slug)
                display = job.company or slug
                log.info("expander: new Lever company '%s' (%s) — fetching all jobs", display, slug)
                new_jobs = lever_fetch(display, slug)
                extra.extend(new_jobs)
                if request_delay > 0:
                    time.sleep(request_delay)

    if extra:
        log.info(
            "expander: discovered %d additional jobs from %d new companies (gh=%d ashby=%d lever=%d)",
            len(extra), len(queued_gh) + len(queued_ashby) + len(queued_lever),
            len(queued_gh), len(queued_ashby), len(queued_lever),
        )

    return jobs + extra
