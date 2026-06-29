"""
Browser runner — opens Playwright, picks the right form filler, runs it,
saves evidence, and returns an ApplicationResult.
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import stealth_sync as _stealth_sync
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

from src.apply.base import ApplicationResult, BaseFormFiller, NeedsUserInput
from src.apply.candidate import load_candidate
from src.apply.forms.ashby_form import AshbyForm
from src.apply.forms.greenhouse_form import GreenhouseForm
from src.apply.forms.lever_form import LeverForm
from src.apply.forms.personio_form import PersonioForm
from src.apply.forms.smartrecruiters_form import SmartRecruitersForm
from src.apply.forms.teamtailor_form import TeamtailorForm
from src.apply.forms.workable_form import WorkableForm
from src.config import ROOT, load
from src.models import Job, Status

log = logging.getLogger(__name__)

_SUCCESS_URL_HINTS = ("confirm", "thank", "success", "submitted", "complete", "thanks", "/done")
_SUCCESS_BODY_HINTS = (
    "thank you",
    "thanks for applying",
    "application received",
    "successfully submitted",
    "application submitted",
    "we'll be in touch",
    "application has been submitted",
    "we received your application",
    "we've received your application",
    "has been received",
    "your submission",
    "you've applied",
    "applied successfully",
    "under review",
)

# Identifiers that are present in the form but gone from DOM after successful submission
_FORM_GONE_SELECTORS = (
    "#first_name",
    "input[name='job_application[first_name]']",
)


def _is_submission_successful(page, original_url: str) -> bool:
    """Return True when there is positive evidence the form was accepted."""
    # 1. URL-based: navigated to a URL containing a success hint
    current = page.url
    if current != original_url:
        curr_lower = current.lower()
        if any(h in curr_lower for h in _SUCCESS_URL_HINTS):
            return True

    # 2. Body text: confirmation message visible anywhere on page
    try:
        body = page.locator("body").inner_text(timeout=3_000).lower()
        if any(h in body for h in _SUCCESS_BODY_HINTS):
            return True
    except Exception:
        pass

    # 3. Form disappeared: Greenhouse new-board (job-boards.greenhouse.io) React
    #    app unmounts the form component after successful submission, removing
    #    identity inputs from the DOM.  Only check when we're still on a
    #    greenhouse.io URL (to avoid false positives from navigation to error pages).
    try:
        if "greenhouse.io" in page.url:
            if all(page.query_selector(sel) is None for sel in _FORM_GONE_SELECTORS):
                return True
    except Exception:
        pass

    return False


_FILLERS: dict[str, type[BaseFormFiller]] = {
    "greenhouse":      GreenhouseForm,
    "lever":           LeverForm,
    "ashby":           AshbyForm,
    "workable":        WorkableForm,
    "smartrecruiters": SmartRecruitersForm,
    "personio":        PersonioForm,
    "teamtailor":      TeamtailorForm,
}


def _evidence_dir(job: Job, run_id: str) -> Path:
    cfg = load("config")
    return ROOT / cfg["paths"]["runs"] / run_id / "evidence" / job.job_key


def _pick_filler(job: Job) -> type[BaseFormFiller] | None:
    ats = (job.ats or "").lower()
    return _FILLERS.get(ats)


def apply_job(job: Job, run_id: str) -> ApplicationResult:
    """
    Apply to a single job. Opens a browser, fills the form, saves evidence.

    Returns an ApplicationResult with status:
      - Status.applied       → submitted successfully
      - Status.failed        → unrecoverable error
      - Status.queued        → needs_user_input flagged
    """
    cfg = load("config")
    candidate = load_candidate()
    apply_cfg = cfg["apply"]
    evidence = _evidence_dir(job, run_id)

    filler_cls = _pick_filler(job)
    if filler_cls is None:
        log.warning("no filler for ats='%s' — routing to manual: %s", job.ats, job.apply_url)
        return ApplicationResult(
            job_key=job.job_key,
            status=Status.queued,
            reason=f"no filler for ATS '{job.ats}' — apply manually at {job.apply_url}",
        )

    max_attempts: int = apply_cfg.get("max_attempts", 2)

    for attempt in range(1, max_attempts + 1):
        log.info(
            "apply attempt %d/%d — %s @ %s (%s)",
            attempt, max_attempts, job.title, job.company, job.ats,
        )
        try:
            result = _run_browser(job, candidate, cfg, filler_cls, evidence, run_id)
            return result
        except Exception as exc:
            log.error("attempt %d failed: %s", attempt, exc)
            if attempt == max_attempts:
                return ApplicationResult(
                    job_key=job.job_key,
                    status=Status.failed,
                    evidence_dir=evidence,
                    reason=str(exc),
                )

    # unreachable but satisfies type checker
    return ApplicationResult(job_key=job.job_key, status=Status.failed)


def _human_delay(min_s: float = 0.3, max_s: float = 0.9) -> None:
    """Short randomised pause to mimic human inter-action latency."""
    time.sleep(random.uniform(min_s, max_s))


def _human_mouse_wander(page) -> None:
    """Move the mouse in a lazy arc across the page before interacting."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]
        # Three random waypoints
        points = [(random.randint(100, w - 100), random.randint(100, h - 100))
                  for _ in range(3)]
        for x, y in points:
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.05, 0.15))
    except Exception:
        pass


def _run_browser(
    job: Job,
    candidate,
    cfg: dict,
    filler_cls: type[BaseFormFiller],
    evidence: Path,
    run_id: str,
) -> ApplicationResult:
    apply_cfg = cfg["apply"]
    dry_run: bool = apply_cfg.get("dry_run", False)
    human_like: bool = apply_cfg.get("human_like", True)
    use_cdp: bool = apply_cfg.get("use_cdp", False)

    _W = random.choice([1280, 1366, 1440, 1920])
    _H = random.choice([768, 800, 900, 1080])
    _UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    )
    _EXTRA_HEADERS = {
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }
    _CONTEXT_OPTS = dict(
        accept_downloads=True,
        locale="en-US",
        timezone_id="Europe/Istanbul",
        viewport={"width": _W, "height": _H},
        user_agent=_UA,
        extra_http_headers=_EXTRA_HEADERS,
    )

    _STEALTH_JS = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=apply_cfg.get("headless", True))
        ctx = browser.new_context(**_CONTEXT_OPTS)
        page = ctx.new_page()

        # Stealth plugin (if installed) patches WebGL, canvas fingerprint, etc.
        if _STEALTH_AVAILABLE:
            _stealth_sync(page)

        # JS evasions: cover webdriver flag and plugin/language spoofing in all modes.
        page.add_init_script(_STEALTH_JS)

        filler = filler_cls(page, job, candidate, cfg)

        try:
            # prefetch runs LLM calls (e.g. cover-letter placeholder fill) before the
            # browser navigates so a slow API call can't invalidate the page context.
            filler.prefetch()

            page.goto(job.apply_url, timeout=30_000, wait_until="domcontentloaded")

            # Brief wander to look human before touching any form field
            if human_like:
                _human_delay(0.8, 1.8)
                _human_mouse_wander(page)
                _human_delay(0.3, 0.7)

            # Screenshot before filling
            filler.screenshot("01_start", evidence)

            filler.fill_form()

            # Screenshot after filling (before submit)
            filler.screenshot("02_filled", evidence)

            if not dry_run:
                # Submit happens inside fill_form → wait then verify
                page.wait_for_load_state("networkidle", timeout=15_000)
                filler.screenshot("03_submitted", evidence)
                log.info("post-submit URL: %s", page.url)
                try:
                    _body_snippet = page.locator("body").inner_text(timeout=3_000)[:300].replace("\n", " ")
                    log.info("post-submit body snippet: %r", _body_snippet)
                except Exception:
                    pass
                _fn_el = page.query_selector("#first_name")
                log.info("post-submit #first_name present: %s", _fn_el is not None)

                # When the form was inside an iframe, filler.page is a Frame object
                # (the iframe's context). Check the frame for confirmation too.
                _form_page = filler.page
                if not _is_submission_successful(page, job.apply_url) and \
                   not _is_submission_successful(_form_page, job.apply_url):
                    log.warning(
                        "submission failed (no confirmation) — %s @ %s",
                        job.title, job.company,
                    )
                    return ApplicationResult(
                        job_key=job.job_key,
                        status=Status.queued,
                        evidence_dir=evidence,
                        reason="NEEDS_USER_INPUT: form submitted but no confirmation detected — check evidence screenshots",
                    )

                log.info("applied → %s @ %s", job.title, job.company)
                return ApplicationResult(
                    job_key=job.job_key,
                    status=Status.applied,
                    evidence_dir=evidence,
                )
            else:
                log.info("dry_run — form filled but not submitted: %s @ %s", job.title, job.company)
                return ApplicationResult(
                    job_key=job.job_key,
                    status=Status.queued,
                    evidence_dir=evidence,
                    reason="dry_run=true",
                )

        except NeedsUserInput as exc:
            filler.screenshot("99_blocked", evidence)
            log.warning("needs user input — %s @ %s: %s", job.title, job.company, exc)
            return ApplicationResult(
                job_key=job.job_key,
                status=Status.queued,
                evidence_dir=evidence,
                reason=f"NEEDS_USER_INPUT: {exc}",
            )

        finally:
            browser.close()
            filler.cleanup_cover_letter_pdf()


def apply_batch(jobs: list[Job], run_id: str) -> list[ApplicationResult]:
    """Apply to a list of jobs sequentially. Returns all results."""
    results: list[ApplicationResult] = []
    for job in jobs:
        results.append(apply_job(job, run_id))
    return results
