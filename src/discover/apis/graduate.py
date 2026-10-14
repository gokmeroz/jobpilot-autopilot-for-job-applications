"""
Graduate Programme Discovery adapter.

Fires targeted graduate-specific queries against JSearch (Google for Jobs) and
Adzuna to catch every major format of grad-hiring across all candidate target
countries:

  - "Graduate Programme / Scheme" (UK style)
  - "Graduate Rotational Program"  (large US-company style)
  - "New Grad Program"             (US tech-company style)
  - "Technology / Software Graduate" (EU / global)
  - "Software Engineering Graduate Programme 20XX"

Both sub-sources are optional: if the API key(s) are absent the source silently
returns an empty list so the rest of the pipeline is unaffected.

Usage:
    python main.py --source graduate
"""
from __future__ import annotations

import logging

from src.models import Job

log = logging.getLogger(__name__)

SOURCE = "graduate"

# ---------------------------------------------------------------------------
# JSearch query matrix — graduate / new-grad specific
# ---------------------------------------------------------------------------

_JSEARCH_QUERIES: list[tuple[str, str]] = [
    # Germany
    ("graduate software engineer Germany",                    "DE"),
    ("software engineering graduate programme Germany",       "DE"),
    ("technology graduate scheme Germany",                    "DE"),
    ("new grad developer Germany relocation",                 "DE"),
    ("junior developer graduate programme Germany",           "DE"),
    ("backend engineer new grad Germany",                     "DE"),
    ("graduate AI engineer Germany",                          "DE"),
    # Netherlands
    ("graduate software engineer Netherlands",                "NL"),
    ("technology graduate programme Netherlands",             "NL"),
    ("new grad developer Netherlands relocation",             "NL"),
    ("graduate backend developer Netherlands",                "NL"),
    ("software engineering graduate Netherlands",             "NL"),
    # Ireland
    ("graduate software engineer Ireland",                    "IE"),
    ("technology graduate programme Ireland",                 "IE"),
    ("new grad developer Ireland",                            "IE"),
    ("software engineering graduate scheme Ireland",          "IE"),
    # United Kingdom
    ("graduate software engineer UK",                         "GB"),
    ("software graduate scheme UK",                           "GB"),
    ("technology graduate programme UK",                      "GB"),
    ("new grad developer UK",                                 "GB"),
    ("backend developer graduate programme UK",               "GB"),
    ("graduate backend engineer UK",                          "GB"),
    ("graduate full stack developer UK",                      "GB"),
    ("graduate AI engineer UK",                               "GB"),
    ("technology graduate scheme London",                     "GB"),
    ("engineering graduate programme software UK",            "GB"),
    ("graduate developer scheme computing UK",                "GB"),
    # USA — remote / new grad
    ("new grad software engineer remote",                     "US"),
    ("graduate software engineer remote USA",                 "US"),
    ("software engineering new grad program USA",             "US"),
    ("entry level graduate software developer remote",        "US"),
    ("new graduate backend engineer remote",                  "US"),
    ("AI engineer new grad remote",                           "US"),
    ("technology graduate program remote USA",                "US"),
    # Worldwide / fully remote
    ("graduate software engineer worldwide remote",           "REMOTE"),
    ("new grad developer fully remote",                       "REMOTE"),
    ("graduate full stack developer remote worldwide",        "REMOTE"),
]

# ---------------------------------------------------------------------------
# Adzuna query matrix — graduate / new-grad specific
# ---------------------------------------------------------------------------

_ADZUNA_TARGETS: list[tuple[str, str, str]] = [
    # Germany
    ("de", "DE", "graduate software engineer"),
    ("de", "DE", "graduate developer programme"),
    ("de", "DE", "technology graduate"),
    ("de", "DE", "new grad software developer"),
    # Netherlands
    ("nl", "NL", "graduate software engineer"),
    ("nl", "NL", "technology graduate programme"),
    ("nl", "NL", "new grad developer"),
    ("nl", "NL", "graduate backend developer"),
    # Ireland
    ("ie", "IE", "graduate software engineer"),
    ("ie", "IE", "technology graduate programme"),
    ("ie", "IE", "new grad software developer"),
    # United Kingdom
    ("gb", "GB", "graduate software engineer"),
    ("gb", "GB", "graduate developer scheme"),
    ("gb", "GB", "technology graduate programme"),
    ("gb", "GB", "software engineering graduate"),
    ("gb", "GB", "graduate backend developer"),
    ("gb", "GB", "new grad software engineer"),
    ("gb", "GB", "graduate AI engineer"),
    ("gb", "GB", "engineering graduate programme"),
    # USA
    ("us", "US", "new grad software engineer"),
    ("us", "US", "graduate software developer"),
    ("us", "US", "software engineering new grad"),
    ("us", "US", "technology graduate program"),
    ("us", "US", "graduate AI engineer"),
    ("us", "US", "graduate backend engineer"),
]


# ---------------------------------------------------------------------------
# Public fetch entry point
# ---------------------------------------------------------------------------

def _in_markets(hint: str, markets: list[str] | None) -> bool:
    if markets is None:
        return True
    # US graduate queries are all remote-oriented
    return hint in markets or (hint == "US" and "REMOTE" in markets)


def fetch(markets: list[str] | None = None) -> list[Job]:
    """
    Aggregate graduate-programme jobs from JSearch and Adzuna.

    Both sub-sources are individually optional — if either API key is absent
    that sub-source is skipped and the function returns whatever the other
    sub-source found.
    """
    all_jobs: list[Job] = []
    seen: set[str] = set()
    jsearch_queries = [q for q in _JSEARCH_QUERIES if _in_markets(q[1], markets)]
    adzuna_targets  = [t for t in _ADZUNA_TARGETS if _in_markets(t[1], markets)]

    def _add(jobs: list[Job]) -> None:
        for job in jobs:
            if job.job_key not in seen:
                seen.add(job.job_key)
                all_jobs.append(job)

    # ── JSearch ──────────────────────────────────────────────────────────────
    try:
        from src.discover.apis.jsearch import fetch as _jsearch_fetch
        log.info("graduate: running %d JSearch queries", len(jsearch_queries))
        jobs = _jsearch_fetch(queries=jsearch_queries) if jsearch_queries else []
        _add(jobs)
        log.info("graduate: JSearch returned %d jobs", len(jobs))
    except Exception as exc:
        log.warning("graduate: JSearch sub-source failed: %s", exc)

    # ── Adzuna ───────────────────────────────────────────────────────────────
    try:
        from src.discover.apis.adzuna import fetch as _adzuna_fetch
        log.info("graduate: running %d Adzuna queries", len(adzuna_targets))
        jobs = _adzuna_fetch(targets=adzuna_targets) if adzuna_targets else []
        _add(jobs)
        log.info("graduate: Adzuna returned %d unique new jobs", len(jobs))
    except Exception as exc:
        log.warning("graduate: Adzuna sub-source failed: %s", exc)

    log.info("graduate: %d unique jobs total", len(all_jobs))
    return all_jobs
