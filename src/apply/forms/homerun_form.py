"""
Homerun ATS form filler.

URL patterns:
  https://{company}.homerun.co/{job-slug}
  https://{company}.homerun.co/jobs/{job-slug}
  https://jobs.homerun.co/{company}/{slug}

Homerun is the dominant ATS for Amsterdam tech startups and Dutch scale-ups.
The job listing page embeds the application form directly (no navigation to a
separate /apply URL). Forms are React-based but use standard HTML inputs.
GDPR consent checkbox is always present (EU company).
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)

_SUBMIT_SEL = (
    "button[type='submit']:has-text('Send application'),"
    "button[type='submit']:has-text('Apply'),"
    "button[type='submit']:has-text('Submit'),"
    "button[type='submit']:has-text('Submit application'),"
    "button[type='submit'],"
    "input[type='submit']"
)

_CONSENT_RE = re.compile(
    r"i agree|i consent|i accept|privacy|gdpr|terms|data.*process|verwerk",
    re.IGNORECASE,
)

# Homerun sometimes renders the form inside a modal triggered by "Apply" CTA
_APPLY_TEXTS = (
    "Apply now", "Apply", "Apply for this job", "Solliciteer",  # Dutch
    "Solliciteer nu", "Send application",
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
                f"Fill a Homerun job application for Goktug Mert Ozdogan "
                f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                f"Nummoria AI SaaS co-founder, open to relocation to Netherlands/EU).\n"
                f"Role: {job.title} at {job.company}\n\n"
                f"Question: {label}\n\n"
                f"Return ONLY the answer text, 1-3 sentences max."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("homerun: LLM answer failed for %r: %s", label, exc)
        return ""


class HomerunForm(BaseFormFiller):

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

        # -- Homerun may require clicking Apply to reveal the form ----------
        _form_sel = (
            "input[name='first_name'], input[name='firstName'], "
            "input[name='name'], input[type='email'], "
            "input[placeholder*='name' i], input[placeholder*='naam' i]"  # Dutch
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
                "Homerun application form did not render — "
                "job may be closed or require login"
            )

        # -- Identity -------------------------------------------------------
        # Homerun uses split first/last or single full-name depending on config
        if p.query_selector("input[name='first_name'], input[name='firstName']"):
            self.fill_first(
                ["input[name='first_name']", "input[name='firstName']",
                 "input[id*='first_name' i]", "input[id*='firstName' i]",
                 "input[placeholder*='First name' i]", "input[placeholder*='Voornaam' i]"],
                c.first_name,
            )
            time.sleep(random.uniform(0.2, 0.4))
            self.fill_first(
                ["input[name='last_name']", "input[name='lastName']",
                 "input[id*='last_name' i]", "input[id*='lastName' i]",
                 "input[placeholder*='Last name' i]", "input[placeholder*='Achternaam' i]"],
                c.last_name,
            )
        else:
            self.fill_first(
                ["input[name='name']", "input[id='name']",
                 "input[placeholder*='Full name' i]",
                 "input[placeholder*='Your name' i]",
                 "input[placeholder*='Naam' i]"],
                c.full_name,
            )

        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='email']", "input[type='email']", "input[id*='email' i]"],
            c.email,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='phone']", "input[name='phone_number']",
             "input[type='tel']", "input[placeholder*='phone' i]",
             "input[placeholder*='telefoon' i]"],  # Dutch
            c.phone,
        )

        # -- Location -------------------------------------------------------
        self.fill_first(
            ["input[name='location']", "input[name='city']",
             "input[placeholder*='location' i]", "input[placeholder*='city' i]",
             "input[placeholder*='stad' i]"],  # Dutch
            "Istanbul, Turkey",
        )

        # -- Resume ---------------------------------------------------------
        _cv_uploaded = (
            self.upload("input[name='resume']", c.resume_path)
            or self.upload("input[name='cv']", c.resume_path)
            or self.upload("input[type='file'][name*='resume' i]", c.resume_path)
            or self.upload("input[type='file'][name*='cv' i]", c.resume_path)
            or self.upload("input[type='file']", c.resume_path)
        )
        if not _cv_uploaded:
            log.warning("homerun: could not upload resume")

        # -- Motivation letter / cover letter --------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        _cl_file_uploaded = False
        for _sel in (
            "input[type='file'][name*='cover' i]",
            "input[type='file'][name*='motivation' i]",
            "input[type='file'][name*='letter' i]",
        ):
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
                    "textarea[name='motivation']",
                    "textarea[name='cover_letter']",
                    "textarea[name='motivationLetter']",
                    "textarea[name='motivation_letter']",
                    "textarea[id*='motivation' i]",
                    "textarea[id*='cover_letter' i]",
                    "textarea[placeholder*='motivation' i]",
                    "textarea[placeholder*='motivatie' i]",  # Dutch
                    "textarea[placeholder*='cover letter' i]",
                    "textarea[placeholder*='tell us' i]",
                    "textarea[placeholder*='vertel' i]",   # Dutch
                ],
                cl_text,
            )

        # -- LinkedIn / portfolio -------------------------------------------
        self.fill_first(
            ["input[name='linkedin']", "input[name='linkedin_url']",
             "input[placeholder*='LinkedIn' i]", "input[name*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='website']", "input[name='portfolio']",
             "input[placeholder*='website' i]", "input[placeholder*='portfolio' i]"],
            c.portfolio_url,
        )

        # -- Custom questions -----------------------------------------------
        self._answer_custom_questions()

        # -- GDPR consent ---------------------------------------------------
        for cb in p.locator("input[type='checkbox']").all():
            try:
                if cb.is_checked():
                    continue
                label_text = self._resolve_cb_label(cb)
                is_required = cb.get_attribute("required") is not None
                is_gdpr = "gdpr" in (cb.get_attribute("name") or "").lower()
                if is_required or is_gdpr or _CONSENT_RE.search(label_text):
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

    def _resolve_cb_label(self, cb) -> str:
        p = self.page
        cb_id = cb.get_attribute("id") or ""
        if cb_id:
            lbl = p.locator(f"label[for='{cb_id}']")
            if lbl.count() > 0:
                return lbl.first.inner_text()
        try:
            return cb.evaluate(
                "el => el.closest('label')?.innerText"
                " || el.parentElement?.innerText || ''"
            )
        except Exception:
            return ""

    def _answer_custom_questions(self) -> None:
        p = self.page
        c = self.candidate
        job = self.job

        _KNOWN = {
            "name", "email", "phone", "cv", "resume", "motivation", "cover",
            "linkedin", "website", "portfolio", "location", "city",
        }

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
                if any(k in label_text.lower() for k in _KNOWN):
                    continue

                ll = label_text.lower()
                answered = False

                if re.search(r"sponsor|visa", ll):
                    ans = "Yes" if c.needs_sponsorship(job.country) else "No"
                    answered = self._try(el, tag, ans)
                elif re.search(r"authoris?ed|right to work|eligible|werk.*vergunning", ll):
                    ans = "Yes" if c.authorized_eu else "No"
                    answered = self._try(el, tag, ans)
                elif re.search(r"relocation|relocat|verhuis", ll):
                    answered = self._try(el, tag, "Yes")
                elif re.search(r"notice period|start|beschikbaar", ll):
                    answered = self._try(el, tag, "Immediately")
                elif re.search(r"how did you hear|referral|waar.*gehoord", ll):
                    answered = self._try(el, tag, "Job board")
                elif re.search(r"salary|salaris|compensation", ll):
                    answered = self._try(el, tag, c.salary_for(job.country))
                elif tag in ("input", "textarea"):
                    answer = _llm_answer(label_text, c, job)
                    if answer:
                        try:
                            el.fill(answer)
                            answered = True
                        except Exception:
                            pass

                if not answered:
                    log.warning("homerun: unanswered field: %r", label_text)

            except Exception as exc:
                log.warning("homerun: field error: %s", exc)

    def _try(self, el, tag: str, value: str) -> bool:
        try:
            if tag == "select":
                el.select_option(label=value)
            else:
                el.fill(value)
            return True
        except Exception:
            return False
