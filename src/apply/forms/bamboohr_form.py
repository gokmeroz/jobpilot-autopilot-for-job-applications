"""
BambooHR ATS form filler.

URL patterns:
  https://{company}.bamboohr.com/careers/{id}/apply
  https://{company}.bamboohr.com/jobs/view.php?id={id}  ← listing; click Apply

BambooHR forms use clean name attributes on standard HTML inputs. They're
typically single-page but may include a paginated multi-step wrapper on
some company configs. Common in US-based startups (50-500 employees).
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)

_APPLY_TEXTS = (
    "Apply for this Job", "Apply for This Job", "Apply Now",
    "Apply", "Apply for this position",
)

_SUBMIT_SEL = (
    "button#submit-btn,"
    "button[type='submit']:has-text('Submit Application'),"
    "button[type='submit']:has-text('Apply'),"
    "button[type='submit']:has-text('Submit'),"
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
                f"Fill a BambooHR job application for Goktug Mert Ozdogan "
                f"(software engineer, Istanbul, 1yr exp, Node.js/React/Python/AWS, "
                f"Nummoria AI SaaS co-founder, open to relocation).\n"
                f"Role: {job.title} at {job.company}\n\n"
                f"Question: {label}\n\n"
                f"Return ONLY the answer text, 1-3 sentences max."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("bamboohr: LLM answer failed for %r: %s", label, exc)
        return ""


class BambooHRForm(BaseFormFiller):

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

        # -- Navigate to apply form if on job listing page ------------------
        _form_sel = (
            "input[name='firstName'], input[id='firstName'], "
            "input[name='first_name'], input[type='email']"
        )
        if not p.query_selector(_form_sel):
            for _text in _APPLY_TEXTS:
                try:
                    btn = p.get_by_text(_text, exact=False).first
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
                "BambooHR application form did not render — "
                "job may be closed or URL changed"
            )

        # -- Identity -------------------------------------------------------
        self.fill_first(
            ["input[name='firstName']", "input[id='firstName']",
             "input[name='first_name']", "input[id='first_name']",
             "input[placeholder*='First name' i]",
             "input[aria-label*='First name' i]"],
            c.first_name,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='lastName']", "input[id='lastName']",
             "input[name='last_name']", "input[id='last_name']",
             "input[placeholder*='Last name' i]",
             "input[aria-label*='Last name' i]"],
            c.last_name,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='email']", "input[id='email']",
             "input[type='email']"],
            c.email,
        )
        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='phone']", "input[id='phone']",
             "input[name='phoneNumber']", "input[id='phoneNumber']",
             "input[type='tel']",
             "input[placeholder*='phone' i]"],
            c.phone,
        )

        # -- Location -------------------------------------------------------
        self.fill_first(
            ["input[name='location']", "input[id='location']",
             "input[name='city']", "input[id='city']",
             "input[placeholder*='location' i]",
             "input[placeholder*='city' i]"],
            "Istanbul, Turkey",
        )
        self.fill_first(
            ["input[name='country']", "select[name='country']",
             "select[id='country']"],
            "Turkey",
        )

        # -- Resume upload --------------------------------------------------
        _cv_uploaded = (
            self.upload("input[name='resume']", c.resume_path)
            or self.upload("input[id='resume']", c.resume_path)
            or self.upload("input[type='file'][name*='resume' i]", c.resume_path)
            or self.upload("input[type='file'][name*='cv' i]", c.resume_path)
            or self.upload("input[type='file']", c.resume_path)
        )
        if not _cv_uploaded:
            log.warning("bamboohr: could not upload resume")

        # -- Cover letter ---------------------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        # BambooHR sometimes has a file input for cover letter
        _cl_file_uploaded = False
        for _sel in (
            "input[name='coverLetter']",
            "input[name='cover_letter']",
            "input[type='file'][name*='cover' i]",
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
                ["textarea[name='coverLetter']",
                 "textarea[name='cover_letter']",
                 "textarea[id='coverLetter']",
                 "textarea[id='cover_letter']",
                 "textarea[placeholder*='cover letter' i]",
                 "textarea[placeholder*='motivation' i]",
                 "textarea[placeholder*='tell us' i]",
                 "textarea[aria-label*='cover letter' i]"],
                cl_text,
            )

        # -- Social / professional links ------------------------------------
        self.fill_first(
            ["input[name='linkedIn']", "input[name='linkedin']",
             "input[id='linkedIn']", "input[placeholder*='LinkedIn' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='gitHub']", "input[name='github']",
             "input[id='gitHub']", "input[placeholder*='GitHub' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[name='website']", "input[id='website']",
             "input[name='portfolioUrl']",
             "input[placeholder*='website' i]",
             "input[placeholder*='portfolio' i]"],
            c.portfolio_url,
        )

        # -- Salary / compensation -----------------------------------------
        val = c.salary_for(job.country)
        self.fill_first(
            ["input[name='salary']", "input[name='desiredSalary']",
             "input[name='expectedSalary']",
             "input[id*='salary' i]",
             "input[placeholder*='salary' i]"],
            val,
        )

        # -- EEO / demographic selects (BambooHR includes these for US jobs) -
        # Gender
        self._select_try("select[name='gender'], select[id='gender']",
                         "Prefer not to say", "Male", "M")
        # Ethnicity (US)
        self._select_try("select[name='ethnicity'], select[id='ethnicity']",
                         "Prefer Not to Respond", "Not Specified",
                         "Two or More Races", "Other")
        # Veteran status (US)
        self._select_try(
            "select[name='veteranStatus'], select[id='veteranStatus']",
            "I am not a protected veteran", "Not a Veteran", "No",
        )
        # Disability (US)
        self._select_try(
            "select[name='disability'], select[id='disability']",
            "I don't wish to answer", "Prefer Not to Respond",
            "No, I Do Not Have a Disability",
        )

        # -- "How did you hear about us" ------------------------------------
        self._select_try(
            "select[name='howDidYouHear'], select[name='how_did_you_hear'],"
            "select[id*='howDidYouHear' i]",
            "Job Board", "LinkedIn", "Online Job Board", "Other",
        )

        # -- Custom / additional questions ----------------------------------
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
                    "website", "portfolio", "salary", "city", "location",
                    "country", "gender", "ethnicity", "veteran", "disability",
                }
                if any(k in label_text.lower().replace(" ", "") for k in _KNOWN):
                    continue

                ll = label_text.lower()
                answered = False

                if re.search(r"sponsor|visa", ll):
                    ans = "Yes" if c.needs_sponsorship(job.country) else "No"
                    answered = self._try_answer(el, tag, ans)
                elif re.search(r"authoris?ed|right to work|eligible", ll):
                    ans = "Yes" if c.authorized_eu else "No"
                    answered = self._try_answer(el, tag, ans)
                elif re.search(r"relocation|relocate", ll):
                    answered = self._try_answer(el, tag, "Yes")
                elif re.search(r"notice period|start date|when can you start", ll):
                    answered = self._try_answer(el, tag, "Immediately")
                elif re.search(r"how did you hear|referral|where did you", ll):
                    answered = self._try_answer(el, tag, "Job board")
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
                            if opt["v"] and opt["t"].lower() not in {"", "select...", "please select", "choose an option"}:
                                el.select_option(value=opt["v"])
                                answered = True
                                break
                    except Exception:
                        pass

                if not answered:
                    log.warning("bamboohr: unanswered required field: %r", label_text)

            except Exception as exc:
                log.warning("bamboohr: field error: %s", exc)

        # -- GDPR / consent checkboxes --------------------------------------
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
                        try:
                            cb.click()
                        except Exception:
                            pass
            except Exception:
                pass

        # -- Walk paginated steps, then submit -------------------------------
        self._walk_steps()
        self.submit(_SUBMIT_SEL)

    # -----------------------------------------------------------------------

    def _select_try(self, selector: str, *opts: str) -> bool:
        for sel in selector.split(","):
            sel = sel.strip()
            try:
                el = self.page.query_selector(sel)
                if not el:
                    continue
                for opt in opts:
                    try:
                        el.select_option(label=opt)
                        return True
                    except Exception:
                        pass
            except Exception:
                pass
        return False

    def _try_answer(self, el, tag: str, value: str) -> bool:
        try:
            if tag == "select":
                el.select_option(label=value)
            else:
                el.fill(value)
            return True
        except Exception:
            return False
