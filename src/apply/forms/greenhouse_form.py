"""
Greenhouse ATS form filler.

Standard form: https://boards.greenhouse.io/{token}/jobs/{id}
Company-embedded variant: apply URL redirects here after JS navigation.
"""
from __future__ import annotations

import logging

from src.apply.base import BaseFormFiller, NeedsUserInput

log = logging.getLogger(__name__)


class GreenhouseForm(BaseFormFiller):

    def prefetch(self) -> None:
        short = self.job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        self._cl_text = self.candidate.cover_letter_text(
            self.job.title, self.job.company, short=short, description=self.job.description
        )

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        p.wait_for_load_state("networkidle", timeout=20_000)

        # -- Identity --------------------------------------------------------
        self.fill("#first_name", c.first_name)
        self.fill("#last_name", c.last_name)
        self.fill("#email", c.email)
        self.fill("#phone", c.phone)

        # -- Resume ----------------------------------------------------------
        uploaded = self.upload("input#resume", c.resume_path)
        if not uploaded:
            # Some Greenhouse boards use a different file input
            self.upload("input[name='resume']", c.resume_path)

        # -- Cover letter ----------------------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        self.fill_first([
            "#cover_letter_text",
            "textarea[name='job_application[cover_letter]']",
            "textarea[aria-label*='cover letter' i]",
        ], cl_text)

        # -- Links -----------------------------------------------------------
        self.fill_first(
            ["#job_application_linkedin_url", "input[id*='linkedin' i]", "input[name*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["#job_application_github_url", "input[id*='github' i]", "input[name*='github' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[id*='website' i]", "input[id*='portfolio' i]", "input[name*='website' i]"],
            c.portfolio_url,
        )

        # -- Work authorization ----------------------------------------------
        # "Are you authorized to work in [country]?"
        # Greenhouse renders these as <select> with options Yes/No
        for sel in p.query_selector_all("select"):
            label_el = p.query_selector(f"label[for='{sel.get_attribute('id')}']")
            label_text = (label_el.inner_text() if label_el else "").lower()

            if "authorized to work" in label_text or "right to work" in label_text:
                if "eu" in label_text or any(c in label_text for c in ["germany", "netherlands", "ireland"]):
                    sel.select_option(label="No") if not self.candidate.authorized_eu else sel.select_option(label="Yes")
                elif "uk" in label_text or "united kingdom" in label_text:
                    sel.select_option(label="No") if not self.candidate.authorized_uk else sel.select_option(label="Yes")
                elif "us" in label_text or "united states" in label_text:
                    sel.select_option(label="No") if not self.candidate.authorized_us else sel.select_option(label="Yes")

            elif "sponsor" in label_text or "visa" in label_text:
                # "Will you now or in future require sponsorship?"
                needs = self.candidate.needs_sponsorship(job.country)
                sel.select_option(label="Yes" if needs else "No")

        # -- EEO (optional, best-effort) -------------------------------------
        for sel in p.query_selector_all("select"):
            label_el = p.query_selector(f"label[for='{sel.get_attribute('id')}']")
            label_text = (label_el.inner_text() if label_el else "").lower()

            if "gender" in label_text:
                try:
                    sel.select_option(label="Prefer not to say")
                except Exception:
                    pass
            elif "pronoun" in label_text:
                for opt in ["He/Him", "He/his", "he/him", "he/his"]:
                    try:
                        sel.select_option(label=opt)
                        break
                    except Exception:
                        pass
            elif "race" in label_text or "ethnicity" in label_text or "background" in label_text:
                # Prefer decline; only fall back to "Other" if no decline option exists
                declined = False
                for opt in ["Prefer not to say", "I don't wish to answer",
                            "I do not wish to answer", "Decline to state",
                            "Decline to identify", "Choose not to disclose"]:
                    try:
                        sel.select_option(label=opt)
                        declined = True
                        break
                    except Exception:
                        pass
                if not declined:
                    for opt in ["Other", "Other (please specify)"]:
                        try:
                            sel.select_option(label=opt)
                            break
                        except Exception:
                            pass
            elif "veteran" in label_text:
                try:
                    sel.select_option(label="I am not a protected veteran")
                except Exception:
                    pass
            elif "disability" in label_text:
                try:
                    sel.select_option(label="I don't wish to answer")
                except Exception:
                    sel.select_option(label="I do not wish to answer")

        # -- Unknown required fields → flag for manual review ----------------
        _known = {
            "first_name", "last_name", "email", "phone",
            "resume", "cover_letter", "linkedin", "github",
            "website", "portfolio", "twitter",
        }
        for el in p.query_selector_all("input[required], textarea[required], select[required]"):
            el_type = el.get_attribute("type") or "text"
            if el_type == "file":
                continue
            val = el.input_value()
            if val:
                continue  # already filled

            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            identifier = el_id or el_name

            if any(k in identifier.lower() for k in _known):
                continue

            # Resolve a human-readable label for the error message
            label_text = ""
            if el_id:
                label_el = p.query_selector(f"label[for='{el_id}']")
                if label_el:
                    label_text = label_el.inner_text()
            if not label_text:
                label_text = (
                    el.get_attribute("aria-label")
                    or el.get_attribute("placeholder")
                    or identifier
                    or "unknown"
                )
            raise NeedsUserInput(f"Unknown required field: '{label_text}'")

        # -- Submit ----------------------------------------------------------
        self.submit("#submit_app, button[type='submit']")
