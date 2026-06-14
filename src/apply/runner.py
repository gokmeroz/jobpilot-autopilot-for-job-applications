"""
Browser runner — opens Playwright, picks the right form filler, runs it,
saves evidence, and returns an ApplicationResult.
"""
from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.apply.base import ApplicationResult, BaseFormFiller, NeedsUserInput
from src.apply.candidate import load_candidate
from src.apply.forms.ashby_form import AshbyForm
from src.apply.forms.greenhouse_form import GreenhouseForm
from src.apply.forms.lever_form import LeverForm
from src.apply.forms.workable_form import WorkableForm
from src.config import ROOT, load
from src.models import Job, Status

log = logging.getLogger(__name__)

_FILLERS: dict[str, type[BaseFormFiller]] = {
    "greenhouse":     GreenhouseForm,
    "lever":          LeverForm,
    "ashby":          AshbyForm,
    "workable":       WorkableForm,
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

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=apply_cfg.get("headless", True))
        ctx = browser.new_context(
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        try:
            page.goto(job.apply_url, timeout=30_000, wait_until="domcontentloaded")

            filler = filler_cls(page, job, candidate, cfg)

            # Screenshot before filling
            filler.screenshot("01_start", evidence)

            filler.fill_form()

            # Screenshot after filling (before submit)
            filler.screenshot("02_filled", evidence)

            if not dry_run:
                # Submit happens inside fill_form → screenshot after navigation
                page.wait_for_load_state("networkidle", timeout=15_000)
                filler.screenshot("03_submitted", evidence)
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


def apply_batch(jobs: list[Job], run_id: str) -> list[ApplicationResult]:
    """Apply to a list of jobs sequentially. Returns all results."""
    results: list[ApplicationResult] = []
    for job in jobs:
        results.append(apply_job(job, run_id))
    return results
