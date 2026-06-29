"""
Workday ATS form filler.

URL pattern: https://{company}.myworkdayjobs.com/en-US/{tenant}/job/{title}/{id}

Workday uses data-automation-id attributes as stable selectors across all
tenants. The application wizard is multi-step; each step is navigated via
the "Next" button (data-automation-id='bottom-navigation-next-btn').

Workday allows guest (unauthenticated) apply — we always choose this path.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)

# -- Stable Workday data-automation-id selectors ----------------------------
_AI = "data-automation-id"

_SEL_APPLY_BTN    = f"a[{_AI}='applyButton'], button[{_AI}='applyButton']"
_SEL_GUEST        = (
    f"button[{_AI}='guestButton'],"
    f"a[{_AI}='guestButton'],"
    "button:has-text('Apply Manually'),"
    "a:has-text('Apply Manually'),"
    "button:has-text('Continue as Guest'),"
    "a:has-text('Continue as Guest')"
)
_SEL_FNAME        = f"input[{_AI}='legalNameSection_firstName']"
_SEL_LNAME        = f"input[{_AI}='legalNameSection_lastName']"
_SEL_EMAIL        = f"input[{_AI}='email']"
_SEL_PHONE        = f"input[{_AI}='phone-number']"
_SEL_COUNTRY      = f"div[{_AI}='addressSection_countryDropdown']"
_SEL_CITY         = f"input[{_AI}='addressSection_city']"
_SEL_STATE        = f"input[{_AI}='addressSection_addressLine1']"
_SEL_RESUME_UPLOAD = (
    f"input[{_AI}='file-upload-input-ref'],"
    "input[type='file']"
)
_SEL_HOW_HEARD    = (
    f"select[{_AI}='howDidYouHearAboutUs'],"
    "select[id*='howDidYou' i]"
)
_SEL_NEXT         = f"button[{_AI}='bottom-navigation-next-btn']"
_SEL_SUBMIT       = (
    f"button[{_AI}='bottom-navigation-next-btn'],"  # same btn on last step
    f"button[{_AI}='submitButton'],"
    "button:has-text('Submit'),"
    "button:has-text('Review and submit')"
)

# Workday generic question container
_SEL_QUESTIONS    = f"div[{_AI}='formField-question']"

_CONSENT_RE = re.compile(
    r"i agree|i consent|i accept|privacy|gdpr|terms|data.*process",
    re.IGNORECASE,
)


def _wd_fill(page, sel: str, value: str, timeout: int = 6_000) -> bool:
    """Fill a Workday text input, retrying once on stale-element."""
    for _ in range(2):
        try:
            el = page.wait_for_selector(sel, timeout=timeout)
            if el:
                el.triple_click()
                el.fill(value)
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _wd_select(page, sel: str, *opts: str) -> bool:
    """Select from a Workday listbox. Workday uses a custom dropdown widget."""
    try:
        trigger = page.wait_for_selector(sel, timeout=4_000)
        if not trigger:
            return False
        trigger.click()
        time.sleep(0.4)
        for opt in opts:
            try:
                item = page.locator(f"li[role='option']:has-text('{opt}')").first
                if item.count() > 0 and item.is_visible(timeout=800):
                    item.click()
                    return True
            except Exception:
                pass
        # Dismiss dropdown if nothing matched
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    except Exception:
        pass
    return False


def _wd_radio_yes_no(page, container_sel: str, answer: str) -> bool:
    """Click Yes or No within a Workday radio-button group."""
    try:
        container = page.locator(container_sel).first
        if container.count() == 0:
            return False
        btn = container.locator(f"label:has-text('{answer}')").first
        if btn.count() > 0 and btn.is_visible(timeout=600):
            btn.click()
            return True
    except Exception:
        pass
    return False


def _llm_answer(label: str, candidate, job) -> str:
    try:
        from anthropic import Anthropic
        from src.config import env, load as _load
        cfg = _load("config")
        model = cfg.get("score", {}).get("model", "claude-haiku-4-5-20251001")
        client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": (
                f"Fill a Workday job application for Goktug Mert Ozdogan "
                f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                f"Nummoria AI SaaS co-founder, open to relocation).\n"
                f"Role: {job.title} at {job.company}\n\n"
                f"Question: {label}\n\n"
                f"Return ONLY the answer text, 1-3 sentences max."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("workday: LLM answer failed for %r: %s", label, exc)
        return ""


class WorkdayForm(BaseFormFiller):

    def prefetch(self) -> None:
        short = self.job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        self._cl_text = self.candidate.cover_letter_text(
            self.job.title, self.job.company, short=short, description=self.job.description
        )

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        try:
            p.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass

        # -- Navigate to application form ------------------------------------
        # Job description page → click Apply → choose Guest apply
        if not p.query_selector(_SEL_FNAME):
            try:
                apply_btn = p.wait_for_selector(_SEL_APPLY_BTN, timeout=8_000)
                if apply_btn:
                    apply_btn.click()
                    time.sleep(random.uniform(0.8, 1.5))
                    try:
                        p.wait_for_load_state("networkidle", timeout=8_000)
                    except Exception:
                        p.wait_for_timeout(2_000)
            except Exception:
                pass

            # Guest/manual apply option (avoid account creation)
            try:
                guest = p.wait_for_selector(_SEL_GUEST, timeout=6_000)
                if guest:
                    guest.click()
                    time.sleep(random.uniform(0.8, 1.5))
                    try:
                        p.wait_for_load_state("networkidle", timeout=10_000)
                    except Exception:
                        p.wait_for_timeout(2_000)
            except Exception:
                pass

        # -- Wait for first form step ----------------------------------------
        try:
            p.wait_for_selector(_SEL_FNAME, timeout=15_000)
        except Exception:
            # May already be on a step that doesn't show name (e.g. My Experience)
            log.warning("workday: first-name input not found — proceeding")

        # -- Walk all steps (Workday is always multi-step) -------------------
        self._walk_workday_steps()

    # -----------------------------------------------------------------------
    # Workday-specific step walker (replaces generic _walk_steps)
    # -----------------------------------------------------------------------

    def _walk_workday_steps(self) -> None:
        """Fill each Workday wizard step and advance until submit."""
        p = self.page
        c = self.candidate
        job = self.job

        for step_idx in range(12):
            log.info("workday: filling step %d — %s @ %s", step_idx + 1, job.title, job.company)

            # Fill everything visible on this step
            self._fill_workday_step()

            time.sleep(random.uniform(0.3, 0.6))

            # Determine if this is the final step (submit) or an intermediate Next
            next_btn = None
            try:
                btn = p.wait_for_selector(_SEL_NEXT, timeout=4_000)
                if btn and btn.is_visible():
                    btn_text = btn.inner_text().strip().lower()
                    # On the very last step Workday shows "Submit" as label
                    if any(h in btn_text for h in ("submit", "send")):
                        # Final — submit
                        if not self.dry_run:
                            btn.click()
                            log.info("workday: submitted application")
                        else:
                            log.info("workday: dry_run — skipping submit")
                        return
                    else:
                        next_btn = btn
            except Exception:
                pass

            if next_btn is None:
                # Try a plain submit button
                try:
                    submit = p.query_selector(_SEL_SUBMIT.split(",")[-1])
                    if submit and submit.is_visible():
                        if not self.dry_run:
                            submit.click()
                        return
                except Exception:
                    pass
                log.warning("workday: no next/submit button found on step %d", step_idx + 1)
                break

            # Advance to next step
            next_btn.click()
            try:
                p.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                p.wait_for_timeout(2_000)

    def _fill_workday_step(self) -> None:
        """Fill whatever fields are visible on the current Workday step."""
        p = self.page
        c = self.candidate
        job = self.job

        # -- Personal info (My Information / Contact Information step) -------
        _wd_fill(p, _SEL_FNAME, c.first_name)
        _wd_fill(p, _SEL_LNAME, c.last_name)
        _wd_fill(p, _SEL_EMAIL, c.email)
        _wd_fill(p, _SEL_PHONE, c.phone)

        # Country/location
        _wd_select(p, _SEL_COUNTRY, "Turkey", "Türkiye")
        _wd_fill(p, _SEL_CITY, "Istanbul")

        # -- Resume (My Experience step) -------------------------------------
        for _rsel in (
            f"input[{_AI}='file-upload-input-ref']",
            "input[type='file'][name*='resume' i]",
            "input[type='file']",
        ):
            try:
                el = p.query_selector(_rsel)
                if el:
                    el.set_input_files(str(c.resume_path))
                    time.sleep(0.5)
                    break
            except Exception:
                pass

        # -- Cover letter (if present on this step) --------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)
        # Workday cover letter is usually a file input labelled "Cover Letter"
        _cl_found = False
        for _lbl in ("Cover Letter", "Cover letter", "Motivation Letter"):
            try:
                loc = p.get_by_label(_lbl, exact=False)
                if loc.count() > 0:
                    is_file = loc.first.evaluate("e => e.type === 'file'")
                    if is_file:
                        pdf = self.generate_cover_letter_pdf()
                        loc.first.set_input_files(str(pdf))
                        _cl_found = True
                        break
                    else:
                        loc.first.fill(cl_text)
                        _cl_found = True
                        break
            except Exception:
                pass
        if not _cl_found:
            # textarea fallback
            for _tsel in (
                "textarea[placeholder*='cover' i]",
                f"textarea[{_AI}*='coverLetter' i]",
            ):
                try:
                    el = p.query_selector(_tsel)
                    if el:
                        el.fill(cl_text)
                        break
                except Exception:
                    pass

        # -- How did you hear about us ---------------------------------------
        _wd_select(p, _SEL_HOW_HEARD, "Job Board", "Job board",
                   "Online Job Board", "LinkedIn", "Other")
        # Also try plain select
        try:
            el = p.query_selector(_SEL_HOW_HEARD.split(",")[1].strip())
            if el:
                try:
                    el.select_option(label="Job Board")
                except Exception:
                    try:
                        el.select_option(label="LinkedIn")
                    except Exception:
                        pass
        except Exception:
            pass

        # -- Voluntary / EEO questions (Application Questionnaire step) ------
        self._fill_workday_questions()

        # -- Consent checkboxes ----------------------------------------------
        self._fill_step_checkboxes()

    def _fill_workday_questions(self) -> None:
        """Handle Workday's custom application questions section."""
        p = self.page
        c = self.candidate
        job = self.job

        for el in p.locator(
            "fieldset, div[role='group'], div[data-automation-id*='question']"
        ).all():
            try:
                label_text = ""
                try:
                    legend = el.locator("legend, label").first
                    if legend.count() > 0:
                        label_text = legend.inner_text().strip()
                except Exception:
                    pass
                if not label_text:
                    continue

                ll = label_text.lower()

                # Yes/No radio questions
                if re.search(r"authoris?ed|right to work|eligible.*work", ll):
                    ans = "Yes" if c.authorized_eu else "No"
                    _wd_radio_yes_no(p, f"fieldset:has-text('{label_text}')", ans)

                elif re.search(r"sponsor|visa", ll):
                    ans = "Yes" if c.needs_sponsorship(job.country) else "No"
                    _wd_radio_yes_no(p, f"fieldset:has-text('{label_text}')", ans)

                elif re.search(r"relocation|relocate", ll):
                    _wd_radio_yes_no(p, f"fieldset:has-text('{label_text}')", "Yes")

                elif re.search(r"18 years|legal age|of age", ll):
                    _wd_radio_yes_no(p, f"fieldset:has-text('{label_text}')", "Yes")

                elif re.search(r"veteran", ll):
                    _wd_radio_yes_no(p, f"fieldset:has-text('{label_text}')", "No")

                elif re.search(r"disability", ll):
                    _wd_radio_yes_no(p, f"fieldset:has-text('{label_text}')", "I don't wish to answer")

                # Text questions — LLM fallback
                elif re.search(r"salary|compensation|expect|desired", ll):
                    val = c.salary_for(job.country)
                    inp = el.locator("input[type='text'], input[type='number']").first
                    if inp.count() > 0:
                        try:
                            inp.fill(val)
                        except Exception:
                            pass

                else:
                    # Generic LLM answer for open text fields
                    inp = el.locator("input[type='text'], textarea").first
                    if inp.count() > 0:
                        try:
                            if not inp.input_value():
                                answer = _llm_answer(label_text, c, job)
                                if answer:
                                    inp.fill(answer)
                        except Exception:
                            pass

            except Exception:
                pass
