"""
Ashby ATS form filler.

Apply URL pattern: https://jobs.ashbyhq.com/{company}/{uuid}
React-based forms — relies on ARIA roles and labels rather than id/name attrs.
"""
from __future__ import annotations

import logging

from playwright.sync_api import Page

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)


def _fill_by_label(page: Page, label_fragment: str, value: str) -> bool:
    """Fill the input associated with a label containing label_fragment."""
    try:
        el = page.get_by_label(label_fragment, exact=False)
        if el.count() > 0:
            el.first.fill(value)
            return True
    except Exception:
        pass
    return False


def _upload_by_label(page: Page, label_fragment: str, path: str) -> bool:
    try:
        el = page.get_by_label(label_fragment, exact=False)
        if el.count() > 0:
            el.first.set_input_files(path)
            return True
    except Exception:
        pass
    # Fallback: bare file input
    try:
        fi = page.locator("input[type='file']").first
        fi.set_input_files(path)
        return True
    except Exception:
        pass
    return False


class AshbyForm(BaseFormFiller):

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        p.wait_for_load_state("networkidle", timeout=20_000)

        # -- Identity --------------------------------------------------------
        _fill_by_label(p, "First name", c.first_name)
        _fill_by_label(p, "Last name", c.last_name)
        _fill_by_label(p, "Email", c.email)
        _fill_by_label(p, "Phone", c.phone)

        # -- Location --------------------------------------------------------
        _fill_by_label(p, "Location", c.location)
        _fill_by_label(p, "City", "Istanbul")

        # -- Links -----------------------------------------------------------
        _fill_by_label(p, "LinkedIn", c.linkedin_url)
        _fill_by_label(p, "GitHub", c.github_url)
        _fill_by_label(p, "Website", c.portfolio_url)
        _fill_by_label(p, "Portfolio", c.portfolio_url)

        # -- Resume ----------------------------------------------------------
        _upload_by_label(p, "Resume", str(c.resume_path))

        # -- Cover letter ----------------------------------------------------
        short = job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        cl_text = c.cover_letter_text(job.title, job.company, short=short)
        if not _fill_by_label(p, "Cover letter", cl_text):
            _fill_by_label(p, "Additional information", cl_text)

        # -- Work authorization (Ashby uses select dropdowns or yes/no radios)
        for label_text, answer in [
            ("authorized to work", "No" if not c.authorized_eu else "Yes"),
            ("require.*sponsor", "Yes" if c.needs_sponsorship(job.country) else "No"),
            ("visa sponsor", "Yes" if c.needs_sponsorship(job.country) else "No"),
        ]:
            try:
                import re
                selects = p.locator("select").all()
                for sel in selects:
                    assoc_label = ""
                    sel_id = sel.get_attribute("id") or ""
                    if sel_id:
                        label_el = p.locator(f"label[for='{sel_id}']")
                        if label_el.count():
                            assoc_label = label_el.first.inner_text()
                    if re.search(label_text, assoc_label, re.IGNORECASE):
                        try:
                            sel.select_option(label=answer)
                        except Exception:
                            pass
            except Exception:
                pass

        # -- EEO (best-effort) -----------------------------------------------
        for gender_label in ["gender", "Gender identity"]:
            try:
                p.get_by_label(gender_label, exact=False).first.select_option(
                    label="Prefer not to say"
                )
            except Exception:
                pass

        # -- Required fields check -------------------------------------------
        for el in p.locator("[aria-required='true'], [required]").all():
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag == "input":
                    val = el.input_value()
                    el_type = el.get_attribute("type") or "text"
                    if not val and el_type != "file":
                        aria_label = el.get_attribute("aria-label") or ""
                        placeholder = el.get_attribute("placeholder") or ""
                        name = aria_label or placeholder or el.get_attribute("name") or "unknown"
                        known = {"first", "last", "email", "phone", "linkedin", "github",
                                 "website", "portfolio", "location", "city"}
                        if not any(k in name.lower() for k in known):
                            raise NeedsUserInput(f"Unknown required field: '{name}'")
            except NeedsUserInput:
                raise
            except Exception:
                pass

        # -- Submit ----------------------------------------------------------
        self.submit("button[type='submit'], button[data-button-type='submit']")
