"""
Post-score LinkedIn URL resolver.

For above-threshold jobs whose apply_url is a LinkedIn page (ats=None),
uses Playwright to render the page and extract the real external apply URL.
Only called for the small set of jobs that pass scoring (typically < 15/run),
so the overhead of a headless browser per job is acceptable.

Falls back silently — if resolution fails the original LinkedIn URL is kept
and the job is routed to manual application.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

from src.models import Job

log = logging.getLogger(__name__)

_ATS_MAP: dict[str, str] = {
    "greenhouse.io":       "greenhouse",
    "lever.co":            "lever",
    "ashbyhq.com":         "ashby",
    "workable.com":        "workable",
    "smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com":   "workday",
    "jobvite.com":         "jobvite",
    "icims.com":           "icims",
    "taleo.net":           "taleo",
    "successfactors.com":  "successfactors",
    "breezy.hr":           "breezy",
    "recruitee.com":       "recruitee",
    "personio.de":         "personio",
    "personio.com":        "personio",
}

_LINKEDIN_RE = re.compile(r"linkedin\.com", re.I)


def _fingerprint(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        for domain, name in _ATS_MAP.items():
            if domain in host:
                return name
    except Exception:
        pass
    return None


def _is_linkedin_url(url: str) -> bool:
    return bool(_LINKEDIN_RE.search(url))


def _clean_linkedin_url(url: str) -> str:
    """Strip tracking params — keeps the clean job URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


# Selectors LinkedIn uses for "Apply on company website" button
# These may drift over time as LinkedIn updates its frontend.
_OFFSITE_SELECTORS = [
    # Public job listing page — non-logged-in view
    'a[data-tracking-control-name="public_jobs_apply-link-offsite_sign-up"]',
    'a[data-tracking-control-name="public_jobs_apply-link-offsite"]',
    # Logged-in view
    'a.jobs-apply-button[href*="://"]',
    # Generic: any <a> button whose text contains "Apply" and href is external
    'button.jobs-apply-button',
]

_EASY_APPLY_SELECTORS = [
    'button[aria-label*="Easy Apply"]',
    'button.jobs-apply-button[aria-label*="Easy Apply"]',
    'span:text("Easy Apply")',
]


def _resolve_with_playwright(linkedin_url: str) -> str | None:
    """
    Open a LinkedIn job page headlessly and extract the external apply URL.
    Returns None if the job uses LinkedIn Easy Apply or the URL can't be found.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    clean = _clean_linkedin_url(linkedin_url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            page.goto(clean, timeout=20_000, wait_until="domcontentloaded")
            # Give JS a moment to hydrate
            page.wait_for_timeout(2_000)

            # Check for Easy Apply first — if present, no external URL exists
            for sel in _EASY_APPLY_SELECTORS:
                try:
                    if page.locator(sel).count() > 0:
                        log.debug("resolve: Easy Apply detected — no external URL: %s", clean)
                        return None
                except Exception:
                    pass

            # Try offsite apply selectors
            for sel in _OFFSITE_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        href = el.get_attribute("href")
                        if href and not _is_linkedin_url(href) and href.startswith("http"):
                            return href
                except Exception:
                    pass

            # Fallback: scan all <a> hrefs for non-LinkedIn ATS domains
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for href in hrefs:
                if not _is_linkedin_url(href) and _fingerprint(href):
                    return href

        except PWTimeout:
            log.debug("resolve: page timeout for %s", clean)
        except Exception as exc:
            log.debug("resolve: error for %s: %s", clean, exc)
        finally:
            browser.close()

    return None


def _resolve_one(job: Job) -> Job:
    """Try to resolve a LinkedIn job URL to its external apply URL."""
    external = _resolve_with_playwright(job.apply_url)
    if external:
        ats = _fingerprint(external)
        log.info(
            "resolve: %s @ %s → %s (ats=%s)",
            job.title, job.company, external[:80], ats,
        )
        return job.model_copy(update={"apply_url": external, "ats": ats})

    log.debug("resolve: no external URL for %s @ %s", job.title, job.company)
    return job


def resolve_apply_urls(jobs: list[Job], delay: float = 1.0) -> list[Job]:
    """
    For each above-threshold job with a LinkedIn URL (ats=None), attempt to
    resolve the real external apply URL via headless browser.

    Jobs with ATS URLs already set are returned unchanged.
    Falls back silently — on failure the original LinkedIn URL is kept.
    """
    to_resolve = [j for j in jobs if j.ats is None and _is_linkedin_url(j.apply_url)]
    already_set = [j for j in jobs if not (j.ats is None and _is_linkedin_url(j.apply_url))]

    if not to_resolve:
        return jobs

    log.info("resolve: attempting URL resolution for %d LinkedIn jobs", len(to_resolve))

    resolved: list[Job] = list(already_set)
    upgraded = 0

    for job in to_resolve:
        updated = _resolve_one(job)
        if updated.ats is not None:
            upgraded += 1
        resolved.append(updated)
        if delay > 0:
            time.sleep(delay)

    log.info(
        "resolve: %d/%d LinkedIn jobs upgraded to direct ATS URLs",
        upgraded, len(to_resolve),
    )

    # Restore original order
    order = {j.job_key: i for i, j in enumerate(jobs)}
    resolved.sort(key=lambda j: order.get(j.job_key, 999))
    return resolved
