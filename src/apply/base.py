"""
Shared types and base class for all ATS form fillers.
"""
from __future__ import annotations

import logging
import random
import time
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
        self.page        = page
        self.job         = job
        self.candidate   = candidate
        self.cfg         = cfg
        self.dry_run: bool = cfg["apply"].get("dry_run", False)
        self.human_like: bool = cfg["apply"].get("human_like", True)
        self._cl_text: str = ""          # pre-computed by prefetch() before page.goto()
        self._cl_pdf_path: Path | None = None  # temp PDF; deleted after submission
        self._parent_page: Page | None = None  # set when form is inside an iframe

    # -- abstract ------------------------------------------------------------

    @abstractmethod
    def fill_form(self) -> None:
        """Fill every field on the form. Raise NeedsUserInput when blocked."""

    def prefetch(self) -> None:
        """Run expensive pre-computation (LLM calls etc.) before the browser navigates.
        Called by the runner before page.goto() so blocking I/O doesn't invalidate
        an active Playwright page context. Override in subclasses as needed.
        """

    # -- helpers -------------------------------------------------------------

    def generate_cover_letter_pdf(self) -> Path:
        """Return path to a temporary PDF of the cover letter, generating it on first call."""
        if self._cl_pdf_path and self._cl_pdf_path.exists():
            return self._cl_pdf_path
        from src.apply.cover_letter_pdf import generate_pdf
        cl_text = self._cl_text or self.candidate.cover_letter_text(
            self.job.title, self.job.company
        )
        self._cl_pdf_path = generate_pdf(cl_text)
        return self._cl_pdf_path

    def cleanup_cover_letter_pdf(self) -> None:
        """Delete the temporary cover letter PDF. Called automatically by the runner."""
        from src.apply.cover_letter_pdf import delete_pdf
        delete_pdf(self._cl_pdf_path)
        self._cl_pdf_path = None

    # -- multi-step form navigation ------------------------------------------

    # Button text patterns that indicate a "Next" pagination step (not submit).
    _NEXT_BTN_TEXTS = (
        "next step",
        "save and continue",
        "continue to next",
        "continue",
        "next",
    )
    # Text substrings that mark a final submit button — exclude from Next detection.
    _SUBMIT_HINTS = (
        "submit",
        "apply now",
        "apply",
        "send application",
        "complete application",
        "finish",
    )

    def _walk_steps(self) -> None:
        """Navigate through paginated form steps, filling each new step's fields.

        Call this just before the submit block in fill_form(). On each step it
        fills cover letter, required fields, and consent checkboxes. Returns
        when no more Next/Continue buttons are found (final step is ready to submit).
        """
        p = self.page
        for _step in range(8):  # safety cap — no real form has >8 steps
            # Find a visible "Next" button that is not a submit
            next_btn = None
            for _text in self._NEXT_BTN_TEXTS:
                try:
                    btn = p.locator(f"button:has-text('{_text}')").first
                    if btn.count() > 0 and btn.is_visible(timeout=300):
                        txt = btn.inner_text().strip().lower()
                        if not any(h in txt for h in self._SUBMIT_HINTS):
                            next_btn = btn
                            break
                except Exception:
                    continue

            if next_btn is None:
                break

            # Click Next and wait for the new step to settle
            self.human_delay(0.2, 0.5)
            next_btn.click()
            log.info(
                "multi-step: advancing to step %d — %s @ %s",
                _step + 2, self.job.title, self.job.company,
            )
            try:
                p.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                p.wait_for_timeout(1_500)

            # Handle fields that appeared on this new step
            self._handle_step()

    def _handle_step(self) -> None:
        """Fill cover letter, required fields, and consent checkboxes on the current step."""
        self._fill_step_cover_letter()
        self._fill_step_required_fields()
        self._fill_step_checkboxes()

    def _fill_step_cover_letter(self) -> bool:
        """Detect and upload/fill a cover letter on the current page. Returns True if found."""
        import re as _re
        p = self.page
        c = self.candidate

        # File input selectors
        for _sel in (
            "input[type='file'][name*='cover']",
            "input[type='file'][id*='cover']",
            "input[type='file'][aria-label*='cover letter' i]",
            "input[type='file'][aria-label*='motivation' i]",
        ):
            try:
                el = p.query_selector(_sel)
                if el:
                    pdf = self.generate_cover_letter_pdf()
                    el.set_input_files(str(pdf))
                    log.info("step: uploaded cover letter PDF (%s)", _sel)
                    return True
            except Exception as exc:
                log.warning("step: CL PDF upload failed (%s): %s", _sel, exc)

        # Label-based detection (Ashby / generic React forms)
        for _frag in ("Cover letter", "Motivation letter"):
            try:
                loc = p.get_by_label(_frag, exact=False)
                if loc.count() > 0:
                    el = loc.first
                    is_file = el.evaluate("e => e.type === 'file'")
                    if is_file:
                        pdf = self.generate_cover_letter_pdf()
                        el.set_input_files(str(pdf))
                        log.info("step: uploaded cover letter PDF via label %r", _frag)
                        return True
                    cl_text = self._cl_text or c.cover_letter_text(self.job.title, self.job.company)
                    el.fill(cl_text)
                    log.info("step: filled cover letter text via label %r", _frag)
                    return True
            except Exception as exc:
                log.warning("step: CL label %r failed: %s", _frag, exc)

        # Textarea name/id/aria selectors
        cl_text = self._cl_text or c.cover_letter_text(self.job.title, self.job.company)
        return self.fill_first([
            "#cover_letter_text",
            "textarea[name*='cover']",
            "textarea[id*='cover']",
            "textarea[name*='coverLetter']",
            "textarea[id*='coverLetter' i]",
            "textarea[aria-label*='cover letter' i]",
            "textarea[placeholder*='cover letter' i]",
            "textarea[name*='motivation']",
            "textarea[aria-label*='motivation' i]",
            "textarea[placeholder*='motivation' i]",
        ], cl_text)

    def _fill_step_required_fields(self) -> None:
        """Fill visible required text/select fields on the current step.

        Uses pattern matching for common question types and LLM fallback for
        open-ended text fields. Fields already handled by the main fill_form()
        pass are skipped via the known-label allow-list.
        """
        import re as _re
        p = self.page
        c = self.candidate

        _KNOWN = {
            "name", "email", "phone", "linkedin", "github", "website",
            "portfolio", "resume", "cover", "location", "city", "country",
            "twitter", "zip", "postal", "pronouns", "gender", "ethnicity",
            "race", "veteran", "disability", "salary", "compensation",
            "demographic", "gdpr", "consent",
        }

        for el in p.locator(
            "input[required], textarea[required], select[required],"
            "input[aria-required='true'], textarea[aria-required='true'],"
            "select[aria-required='true']"
        ).all():
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue
                el_type = (el.get_attribute("type") or "text").lower()
                if el_type in ("file", "checkbox", "radio", "hidden"):
                    continue
                if tag != "select":
                    try:
                        if el.input_value():
                            continue
                    except Exception:
                        continue

                # Resolve label
                el_id = el.get_attribute("id") or ""
                label_text = ""
                if el_id:
                    lbl = p.locator(f"label[for='{el_id}']")
                    if lbl.count():
                        label_text = lbl.first.inner_text()
                if not label_text:
                    label_text = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("placeholder")
                        or el.get_attribute("name")
                        or ""
                    )
                ll = label_text.lower()

                if any(k in ll for k in _KNOWN):
                    continue

                # Pattern-matched common question types
                answered = False
                _rules = [
                    (r"how did you hear|referral|where did you (find|learn|hear)",
                     "Job board", "Job board"),
                    (r"notice period|when can you start|available to start|earliest start",
                     "Immediately available", "Immediately"),
                    (r"relocation|willing to relocate|open to reloc",
                     "Yes", "Yes"),
                    (r"open to remote|work remotely|remote work",
                     "Yes", "Yes"),
                ]
                for pat, text_ans, select_ans in _rules:
                    if _re.search(pat, ll):
                        try:
                            if tag == "select":
                                el.select_option(label=select_ans)
                            else:
                                el.fill(text_ans)
                            answered = True
                            break
                        except Exception:
                            pass

                if not answered and _re.search(
                    r"visa.*sponsor|require.*sponsor|need.*sponsor|sponsorship", ll
                ):
                    ans = "Yes" if c.needs_sponsorship(self.job.country) else "No"
                    try:
                        if tag == "select":
                            el.select_option(label=ans)
                        else:
                            el.fill(ans)
                        answered = True
                    except Exception:
                        pass

                if not answered and _re.search(
                    r"authoris?ed.*work|right to work|work.*authoriz|eligible.*work", ll
                ):
                    try:
                        if tag == "select":
                            el.select_option(label="No")
                        else:
                            el.fill("No")
                        answered = True
                    except Exception:
                        pass

                # LLM fallback for open-ended text/textarea
                if not answered and tag in ("input", "textarea") and label_text:
                    answered = bool(self._llm_answer_field(label_text, el))

            except Exception:
                pass

    def _fill_step_checkboxes(self) -> None:
        """Check required or consent-style checkboxes visible on the current step."""
        import re as _re
        p = self.page
        _CONSENT = _re.compile(
            r"i agree|i accept|i understand|i acknowledge|i confirm|i consent"
            r"|agree to|accept the|terms|privacy policy|gdpr|data.*process",
            _re.IGNORECASE,
        )
        for cb in p.locator("input[type='checkbox']").all():
            try:
                if cb.is_checked():
                    continue
                is_required = (
                    cb.get_attribute("required") is not None
                    or cb.get_attribute("aria-required") == "true"
                )
                label_text = ""
                cb_id = cb.get_attribute("id") or ""
                if cb_id:
                    lbl = p.locator(f"label[for='{cb_id}']")
                    if lbl.count():
                        label_text = lbl.first.inner_text()
                if not label_text:
                    try:
                        label_text = cb.evaluate(
                            "el => el.closest('label')?.innerText"
                            " || el.parentElement?.innerText"
                            " || el.parentElement?.parentElement?.innerText || ''"
                        )
                    except Exception:
                        pass
                if is_required or _CONSENT.search(label_text):
                    try:
                        cb.check()
                    except Exception:
                        try:
                            cb.click()
                        except Exception:
                            pass
            except Exception:
                pass

    def _llm_answer_field(self, label: str, el) -> bool:
        """Use Claude Haiku to answer an open-ended field. Returns True if filled."""
        try:
            from anthropic import Anthropic
            from src.config import env, load as _load_cfg
            _cfg = _load_cfg("config")
            _model = _cfg.get("score", {}).get("model", "claude-haiku-4-5-20251001")
            _client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))
            _resp = _client.messages.create(
                model=_model,
                max_tokens=300,
                messages=[{"role": "user", "content": (
                    f"Fill a job application for Goktug Mert Ozdogan "
                    f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                    f"Nummoria AI SaaS co-founder, open to relocation).\n"
                    f"Role: {self.job.title} at {self.job.company}\n\n"
                    f"Question: {label}\n\n"
                    f"Return ONLY the answer text, 1-3 sentences max."
                )}],
            )
            ans = _resp.content[0].text.strip()
            if ans:
                el.fill(ans)
                return True
        except Exception as exc:
            log.warning("step: LLM fallback failed for %r: %s", label, exc)
        return False

    def human_delay(self, min_s: float = 0.15, max_s: float = 0.5) -> None:
        """Short randomised pause between field interactions."""
        if self.human_like:
            time.sleep(random.uniform(min_s, max_s))

    def fill(self, selector: str, value: str, *, timeout: int = FILL_TIMEOUT) -> bool:
        """Fill a text input. Returns False if element not found."""
        try:
            el = self.page.wait_for_selector(selector, timeout=timeout)
            if el:
                self.human_delay()
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
            # state="attached" — new Greenhouse boards hide the <input type="file">
            # with class="visually-hidden".  Default state="visible" would time out;
            # set_input_files() works on hidden inputs via the change event.
            el = self.page.wait_for_selector(selector, timeout=FILL_TIMEOUT, state="attached")
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
        # When form is inside an iframe, self.page is a Frame (no .screenshot()).
        # Use the original parent page which captures the full viewport including frames.
        target = self._parent_page or self.page
        try:
            target.screenshot(path=str(path), full_page=True)
            log.debug("screenshot → %s", path.name)
        except Exception as exc:
            log.debug("screenshot skipped (%s): %s", path.name, exc)

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
