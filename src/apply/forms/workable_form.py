"""
Workable ATS form filler.

Apply URL pattern: https://apply.workable.com/{company}/j/{id}/apply
"""
from __future__ import annotations

import logging

from src.apply.base import BaseFormFiller, NeedsUserInput

log = logging.getLogger(__name__)


class WorkableForm(BaseFormFiller):

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        p.wait_for_load_state("networkidle", timeout=20_000)

        # -- Identity --------------------------------------------------------
        self.fill_first(
            ["input[name='firstname']", "input[aria-label*='First name' i]", "input[id*='firstname' i]"],
            c.first_name,
        )
        self.fill_first(
            ["input[name='lastname']", "input[aria-label*='Last name' i]", "input[id*='lastname' i]"],
            c.last_name,
        )
        self.fill_first(
            ["input[name='email']", "input[type='email']"],
            c.email,
        )
        self.fill_first(
            ["input[name='phone']", "input[type='tel']"],
            c.phone,
        )

        # -- Location --------------------------------------------------------
        self.fill_first(
            ["input[name='address']", "input[aria-label*='location' i]", "input[placeholder*='city' i]"],
            c.location,
        )

        # -- Resume ----------------------------------------------------------
        uploaded = self.upload("input[type='file'][name*='resume' i], input[type='file'][name*='cv' i]", c.resume_path)
        if not uploaded:
            self.upload("input[type='file']", c.resume_path)

        # -- Cover letter ----------------------------------------------------
        short = job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        cl_text = c.cover_letter_text(job.title, job.company, short=short)
        self.fill_first(
            [
                "textarea[name='cover_letter']",
                "textarea[aria-label*='cover letter' i]",
                "textarea[placeholder*='cover letter' i]",
            ],
            cl_text,
        )

        # -- Links -----------------------------------------------------------
        self.fill_first(
            ["input[name='linkedin']", "input[aria-label*='LinkedIn' i]", "input[placeholder*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='github']", "input[aria-label*='GitHub' i]", "input[placeholder*='github' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[name='website']", "input[aria-label*='website' i]", "input[placeholder*='portfolio' i]"],
            c.portfolio_url,
        )

        # -- Work authorization ----------------------------------------------
        # Workable uses yes/no radio buttons for work auth questions
        for el in p.query_selector_all("fieldset"):
            legend = el.query_selector("legend")
            legend_text = (legend.inner_text() if legend else "").lower()

            if "authorized" in legend_text or "right to work" in legend_text:
                answer = "yes" if c.authorized_eu else "no"
                radio = el.query_selector(f"input[type='radio'][value='{answer}']")
                if not radio:
                    radio = el.query_selector(f"label:has-text('{answer.title()}')")
                if radio:
                    try:
                        radio.click()
                    except Exception:
                        pass

            elif "sponsor" in legend_text or "visa" in legend_text:
                needs = c.needs_sponsorship(job.country)
                answer = "yes" if needs else "no"
                radio = el.query_selector(f"input[type='radio'][value='{answer}']")
                if not radio:
                    radio = el.query_selector(f"label:has-text('{answer.title()}')")
                if radio:
                    try:
                        radio.click()
                    except Exception:
                        pass

        # -- Required field check --------------------------------------------
        for el in p.query_selector_all("input[required], textarea[required], select[required]"):
            el_type = el.get_attribute("type") or "text"
            if el_type == "file":
                continue
            try:
                val = el.input_value()
            except Exception:
                continue
            if not val:
                name = (
                    el.get_attribute("aria-label")
                    or el.get_attribute("name")
                    or el.get_attribute("placeholder")
                    or "unknown"
                )
                known = {"firstname", "lastname", "email", "phone", "address",
                         "cover", "linkedin", "github", "website"}
                if not any(k in name.lower() for k in known):
                    raise NeedsUserInput(f"Unknown required field: '{name}'")

        # -- Submit ----------------------------------------------------------
        self.submit("button[type='submit'][data-ui='save-button'], button[type='submit']")
