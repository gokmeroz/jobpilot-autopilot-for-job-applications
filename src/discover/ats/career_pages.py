"""
Direct employer career pages (config/sources.yaml `career_pages`).

Research leads only: each page is fetched, Greenhouse/Ashby/Lever board links
on it are expanded into every open job on that board, and the page itself is
recorded in the coverage log. Nothing here asserts a role is open or sponsored.
"""
from __future__ import annotations

import logging
import re

import requests

from src.config import load

log = logging.getLogger(__name__)

SOURCE = "career_page"
_UA = {"User-Agent": "Mozilla/5.0 (JobPilot job search)"}
_BOARD_RE = re.compile(
    r"https?://(?:job-boards\.|boards\.)?greenhouse\.io/[\w-]+|https?://jobs\.ashbyhq\.com/[\w.-]+|https?://jobs\.lever\.co/[\w-]+",
    re.IGNORECASE,
)

COVERAGE: list[dict] = []   # [{company, url, status, boards}]


def fetch(markets: list[str] | None = None) -> list:
    from src.discover.ats.ashby import _fetch_company as ashby
    from src.discover.ats.greenhouse import _fetch_company as gh
    from src.discover.ats.lever import _fetch_company as lever

    COVERAGE.clear()
    jobs: list = []
    for company, url in (load("sources").get("career_pages") or {}).items():
        boards = set(_BOARD_RE.findall(url))
        status = "ok"
        if not boards:
            try:
                html = requests.get(url, headers=_UA, timeout=20).text
                boards = set(_BOARD_RE.findall(html))
                status = "ok" if boards else "reachable, no supported ATS board found — check manually"
            except requests.RequestException as exc:
                status = f"unreachable: {exc}"
        for b in boards:
            token = b.rstrip("/").rsplit("/", 1)[-1]
            if "greenhouse" in b:
                jobs += gh(company, token)
            elif "ashbyhq" in b:
                jobs += ashby(company, token)
            else:
                jobs += lever(company, token)
        COVERAGE.append({"company": company, "url": url, "status": status, "boards": sorted(boards)})
        log.info("career page %s: %s (%d board(s))", company, status, len(boards))
    return jobs
