"""
Lever ATS form filler.

Apply URL pattern: https://jobs.lever.co/{company}/{uuid}/apply
"""
from __future__ import annotations

import logging

from src.apply.base import BaseFormFiller, NeedsUserInput

log = logging.getLogger(__name__)


class LeverForm(BaseFormFiller):

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        p.wait_for_load_state("networkidle", timeout=20_000)

        # -- Identity --------------------------------------------------------
        # Lever uses a single "Full name" field, not split first/last
        self.fill_first(
            ["input[name='name']", "input[id='name']"],
            c.full_name,
        )
        self.fill_first(
            ["input[name='email']", "input[id='email']"],
            c.email,
        )
        self.fill_first(
            ["input[name='phone']", "input[id='phone']"],
            c.phone,
        )
        # Current company / org
        self.fill_first(
            ["input[name='org']", "input[id='org']"],
            "Nummoria",
        )

        # -- Links -----------------------------------------------------------
        self.fill_first(
            ["input[name='urls[LinkedIn]']", "input[placeholder*='LinkedIn' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='urls[GitHub]']", "input[placeholder*='GitHub' i]"],
            c.github_url,
        )
        self.fill_first(
            [
                "input[name='urls[Portfolio]']",
                "input[name='urls[Other]']",
                "input[placeholder*='portfolio' i]",
                "input[placeholder*='website' i]",
            ],
            c.portfolio_url,
        )

        # -- Resume ----------------------------------------------------------
        uploaded = self.upload("input[type='file']", c.resume_path)
        if not uploaded:
            log.warning("lever: could not upload resume for %s @ %s", job.title, job.company)

        # -- Cover letter ----------------------------------------------------
        short = job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        cl_text = c.cover_letter_text(job.title, job.company, short=short, description=job.description)
        self.fill_first(
            [
                "textarea[name='comments']",
                "textarea[placeholder*='cover letter' i]",
                "textarea[placeholder*='additional' i]",
            ],
            cl_text,
        )

        # -- Custom questions -------------------------------------------------
        # Lever "Additional Information" free-text fields are handled above.
        # Any other required field we can't answer → flag for manual review.
        for el in p.query_selector_all("input[required], textarea[required], select[required]"):
            val = ""
            try:
                el_type = el.get_attribute("type") or "text"
                if el_type != "file":
                    val = el.input_value()
            except Exception:
                pass
            if not val:
                name = el.get_attribute("name") or el.get_attribute("id") or "unknown"
                known = {"name", "email", "phone", "org", "urls", "comments"}
                if not any(k in name.lower() for k in known):
                    raise NeedsUserInput(f"Unknown required field: '{name}'")

        # -- Walk through any paginated steps before submit ------------------
        self._walk_steps()

        # -- Submit ----------------------------------------------------------
        self.submit("button[type='submit'], button.template-btn-submit")
