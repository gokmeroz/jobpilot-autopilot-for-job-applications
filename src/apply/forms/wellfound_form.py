"""
Wellfound (formerly AngelList Talent) form filler.

URL patterns:
  https://wellfound.com/jobs/{id}
  https://wellfound.com/company/{slug}/jobs/{id}
  https://angel.co/company/{slug}/jobs/{id}  (legacy, redirects to wellfound.com)

Wellfound surfaces an "Apply" button on the job listing that opens an inline
modal (or navigates to /apply). The modal form is straightforward: name, email,
phone, resume, cover note, LinkedIn, and optional custom questions.

Some Wellfound postings are "Easy Apply" (modal) while others redirect to the
company's own ATS. The filler handles the native Wellfound apply modal only;
external redirects are caught and surfaced as NeedsUserInput.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)

_APPLY_TEXTS = (
    "Apply now", "Apply", "Easy Apply", "Apply to this job",
    "Apply for this role", "Apply for this position",
)

_SUBMIT_SEL = (
    "button[type='submit']:has-text('Submit application'),"
    "button[type='submit']:has-text('Apply'),"
    "button[type='submit']:has-text('Send application'),"
    "button[type='submit'],"
    "button:has-text('Submit application'),"
    "button:has-text('Apply now')"
)

_CONSENT_RE = re.compile(
    r"i agree|i consent|i accept|privacy|gdpr|terms|data.*process",
    re.IGNORECASE,
)

_EXTERNAL_HINTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "bamboohr.com", "workday", "myworkdayjobs",
)


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
                f"Fill a Wellfound job application for Goktug Mert Ozdogan "
                f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                f"Nummoria AI SaaS co-founder, open to relocation).\n"
                f"Role: {job.title} at {job.company}\n\n"
                f"Question: {label}\n\n"
                f"Return ONLY the answer text, 1-3 sentences max."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("wellfound: LLM answer failed for %r: %s", label, exc)
        return ""


class WellfoundForm(BaseFormFiller):

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

        # -- Detect if we've been redirected to an external ATS -------------
        current_url = p.url.lower()
        if any(hint in current_url for hint in _EXTERNAL_HINTS):
            raise NeedsUserInput(
                f"Wellfound redirected to external ATS: {p.url} — "
                "apply via the appropriate filler or manually"
            )

        # -- Click Apply button (opens modal or navigates to apply page) ----
        modal_or_form_sel = (
            "input[name='name'], input[placeholder*='Full name' i], "
            "input[placeholder*='First name' i], input[type='email']"
        )
        if not p.query_selector(modal_or_form_sel):
            _clicked = False
            for _text in _APPLY_TEXTS:
                try:
                    btn = p.get_by_text(_text, exact=True).first
                    if btn.count() > 0 and btn.is_visible(timeout=500):
                        btn.click()
                        time.sleep(random.uniform(0.6, 1.2))
                        try:
                            p.wait_for_load_state("networkidle", timeout=6_000)
                        except Exception:
                            p.wait_for_timeout(1_500)
                        _clicked = True
                        break
                except Exception:
                    continue

            if not _clicked:
                # Try by role
                try:
                    btn = p.get_by_role("button", name=re.compile(r"apply", re.I)).first
                    if btn.count() > 0 and btn.is_visible(timeout=3_000):
                        btn.click()
                        time.sleep(1.0)
                except Exception:
                    pass

        # -- Detect external redirect after clicking Apply -------------------
        time.sleep(0.5)
        current_url = p.url.lower()
        if any(hint in current_url for hint in _EXTERNAL_HINTS):
            raise NeedsUserInput(
                f"Wellfound 'Apply' redirected to external ATS: {p.url}"
            )

        # -- Wait for form fields -------------------------------------------
        try:
            p.wait_for_selector(
                "input[type='email'], input[placeholder*='name' i], "
                "input[name='first_name'], input[name='name']",
                timeout=12_000,
            )
        except Exception:
            raise NeedsUserInput(
                "Wellfound application form did not render — "
                "job may be closed, require login, or redirected"
            )

        # -- Name -----------------------------------------------------------
        # Wellfound may use a single "Full name" field or split first/last
        if p.query_selector("input[name='first_name'], input[placeholder*='First name' i]"):
            self.fill_first(
                ["input[name='first_name']",
                 "input[placeholder*='First name' i]"],
                c.first_name,
            )
            self.fill_first(
                ["input[name='last_name']",
                 "input[placeholder*='Last name' i]"],
                c.last_name,
            )
        else:
            self.fill_first(
                ["input[name='name']",
                 "input[placeholder*='Full name' i]",
                 "input[placeholder*='Your name' i]"],
                c.full_name,
            )

        time.sleep(random.uniform(0.2, 0.4))

        # -- Email & phone --------------------------------------------------
        self.fill_first(
            ["input[name='email']", "input[type='email']"],
            c.email,
        )
        self.fill_first(
            ["input[name='phone']", "input[type='tel']",
             "input[placeholder*='phone' i]"],
            c.phone,
        )

        # -- Location -------------------------------------------------------
        self.fill_first(
            ["input[name='location']", "input[placeholder*='location' i]",
             "input[placeholder*='city' i]"],
            "Istanbul, Turkey",
        )

        # -- Resume upload --------------------------------------------------
        _cv_uploaded = (
            self.upload("input[type='file'][name*='resume' i]", c.resume_path)
            or self.upload("input[type='file'][name*='cv' i]", c.resume_path)
            or self.upload("input[type='file']", c.resume_path)
        )
        if not _cv_uploaded:
            log.warning("wellfound: could not upload resume")

        # -- Cover note / introduction --------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)
        self.fill_first(
            [
                "textarea[name='note']",
                "textarea[name='cover_note']",
                "textarea[name='cover_letter']",
                "textarea[name='introduction']",
                "textarea[placeholder*='note' i]",
                "textarea[placeholder*='cover' i]",
                "textarea[placeholder*='introduction' i]",
                "textarea[placeholder*='tell us' i]",
                "textarea[placeholder*='motivation' i]",
                "textarea",
            ],
            cl_text,
        )

        # -- LinkedIn / GitHub / portfolio ----------------------------------
        self.fill_first(
            ["input[name='linkedin_url']", "input[placeholder*='LinkedIn' i]",
             "input[name*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='github_url']", "input[placeholder*='GitHub' i]",
             "input[name*='github' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[name='website']", "input[placeholder*='portfolio' i]",
             "input[placeholder*='website' i]"],
            c.portfolio_url,
        )

        # -- Salary expectation (often asked on Wellfound) ------------------
        val = c.salary_for(job.country)
        self.fill_first(
            ["input[name*='salary' i]", "input[placeholder*='salary' i]",
             "input[placeholder*='compensation' i]"],
            val,
        )

        # -- Custom questions -----------------------------------------------
        for el in p.locator(
            "input[required], textarea[required], select[required],"
            "input[aria-required='true'], textarea[aria-required='true'],"
            "select[aria-required='true']"
        ).all():
            try:
                el_type = (el.get_attribute("type") or "text").lower()
                if el_type in ("file", "hidden", "checkbox", "radio"):
                    continue
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue

                # Skip if already filled
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
                    if lbl.count() > 0:
                        label_text = lbl.first.inner_text().strip()
                if not label_text:
                    label_text = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("placeholder")
                        or el.get_attribute("name")
                        or ""
                    )
                if not label_text:
                    continue

                ll = label_text.lower()
                answered = False

                if re.search(r"sponsor|visa", ll):
                    ans = "Yes" if c.needs_sponsorship(job.country) else "No"
                    answered = self._try_fill(el, tag, ans)
                elif re.search(r"authoris?ed|right to work|eligible", ll):
                    ans = "Yes" if c.authorized_eu else "No"
                    answered = self._try_fill(el, tag, ans)
                elif re.search(r"relocation|relocate", ll):
                    answered = self._try_fill(el, tag, "Yes")
                elif re.search(r"how did you hear|referral|where did you", ll):
                    answered = self._try_fill(el, tag, "Job board")
                elif re.search(r"notice period|when can you start", ll):
                    answered = self._try_fill(el, tag, "Immediately")
                elif tag in ("input", "textarea"):
                    answer = _llm_answer(label_text, c, job)
                    if answer:
                        try:
                            el.fill(answer)
                            answered = True
                        except Exception:
                            pass

                if not answered:
                    log.warning("wellfound: unanswered required field: %r", label_text)

            except Exception as exc:
                log.warning("wellfound: error on field: %s", exc)

        # -- Consent checkboxes ---------------------------------------------
        for cb in p.locator("input[type='checkbox']").all():
            try:
                if cb.is_checked():
                    continue
                label_text = ""
                cb_id = cb.get_attribute("id") or ""
                if cb_id:
                    lbl = p.locator(f"label[for='{cb_id}']")
                    if lbl.count() > 0:
                        label_text = lbl.first.inner_text()
                if not label_text:
                    try:
                        label_text = cb.evaluate(
                            "el => el.closest('label')?.innerText"
                            " || el.parentElement?.innerText || ''"
                        )
                    except Exception:
                        pass
                is_required = cb.get_attribute("required") is not None
                if is_required or _CONSENT_RE.search(label_text):
                    try:
                        cb.check()
                    except Exception:
                        cb.click()
            except Exception:
                pass

        # -- Walk paginated steps then submit --------------------------------
        self._walk_steps()
        self.submit(_SUBMIT_SEL)

    # -----------------------------------------------------------------------

    def _try_fill(self, el, tag: str, value: str) -> bool:
        try:
            if tag == "select":
                el.select_option(label=value)
            else:
                el.fill(value)
            return True
        except Exception:
            return False
