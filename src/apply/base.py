"""
Shared types and base class for all ATS form fillers.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout

from src.apply.candidate import CandidateData
from src.models import Job, Status

log = logging.getLogger(__name__)

FILL_TIMEOUT = 8_000    # ms — time to wait for any single element
NAV_TIMEOUT  = 20_000   # ms — page navigation


class NeedsUserInput(Exception):
    """Raised when the form contains a question the pipeline cannot answer."""


@dataclass
class ApplicationResult:
    job_key: str
    status: Status
    evidence_dir: Path | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Base filler
# ---------------------------------------------------------------------------

class BaseFormFiller(ABC):
    """
    Abstract base for ATS form fillers.

    Subclasses implement `fill_form()`. Common helpers live here so each
    filler stays focused on its own selectors.
    """

    def __init__(self, page: Page, job: Job, candidate: CandidateData, cfg: dict) -> None:
        self.page      = page
        self.job       = job
        self.candidate = candidate
        self.cfg       = cfg
        self.dry_run: bool = cfg["apply"].get("dry_run", False)

    # -- abstract ------------------------------------------------------------

    @abstractmethod
    def fill_form(self) -> None:
        """Fill every field on the form. Raise NeedsUserInput when blocked."""

    # -- helpers -------------------------------------------------------------

    def fill(self, selector: str, value: str, *, timeout: int = FILL_TIMEOUT) -> bool:
        """Fill a text input. Returns False if element not found."""
        try:
            el = self.page.wait_for_selector(selector, timeout=timeout)
            if el:
                el.fill(value)
                return True
        except PWTimeout:
            pass
        return False

    def fill_first(self, selectors: list[str], value: str) -> bool:
        """Try selectors in order, fill the first one that exists."""
        for sel in selectors:
            if self.fill(sel, value, timeout=2_000):
                return True
        return False

    def upload(self, selector: str, path: Path) -> bool:
        """Upload a file. Returns False if element not found."""
        try:
            el = self.page.wait_for_selector(selector, timeout=FILL_TIMEOUT)
            if el:
                el.set_input_files(str(path))
                return True
        except PWTimeout:
            pass
        return False

    def select_option(self, selector: str, value: str, *, timeout: int = FILL_TIMEOUT) -> bool:
        """Select a <select> option by value or label text."""
        try:
            el = self.page.wait_for_selector(selector, timeout=timeout)
            if not el:
                return False
            # Try by value first, then visible text
            try:
                el.select_option(value=value)
                return True
            except Exception:
                pass
            try:
                el.select_option(label=value)
                return True
            except Exception:
                pass
        except PWTimeout:
            pass
        return False

    def click_radio(self, selector: str) -> bool:
        """Click a radio button or checkbox."""
        try:
            el = self.page.wait_for_selector(selector, timeout=FILL_TIMEOUT)
            if el:
                el.click()
                return True
        except PWTimeout:
            pass
        return False

    def screenshot(self, name: str, evidence_dir: Path) -> None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        path = evidence_dir / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=True)
        log.debug("screenshot → %s", path.name)

    def submit(self, selector: str) -> None:
        """Click submit — skipped entirely in dry_run mode."""
        if self.dry_run:
            log.info("dry_run=true — skipping submit for %s @ %s", self.job.title, self.job.company)
            return
        try:
            btn = self.page.wait_for_selector(selector, timeout=FILL_TIMEOUT)
            if btn:
                btn.click()
        except PWTimeout:
            raise NeedsUserInput(f"Submit button not found: {selector}")
