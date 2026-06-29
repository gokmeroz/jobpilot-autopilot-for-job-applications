"""
Dover ATS form filler.

URL patterns:
  https://app.dover.com/apply/{token}
  https://jobs.dover.com/{company}/{slug}
  https://jobs.dover.com/apply/{token}

Dover is common among YC-backed and US seed-stage startups. Forms are
React-based SPAs. The apply URL may be on app.dover.com or jobs.dover.com;
both render the same form structure. Fields use data-testid or aria-label
attributes as stable selectors.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)

_APPLY_TEXTS = (
    "Apply", "Apply now", "Apply for this role", "Apply for this job",
    "Submit application", "Start application",
)

_SUBMIT_SEL = (
    "button[type='submit']:has-text('Submit'),"
    "button[type='submit']:has-text('Apply'),"
    "button[type='submit']:has-text('Submit Application'),"
    "button[data-testid='submit-btn'],"
    "button[type='submit'],"
    "input[type='submit']"
)

_CONSENT_RE = re.compile(
    r"i agree|i consent|i accept|privacy|gdpr|terms|data.*process",
    re.IGNORECASE,
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
                f"Fill a Dover job application for Goktug Mert Ozdogan "
                f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                f"Nummoria AI SaaS co-founder, open to relocation / remote).\n"
                f"Role: {job.title} at {job.company}\n\n"
                f"Question: {label}\n\n"
                f"Return ONLY the answer text, 1-3 sentences max."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("dover: LLM answer failed for %r: %s", label, exc)
        return ""


class DoverForm(BaseFormFiller):

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

        # -- Navigate to apply form (jobs.dover.com listing pages) ----------
        _form_sel = (
            "input[name='firstName'], input[name='first_name'], "
            "input[aria-label*='First name' i], input[data-testid*='first' i], "
            "input[type='email']"
        )
        if not p.query_selector(_form_sel):
            for _text in _APPLY_TEXTS:
                try:
                    btn = p.get_by_text(_text, exact=True).first
                    if btn.count() > 0 and btn.is_visible(timeout=500):
                        btn.click()
                        try:
                            p.wait_for_load_state("networkidle", timeout=8_000)
                        except Exception:
                            p.wait_for_timeout(2_000)
                        break
                except Exception:
                    continue

        try:
            p.wait_for_selector(_form_sel, timeout=15_000)
        except Exception:
            raise NeedsUserInput(
                "Dover application form did not render — "
                "job may be closed or URL changed"
            )

        # -- Identity -------------------------------------------------------
        self.fill_first(
            ["input[name='firstName']", "input[name='first_name']",
             "input[data-testid='first-name']",
             "input[aria-label*='First name' i]",
             "input[placeholder*='First name' i]"],
            c.first_name,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='lastName']", "input[name='last_name']",
             "input[data-testid='last-name']",
             "input[aria-label*='Last name' i]",
             "input[placeholder*='Last name' i]"],
            c.last_name,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='email']", "input[data-testid='email']",
             "input[type='email']", "input[aria-label*='Email' i]"],
            c.email,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='phone']", "input[name='phoneNumber']",
             "input[data-testid='phone']",
             "input[type='tel']", "input[aria-label*='Phone' i]",
             "input[placeholder*='phone' i]"],
            c.phone,
        )

        # -- Location -------------------------------------------------------
        self.fill_first(
            ["input[name='location']", "input[name='city']",
             "input[data-testid='location']",
             "input[aria-label*='location' i]",
             "input[placeholder*='location' i]",
             "input[placeholder*='city' i]"],
            "Istanbul, Turkey",
        )

        # -- Resume ---------------------------------------------------------
        _cv_uploaded = (
            self.upload("input[name='resume']", c.resume_path)
            or self.upload("input[data-testid='resume-upload']", c.resume_path)
            or self.upload("input[type='file'][name*='resume' i]", c.resume_path)
            or self.upload("input[type='file'][name*='cv' i]", c.resume_path)
            or self.upload("input[type='file']", c.resume_path)
        )
        if not _cv_uploaded:
            log.warning("dover: could not upload resume")

        # -- Cover letter ---------------------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        _cl_file_uploaded = False
        for _sel in ("input[type='file'][name*='cover' i]",):
            try:
                el = p.query_selector(_sel)
                if el:
                    pdf = self.generate_cover_letter_pdf()
                    el.set_input_files(str(pdf))
                    _cl_file_uploaded = True
                    break
            except Exception:
                pass

        if not _cl_file_uploaded:
            self.fill_first(
                [
                    "textarea[name='coverLetter']",
                    "textarea[name='cover_letter']",
                    "textarea[data-testid='cover-letter']",
                    "textarea[aria-label*='cover letter' i]",
                    "textarea[placeholder*='cover letter' i]",
                    "textarea[placeholder*='motivation' i]",
                    "textarea[placeholder*='tell us' i]",
                ],
                cl_text,
            )

        # -- Social links --------------------------------------------------
        self.fill_first(
            ["input[name='linkedinUrl']", "input[name='linkedin_url']",
             "input[name='linkedin']",
             "input[data-testid='linkedin']",
             "input[aria-label*='LinkedIn' i]",
             "input[placeholder*='LinkedIn' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='githubUrl']", "input[name='github']",
             "input[aria-label*='GitHub' i]",
             "input[placeholder*='GitHub' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[name='website']", "input[name='portfolioUrl']",
             "input[aria-label*='website' i]",
             "input[placeholder*='website' i]",
             "input[placeholder*='portfolio' i]"],
            c.portfolio_url,
        )

        # -- Salary --------------------------------------------------------
        val = c.salary_for(job.country)
        self.fill_first(
            ["input[name='salary']", "input[name='desiredSalary']",
             "input[name='expectedSalary']",
             "input[aria-label*='salary' i]",
             "input[placeholder*='salary' i]"],
            val,
        )

        # -- Custom / screening questions ----------------------------------
        for el in p.locator(
            "input[required]:not([type='file']):not([type='checkbox'])"
            ":not([type='radio']):not([type='hidden']),"
            "textarea[required], select[required],"
            "input[aria-required='true']:not([type='file']):not([type='checkbox']),"
            "textarea[aria-required='true'], select[aria-required='true']"
        ).all():
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue
                if tag != "select":
                    try:
                        if el.input_value():
                            continue
                    except Exception:
                        continue

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

                _KNOWN = {
                    "firstname", "lastname", "first_name", "last_name",
                    "email", "phone", "resume", "cover", "linkedin", "github",
                    "website", "portfolio", "location", "salary",
                }
                if any(k in label_text.lower().replace(" ", "") for k in _KNOWN):
                    continue

                ll = label_text.lower()
                answered = False

                if re.search(r"sponsor|visa", ll):
                    ans = "Yes" if c.needs_sponsorship(job.country) else "No"
                    answered = self._try(el, tag, ans)
                elif re.search(r"authoris?ed|right to work|eligible", ll):
                    ans = "Yes" if c.authorized_eu else "No"
                    answered = self._try(el, tag, ans)
                elif re.search(r"relocation|relocat", ll):
                    answered = self._try(el, tag, "Yes")
                elif re.search(r"notice period|start date|when can you start", ll):
                    answered = self._try(el, tag, "Immediately")
                elif re.search(r"how did you hear|referral|where did you", ll):
                    answered = self._try(el, tag, "Job board")
                elif tag in ("input", "textarea"):
                    answer = _llm_answer(label_text, c, job)
                    if answer:
                        try:
                            el.fill(answer)
                            answered = True
                        except Exception:
                            pass
                elif tag == "select":
                    try:
                        opts = el.evaluate(
                            "e => Array.from(e.options).map(o => ({v: o.value, t: o.text.trim()}))"
                        )
                        for opt in opts:
                            if opt["v"] and opt["t"].lower() not in {"", "select...", "please select"}:
                                el.select_option(value=opt["v"])
                                answered = True
                                break
                    except Exception:
                        pass

                if not answered:
                    log.warning("dover: unanswered field: %r", label_text)

            except Exception as exc:
                log.warning("dover: field error: %s", exc)

        # -- GDPR / consent ------------------------------------------------
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

        # -- Walk steps + submit -------------------------------------------
        self._walk_steps()
        self.submit(_SUBMIT_SEL)

    # -----------------------------------------------------------------------

    def _try(self, el, tag: str, value: str) -> bool:
        try:
            if tag == "select":
                el.select_option(label=value)
            else:
                el.fill(value)
            return True
        except Exception:
            return False
