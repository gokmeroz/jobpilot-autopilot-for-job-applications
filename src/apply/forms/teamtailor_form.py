"""
Teamtailor ATS form filler.

URL patterns:
  https://{company}.teamtailor.com/jobs/{slug}
  https://{company}.teamtailor.com/jobs/{slug}/applications/new
  Custom domains: https://career.{company}.com/jobs/{slug}  (fingerprinted by
  teamtailor.com subdomain; custom domains not auto-detected)

Teamtailor uses Rails-style field names: candidate[field] for identity and
answers[{question_id}][answer] for custom questions. The job listing page
requires clicking "Apply" / "Send application" to reach the actual form.
Forms are typically single-page; _walk_steps() handles multi-step variants.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm_answer(label: str, candidate, job) -> str:
    try:
        from anthropic import Anthropic
        from src.config import env, load as _load
        cfg = _load("config")
        model = cfg.get("score", {}).get("model", "claude-haiku-4-5-20251001")
        client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))
        profile = (
            f"Candidate: {candidate.full_name}\n"
            f"Role: {job.title} at {job.company}\n"
            f"Location: Istanbul, Turkey (open to relocation)\n"
            f"Experience: {candidate.yoe} year(s)\n"
            f"Skills: Node.js, TypeScript, React, Python, MongoDB, AWS, AI/LLM integration\n"
            f"Recent: Nummoria (AI personal finance SaaS, full-stack co-founder)\n"
            f"LinkedIn: {candidate.linkedin_url}\n"
            f"GitHub: {candidate.github_url}"
        )
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": (
                f"Fill a job application for the candidate below.\n"
                f"Answer this question concisely and professionally (1-3 sentences max).\n\n"
                f"{profile}\n\nQuestion: {label}\n\n"
                f"Return ONLY the answer text, nothing else."
            )}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("LLM answer failed for %r: %s", label, exc)
        return ""


def _select_opt(el, *opts: str) -> bool:
    for opt in opts:
        try:
            el.select_option(label=opt)
            return True
        except Exception:
            pass
    try:
        options = el.evaluate(
            "e => Array.from(e.options).map(o => ({v: o.value, t: o.text.trim()}))"
        )
        for target in opts:
            tl = target.lower()
            for option in options:
                if option["v"] and tl in option["t"].lower():
                    el.select_option(value=option["v"])
                    return True
    except Exception:
        pass
    return False


def _resolve_label(page, el) -> str:
    """Return the best human-readable label for a form element."""
    el_id = el.get_attribute("id") or ""
    if el_id:
        lbl = page.locator(f"label[for='{el_id}']")
        if lbl.count():
            return lbl.first.inner_text().strip()
    # aria-label, placeholder, name as fallbacks
    return (
        el.get_attribute("aria-label")
        or el.get_attribute("placeholder")
        or el.get_attribute("name")
        or ""
    )


def _answer_question(label: str, el, tag: str, candidate, job) -> bool:
    """Pattern-match or LLM-answer a custom Teamtailor question field."""
    ll = label.lower().strip()

    def _fill(v: str) -> bool:
        try:
            el.fill(str(v))
            return True
        except Exception:
            return False

    def _fos(text_val: str, *select_opts: str) -> bool:
        return _select_opt(el, *select_opts) if tag == "select" else _fill(text_val)

    if re.search(r"linkedin", ll):
        return _fill(candidate.linkedin_url)
    if re.search(r"github", ll):
        return _fill(candidate.github_url)
    if re.search(r"portfolio|personal.*(site|url|website)|your website", ll):
        return _fill(candidate.portfolio_url)
    if re.search(r"twitter|x\.com|x handle", ll):
        return _fill(candidate.twitter_url)

    if re.search(r"years?.*(professional|work|experience|coding)|how many years", ll):
        return _fos(str(candidate.yoe), str(candidate.yoe), "0", "1", "Less than 1", "0-1")
    if re.search(r"how did you hear|referral|where did you (find|hear|learn)", ll):
        return _fos("Job board", "Job board", "Online Job Board", "LinkedIn", "Other")
    if re.search(r"notice period|when can you start|available to start|earliest start", ll):
        return _fos("Immediately available", "Immediately", "ASAP", "0 days", "Less than 1 month")
    if re.search(r"salary|compensation|expected pay|desired pay|pay expectation", ll):
        val = candidate.salary_for(job.country)
        return _fos(val, val)
    if re.search(r"location|city|where.*based|current location|where do you live", ll):
        return _fos("Istanbul, Turkey",
                    "Turkey", "Türkiye", "Istanbul", "Other",
                    "International", "Located Elsewhere")
    if re.search(r"relocation|willing to relocate|open to reloc", ll):
        return _fos("Yes", "Yes", "Open to relocation")
    if re.search(r"open to remote|work remotely|remote work", ll):
        return _fos("Yes", "Yes", "Remote")
    if re.search(r"time.?zone|timezone", ll):
        return _fos("UTC+3", "UTC+3", "UTC +3", "GMT+3", "Europe/Istanbul", "Other")
    if re.search(r"authoris?ed.*work|right to work|work.*authoriz|eligible.*work", ll):
        ans = "No" if not candidate.authorized_eu else "Yes"
        return _fos(ans, ans)
    if re.search(r"visa.*sponsor|require.*sponsor|need.*sponsor|sponsorship|work visa", ll):
        ans = "Yes" if candidate.needs_sponsorship(job.country) else "No"
        return _fos(ans, ans)
    if re.search(r"nationality|passport|citizen", ll):
        return _fos("Turkish", "Turkish", "Turkey", "Türkiye", "Other")
    if re.search(r"gender", ll):
        return _fos("Prefer not to say", "Prefer not to say", "Male", "Man")
    if re.search(r"degree|highest.*education|education.*level", ll):
        return _fos("Bachelor's degree",
                    "Bachelor", "Bachelor's degree", "B.Sc.", "University degree")
    if re.search(r"agree|confirm|accept|consent|acknowledge|privacy|terms", ll):
        return _fos("Yes", "Yes", "I agree", "I confirm", "I accept")

    if tag in ("input", "textarea"):
        answer = _llm_answer(label, candidate, job)
        if answer:
            return _fill(answer)

    if tag == "select":
        try:
            opts = el.evaluate("e => Array.from(e.options).map(o => ({v: o.value, t: o.text}))")
            _SKIP = {"", "select...", "please select", "choose...", "-- select --"}
            for opt in opts:
                if opt["v"] and opt["t"].strip().lower() not in _SKIP:
                    el.select_option(value=opt["v"])
                    return True
        except Exception:
            pass

    return False


# ---------------------------------------------------------------------------
# Form filler
# ---------------------------------------------------------------------------

_APPLY_BTN_TEXTS = (
    "Apply", "Apply now", "Apply for this job", "Apply for this position",
    "Send application", "Start application",
)

# candidate[field] names Teamtailor uses for identity — skip these in the
# custom-question sweep since they are handled explicitly above.
_CANDIDATE_FIELDS = {
    "first_name", "last_name", "email", "phone", "pitch",
    "resume", "linkedin_profile", "website",
}

_CONSENT_PAT = re.compile(
    r"privacy|gdpr|consent|agree|terms|policy|data.*process",
    re.IGNORECASE,
)

_SUBMIT_SEL = (
    "button[type='submit']:has-text('Send application'),"
    "button[type='submit']:has-text('Submit application'),"
    "button[type='submit']:has-text('Apply'),"
    "button[type='submit']:has-text('Submit'),"
    "button[type='submit'],"
    "input[type='submit']"
)


class TeamtailorForm(BaseFormFiller):

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

        # -- Navigate to application form if on job listing page -------------
        # Teamtailor job pages show description + prominent Apply button.
        # Clicking it navigates to /applications/new (same tab).
        if not p.query_selector(
            "input[name='candidate[first_name]'], input[name='candidate[email]'], "
            "input[type='email']"
        ):
            for _text in _APPLY_BTN_TEXTS:
                try:
                    btn = p.get_by_text(_text, exact=True).first
                    if btn.count() > 0 and btn.is_visible(timeout=500):
                        btn.click()
                        try:
                            p.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:
                            p.wait_for_timeout(2_000)
                        break
                except Exception:
                    continue

        try:
            p.wait_for_selector(
                "input[name='candidate[first_name]'], input[name='candidate[email]'], "
                "input[type='email']",
                timeout=15_000,
            )
        except Exception:
            raise NeedsUserInput(
                "Teamtailor application form did not render — "
                "job may be closed or the URL changed"
            )

        # -- Identity --------------------------------------------------------
        # Primary: candidate[field] name attributes (Teamtailor's Rails convention).
        # Fallback: label text matching.

        self.fill_first(
            ["input[name='candidate[first_name]']",
             "input[id='candidate_first_name']"],
            c.first_name,
        ) or p.get_by_label("First name", exact=False).first.fill(c.first_name) if (
            p.get_by_label("First name", exact=False).count() > 0
        ) else None

        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='candidate[last_name]']",
             "input[id='candidate_last_name']"],
            c.last_name,
        ) or p.get_by_label("Last name", exact=False).first.fill(c.last_name) if (
            p.get_by_label("Last name", exact=False).count() > 0
        ) else None

        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='candidate[email]']",
             "input[id='candidate_email']",
             "input[type='email']"],
            c.email,
        )

        time.sleep(random.uniform(0.2, 0.4))

        self.fill_first(
            ["input[name='candidate[phone]']",
             "input[id='candidate_phone']",
             "input[type='tel']"],
            c.phone,
        )

        time.sleep(random.uniform(0.2, 0.4))

        # -- Resume ----------------------------------------------------------
        _cv_uploaded = (
            self.upload("input[name='candidate[resume]']", c.resume_path)
            or self.upload("input[id='candidate_resume']", c.resume_path)
            or self.upload("input[type='file'][name*='resume' i]", c.resume_path)
            or self.upload("input[type='file'][name*='cv' i]", c.resume_path)
        )
        if not _cv_uploaded:
            log.warning("teamtailor: could not upload resume for %s @ %s", job.title, job.company)

        # -- Cover letter / pitch --------------------------------------------
        # Teamtailor calls this field "pitch" (candidate[pitch]).
        # Some companies also offer a file upload for a cover letter doc.
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        # File upload first (some Teamtailor configs allow attaching a cover letter doc)
        _cl_file_uploaded = False
        for _sel in (
            "input[type='file'][name*='cover' i]",
            "input[type='file'][name*='letter' i]",
        ):
            try:
                el = p.query_selector(_sel)
                if el:
                    _cl_pdf = self.generate_cover_letter_pdf()
                    el.set_input_files(str(_cl_pdf))
                    _cl_file_uploaded = True
                    log.info("teamtailor: uploaded cover letter PDF via %s", _sel)
                    break
            except Exception:
                pass

        if not _cl_file_uploaded:
            self.fill_first(
                ["textarea[name='candidate[pitch]']",
                 "textarea[id='candidate_pitch']",
                 "textarea[name*='pitch' i]",
                 "textarea[name*='cover' i]",
                 "textarea[placeholder*='cover letter' i]",
                 "textarea[placeholder*='motivation' i]",
                 "textarea[placeholder*='tell us' i]"],
                cl_text,
            )

        # -- Links -----------------------------------------------------------
        self.fill_first(
            ["input[name='candidate[linkedin_profile]']",
             "input[id='candidate_linkedin_profile']",
             "input[name*='linkedin' i]",
             "input[placeholder*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='candidate[website]']",
             "input[id='candidate_website']",
             "input[name*='website' i]",
             "input[name*='portfolio' i]",
             "input[placeholder*='website' i]",
             "input[placeholder*='portfolio' i]"],
            c.portfolio_url,
        )

        # -- Custom questions (answers[{id}][answer]) -------------------------
        # Teamtailor encodes custom question inputs as answers[{question_id}][answer].
        # We find all such inputs, resolve the label from the associated <label>,
        # and answer using pattern matching + LLM fallback.
        for el in p.locator(
            "input[name^='answers['], textarea[name^='answers['], select[name^='answers[']"
        ).all():
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                el_type = (el.get_attribute("type") or "text").lower()
                if el_type in ("file", "hidden", "checkbox", "radio"):
                    continue
                if tag != "select":
                    try:
                        if el.input_value():
                            continue
                    except Exception:
                        continue

                label_text = _resolve_label(p, el)
                if not label_text:
                    continue

                answered = _answer_question(label_text, el, tag, c, job)
                if not answered:
                    log.warning("teamtailor: unanswered question: %r", label_text)
            except Exception as exc:
                log.warning("teamtailor: error on custom question: %s", exc)

        # -- Required field sweep (catches anything outside answers[] scope) --
        _KNOWN = {
            "first_name", "last_name", "email", "phone", "pitch",
            "resume", "linkedin", "website", "portfolio", "name",
        }
        for el in p.locator(
            "input[required], textarea[required], select[required],"
            "input[aria-required='true'], textarea[aria-required='true'],"
            "select[aria-required='true']"
        ).all():
            try:
                el_type = (el.get_attribute("type") or "text").lower()
                if el_type in ("file", "hidden", "checkbox", "radio"):
                    continue
                el_name = (el.get_attribute("name") or "").lower()
                # Skip candidate[] fields and answers[] fields (handled above)
                if any(k in el_name for k in _KNOWN):
                    continue
                if "answers[" in el_name:
                    continue

                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue
                if tag != "select":
                    try:
                        if el.input_value():
                            continue
                    except Exception:
                        continue

                label_text = _resolve_label(p, el)
                if not label_text:
                    continue

                answered = _answer_question(label_text, el, tag, c, job)
                if not answered:
                    raise NeedsUserInput(f"Unknown required field: '{label_text}'")
            except NeedsUserInput:
                raise
            except Exception:
                pass

        # -- GDPR / consent checkboxes ---------------------------------------
        for cb in p.locator("input[type='checkbox']").all():
            try:
                if cb.is_checked():
                    continue
                is_required = (
                    cb.get_attribute("required") is not None
                    or cb.get_attribute("aria-required") == "true"
                )
                cb_name = (cb.get_attribute("name") or "").lower()
                label_text = _resolve_label(p, cb)
                if not label_text:
                    try:
                        label_text = cb.evaluate(
                            "el => el.closest('label')?.innerText"
                            " || el.parentElement?.innerText"
                            " || el.parentElement?.parentElement?.innerText || ''"
                        )
                    except Exception:
                        pass
                is_gdpr = "gdpr_consent" in cb_name or "consent" in cb_name
                if is_required or is_gdpr or _CONSENT_PAT.search(label_text):
                    try:
                        cb.check()
                    except Exception:
                        try:
                            cb.click()
                        except Exception:
                            pass
            except Exception:
                pass

        # -- Walk through any paginated steps --------------------------------
        self._walk_steps()

        # -- Submit ----------------------------------------------------------
        self.submit(_SUBMIT_SEL)
