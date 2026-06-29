"""
Breezy HR ATS form filler.

URL patterns:
  https://{company}.breezy.hr/p/{id}/apply
  https://breezy.hr/p/{id}/apply
  https://{company}.breezy.hr/p/{id}  ← listing; need to click Apply

Breezy HR is used by 1,000+ SMBs globally, especially US remote-first
companies. Forms are standard HTML with clean `name` attributes. The
application page is a single-page form with optional custom questions.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)

_APPLY_TEXTS = (
    "Apply Now", "Apply now", "Apply", "Apply for this position",
    "Apply for this job", "Submit application",
)

_SUBMIT_SEL = (
    "button[type='submit']:has-text('Submit Application'),"
    "button[type='submit']:has-text('Submit application'),"
    "button[type='submit']:has-text('Apply'),"
    "button[type='submit']:has-text('Submit'),"
    "#submit-btn,"
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
                f"Fill a Breezy HR job application for Goktug Mert Ozdogan "
                f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                f"Nummoria AI SaaS co-founder, open to relocation / remote).\n"
                f"Role: {job.title} at {job.company}\n\n"
                f"Question: {label}\n\n"
                f"Return ONLY the answer text, 1-3 sentences max."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("breezy: LLM answer failed for %r: %s", label, exc)
        return ""


class BreezyForm(BaseFormFiller):

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

        # -- Navigate to apply form (listing page → /apply) ----------------
        _form_sel = (
            "input[name='name'], input[name='first_name'], "
            "input[id='name'], input[type='email']"
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
            p.wait_for_selector(_form_sel, timeout=12_000)
        except Exception:
            raise NeedsUserInput(
                "Breezy HR application form did not render — "
                "job may be closed or URL changed"
            )

        # -- Identity -------------------------------------------------------
        # Breezy uses a single "Name" field in older configs; split in newer ones.
        if p.query_selector("input[name='first_name'], input[id='first_name']"):
            self.fill_first(
                ["input[name='first_name']", "input[id='first_name']",
                 "input[placeholder*='First name' i]"],
                c.first_name,
            )
            time.sleep(random.uniform(0.2, 0.4))
            self.fill_first(
                ["input[name='last_name']", "input[id='last_name']",
                 "input[placeholder*='Last name' i]"],
                c.last_name,
            )
        else:
            self.fill_first(
                ["input[name='name']", "input[id='name']",
                 "input[placeholder*='Full name' i]",
                 "input[placeholder*='Your name' i]",
                 "input[aria-label*='Name' i]"],
                c.full_name,
            )

        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='email_address']", "input[name='email']",
             "input[id='email_address']", "input[id='email']",
             "input[type='email']"],
            c.email,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='phone']", "input[id='phone']",
             "input[name='phone_number']",
             "input[type='tel']",
             "input[placeholder*='phone' i]"],
            c.phone,
        )

        # -- Location -------------------------------------------------------
        self.fill_first(
            ["input[name='address']", "input[id='address']",
             "input[name='location']", "input[id='location']",
             "input[name='city']", "input[id='city']",
             "input[placeholder*='address' i]",
             "input[placeholder*='location' i]",
             "input[placeholder*='city' i]"],
            "Istanbul, Turkey",
        )

        # -- Summary / headline (Breezy has an optional summary field) ------
        self.fill_first(
            ["input[name='summary']", "input[id='summary']",
             "input[placeholder*='headline' i]",
             "input[placeholder*='summary' i]"],
            f"Software Engineer | Node.js · React · Python · AWS | {job.title}",
        )

        # -- Resume ---------------------------------------------------------
        _cv_uploaded = (
            self.upload("input[name='resume']", c.resume_path)
            or self.upload("input[id='resume']", c.resume_path)
            or self.upload("input[type='file'][name*='resume' i]", c.resume_path)
            or self.upload("input[type='file'][name*='cv' i]", c.resume_path)
            or self.upload("input[type='file']", c.resume_path)
        )
        if not _cv_uploaded:
            log.warning("breezy: could not upload resume")

        # -- Cover letter ---------------------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        _cl_file_uploaded = False
        for _sel in (
            "input[name='cover_letter']",
            "input[type='file'][name*='cover' i]",
        ):
            try:
                el = p.query_selector(_sel)
                if el:
                    input_type = (el.get_attribute("type") or "text").lower()
                    if input_type == "file":
                        pdf = self.generate_cover_letter_pdf()
                        el.set_input_files(str(pdf))
                        _cl_file_uploaded = True
                        break
            except Exception:
                pass

        if not _cl_file_uploaded:
            self.fill_first(
                [
                    "textarea[name='cover_letter']",
                    "textarea[id='cover_letter']",
                    "textarea[name='coverLetter']",
                    "textarea[placeholder*='cover letter' i]",
                    "textarea[placeholder*='motivation' i]",
                    "textarea[placeholder*='tell us' i]",
                    "textarea[aria-label*='cover letter' i]",
                ],
                cl_text,
            )

        # -- Social links --------------------------------------------------
        self.fill_first(
            ["input[name='linkedin']", "input[id='linkedin']",
             "input[name='linkedin_url']",
             "input[placeholder*='LinkedIn' i]",
             "input[aria-label*='LinkedIn' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='github']", "input[id='github']",
             "input[placeholder*='GitHub' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[name='website']", "input[id='website']",
             "input[name='portfolio']",
             "input[placeholder*='website' i]",
             "input[placeholder*='portfolio' i]"],
            c.portfolio_url,
        )

        # -- "How did you hear" (Breezy has a dropdown for this) -----------
        for _sel in (
            "select[name='source']", "select[id='source']",
            "select[name='how_did_you_hear']",
            "select[id*='source' i]",
        ):
            try:
                el = p.query_selector(_sel)
                if el:
                    for opt in ("Job Board", "LinkedIn", "Online", "Other"):
                        try:
                            el.select_option(label=opt)
                            break
                        except Exception:
                            pass
                    break
            except Exception:
                pass

        # -- Custom questions (Breezy renders them after the standard block) -
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
                    "name", "email", "phone", "address", "location", "city",
                    "resume", "cover", "linkedin", "github", "website",
                    "portfolio", "summary", "source",
                }
                if any(k in label_text.lower() for k in _KNOWN):
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
                elif re.search(r"salary|compensation", ll):
                    answered = self._try(el, tag, c.salary_for(job.country))
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
                            if opt["v"] and opt["t"].lower() not in {"", "select...", "please select", "choose one"}:
                                el.select_option(value=opt["v"])
                                answered = True
                                break
                    except Exception:
                        pass

                if not answered:
                    log.warning("breezy: unanswered field: %r", label_text)

            except Exception as exc:
                log.warning("breezy: field error: %s", exc)

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
