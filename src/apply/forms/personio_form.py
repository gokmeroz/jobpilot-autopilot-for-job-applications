"""
Personio ATS form filler.

URL patterns:
  https://jobs.personio.de/job/{id}           — hosted (English / German)
  https://jobs.personio.com/job/{id}          — hosted (English)
  https://{company}.jobs.personio.de/{id}     — company-branded subdomain

The job listing page shows a description + "Apply now" / "Jetzt bewerben"
button. Clicking it replaces the page content with the application form or
navigates to a /apply sub-route (behaviour varies by company config).

Personio forms can be single-page or multi-step. _walk_steps() handles the
multi-step case automatically.
"""
from __future__ import annotations

import logging
import re
import time
import random

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers (kept as functions so they can be called without `self`)
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


def _fill_by_label(page, label_frag: str, value: str, slow: bool = False) -> bool:
    try:
        el = page.get_by_label(label_frag, exact=False)
        if el.count() > 0:
            target = el.first
            target.click()
            target.fill("")
            if slow:
                target.type(value, delay=random.randint(30, 80))
                time.sleep(random.uniform(0.1, 0.3))
            else:
                target.fill(value)
            return True
    except Exception:
        pass
    return False


def _upload_by_label(page, label_frag: str, path: str) -> bool:
    try:
        el = page.get_by_label(label_frag, exact=False)
        if el.count() > 0:
            el.first.set_input_files(path)
            return True
    except Exception:
        pass
    return False


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


def _answer_question(label: str, el, tag: str, candidate, job) -> bool:
    """Pattern-match or LLM-answer a custom Personio question field."""
    ll = label.lower().strip()

    def _fill(v: str) -> bool:
        try:
            el.fill(str(v))
            return True
        except Exception:
            return False

    def _fos(text_val: str, *select_opts: str) -> bool:
        return _select_opt(el, *select_opts) if tag == "select" else _fill(text_val)

    # URLs
    if re.search(r"linkedin", ll):
        return _fill(candidate.linkedin_url)
    if re.search(r"github", ll):
        return _fill(candidate.github_url)
    if re.search(r"portfolio|personal.*(site|url|website)|your website", ll):
        return _fill(candidate.portfolio_url)
    if re.search(r"twitter|x\.com|x handle", ll):
        return _fill(candidate.twitter_url)

    # Factual
    if re.search(r"years?.*(professional|work|experience|coding)|berufserfahrung|wie viele jahre", ll):
        return _fos(str(candidate.yoe), str(candidate.yoe), "0", "1", "Less than 1", "0-1")
    if re.search(r"how did you hear|referral|wo haben sie|wie sind sie auf|where did you (find|hear)", ll):
        return _fos("Job board", "Job board", "Online Job Board", "LinkedIn", "Stellenanzeige", "Other")
    if re.search(r"notice period|when can you start|verfügbar|eintrittstermin|available to start|earliest start", ll):
        return _fos("Immediately available", "Immediately", "Sofort", "ASAP", "0 days", "Less than 1 month")
    if re.search(r"salary|gehalt|compensation|desired pay|gehaltsvorstellung|expected.*pay|pay.*expectation", ll):
        val = candidate.salary_for(job.country)
        return _fos(val, val)
    if re.search(r"location|city|ort|standort|current location|where.*based|where.*live", ll):
        return _fos("Istanbul, Turkey", "Turkey", "Türkiye", "Istanbul",
                    "Other", "International", "Located Elsewhere")
    if re.search(r"relocation|umzug|willing to relocate|open to reloc|relocate for", ll):
        return _fos("Yes", "Yes", "Ja", "Open to relocation")
    if re.search(r"open to remote|work remotely|remote work|remote position", ll):
        return _fos("Yes", "Yes", "Ja", "Remote")
    if re.search(r"time.?zone|zeitzone", ll):
        return _fos("UTC+3", "UTC+3", "UTC +3", "GMT+3", "Europe/Istanbul", "Other")
    if re.search(r"authoris?ed.*work|right to work|arbeitsgenehmigung|arbeitserlaubnis"
                 r"|work.*permit|berechtigt.*arbeiten", ll):
        ans = "No" if not candidate.authorized_eu else "Yes"
        return _fos(ans, ans, "Nein" if ans == "No" else "Ja")
    if re.search(r"visa.*sponsor|sponsorship|visa.*benötigen|arbeitsvisum|work visa", ll):
        ans = "Yes" if candidate.needs_sponsorship(job.country) else "No"
        return _fos(ans, ans, "Ja" if ans == "Yes" else "Nein")
    if re.search(r"nationality|staatsangehörigkeit|nationalität|passport", ll):
        return _fos("Turkish", "Turkish", "Türkisch", "Turkey", "Türkei", "Other")
    if re.search(r"gender|geschlecht", ll):
        return _fos("Prefer not to say", "Prefer not to say", "Male", "Mann", "Männlich")
    if re.search(r"degree|abschluss|highest.*education|bildungsabschluss", ll):
        return _fos("Bachelor's degree",
                    "Bachelor", "Bachelor's degree", "B.Sc.",
                    "University degree", "Hochschulabschluss")
    if re.search(r"pronouns?|pronomen", ll):
        return _fos("He/Him", "He/Him", "He / Him", "Prefer not to say")
    if re.search(r"ethnicity|race|herkunft", ll):
        return _fos("Prefer not to say",
                    "Prefer not to say", "Decline to identify",
                    "I prefer not to answer")
    if re.search(r"disability|behinderung", ll):
        return _fos("No", "No, I don't have a disability", "Nein",
                    "I prefer not to answer", "Prefer not to say")
    if re.search(r"veteran|military|militär", ll):
        return _fos("No", "I am not a protected veteran", "No", "Nein")
    if re.search(r"agree|confirm|accept|consent|acknowledge|datenschutz|einverstanden", ll):
        return _fos("Yes", "Yes", "Ja", "I agree", "I confirm")

    # LLM fallback for open-ended text / textarea
    if tag in ("input", "textarea"):
        answer = _llm_answer(label, candidate, job)
        if answer:
            return _fill(answer)

    # Select fallback: pick first substantive option
    if tag == "select":
        try:
            opts = el.evaluate("e => Array.from(e.options).map(o => ({v: o.value, t: o.text}))")
            _PLACEHOLDERS = {"", "select...", "please select", "bitte wählen",
                             "-- bitte auswählen --", "choose..."}
            for opt in opts:
                if opt["v"] and opt["t"].strip().lower() not in _PLACEHOLDERS:
                    el.select_option(value=opt["v"])
                    return True
        except Exception:
            pass

    return False


# ---------------------------------------------------------------------------
# Form filler
# ---------------------------------------------------------------------------

_APPLY_BTN_TEXTS = (
    "Apply now", "Apply for this job", "Apply for this position",
    "Jetzt bewerben", "Jetzt bewerben!", "Apply", "Bewerben",
    "Start application", "Start your application",
)

_SUBMIT_SEL = (
    "button[type='submit']:has-text('Send application'),"
    "button[type='submit']:has-text('Bewerbung absenden'),"
    "button[type='submit']:has-text('Submit application'),"
    "button[type='submit']:has-text('Jetzt bewerben'),"
    "button[type='submit']:has-text('Submit'),"
    "button[type='submit'],"
    "button:has-text('Send application'),"
    "button:has-text('Bewerbung absenden'),"
    "input[type='submit']"
)

_KNOWN_LABELS = {
    "first", "last", "vorname", "nachname", "name", "email", "e-mail",
    "phone", "telefon", "mobile", "location", "ort", "city", "standort",
    "resume", "cv", "lebenslauf", "curriculum", "cover", "anschreiben",
    "motivation", "linkedin", "github", "website", "portfolio",
}

_CONSENT_PAT = re.compile(
    r"privacy|datenschutz|gdpr|dsgvo|terms|nutzungsbedingungen"
    r"|consent|einwilligung|agree|declare|bestätig",
    re.IGNORECASE,
)


class PersonioForm(BaseFormFiller):

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
        # Check whether the form is already visible (some URLs land directly on it).
        if not p.query_selector("input[type='email'], input[name*='email' i]"):
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
                "input[type='email'], input[name*='email' i], input[name='email']",
                timeout=15_000,
            )
        except Exception:
            raise NeedsUserInput(
                "Personio application form did not render — "
                "job may be closed or require a login"
            )

        # -- Identity --------------------------------------------------------
        # Try English labels, then German equivalents, then name/id attributes.

        if not _fill_by_label(p, "First name", c.first_name, slow=True):
            if not _fill_by_label(p, "Vorname", c.first_name, slow=True):
                self.fill_first([
                    "input[name='first_name']", "input[name='firstName']",
                    "input[id*='first' i]", "input[placeholder*='First name' i]",
                    "input[placeholder*='Vorname' i]",
                ], c.first_name)

        time.sleep(random.uniform(0.2, 0.4))

        if not _fill_by_label(p, "Last name", c.last_name, slow=True):
            if not _fill_by_label(p, "Nachname", c.last_name, slow=True):
                self.fill_first([
                    "input[name='last_name']", "input[name='lastName']",
                    "input[id*='last' i]", "input[placeholder*='Last name' i]",
                    "input[placeholder*='Nachname' i]",
                ], c.last_name)

        time.sleep(random.uniform(0.2, 0.4))

        if not _fill_by_label(p, "Email", c.email):
            if not _fill_by_label(p, "E-Mail", c.email):
                if not _fill_by_label(p, "E-mail", c.email):
                    self.fill_first([
                        "input[type='email']", "input[name='email']", "input[id*='email' i]",
                    ], c.email)

        time.sleep(random.uniform(0.2, 0.4))

        if not _fill_by_label(p, "Phone", c.phone):
            if not _fill_by_label(p, "Telefon", c.phone):
                if not _fill_by_label(p, "Mobile", c.phone):
                    self.fill_first([
                        "input[type='tel']", "input[name='phone']",
                        "input[name='telephone']", "input[id*='phone' i]",
                    ], c.phone)

        time.sleep(random.uniform(0.2, 0.4))

        # -- Location --------------------------------------------------------
        for _lbl in ("Location", "City", "Ort", "Standort", "Current location", "Wohnort"):
            if _fill_by_label(p, _lbl, c.location):
                break

        # -- Resume ----------------------------------------------------------
        _cv_uploaded = False
        for _lbl in ("CV", "Resume", "Lebenslauf", "Curriculum Vitae", "Upload CV",
                     "Upload Resume", "Lebenslauf hochladen"):
            if _upload_by_label(p, _lbl, str(c.resume_path)):
                _cv_uploaded = True
                log.info("personio: uploaded resume via label %r", _lbl)
                break
        if not _cv_uploaded:
            for _sel in (
                "input[type='file'][name*='resume' i]",
                "input[type='file'][name*='cv' i]",
                "input[type='file'][name*='lebenslauf' i]",
                "input[type='file']",
            ):
                if self.upload(_sel, c.resume_path):
                    _cv_uploaded = True
                    break
        if not _cv_uploaded:
            log.warning("personio: could not upload resume for %s @ %s", job.title, job.company)

        # -- Cover letter (PDF upload preferred, text fallback) --------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)
        _cl_handled = False
        for _lbl in ("Cover letter", "Motivation letter", "Anschreiben",
                     "Motivationsschreiben", "Motivationsbrief"):
            try:
                loc = p.get_by_label(_lbl, exact=False)
                if loc.count() > 0:
                    el = loc.first
                    is_file = el.evaluate("e => e.type === 'file'")
                    if is_file:
                        _cl_pdf = self.generate_cover_letter_pdf()
                        el.set_input_files(str(_cl_pdf))
                        log.info("personio: uploaded cover letter PDF via label %r", _lbl)
                    else:
                        el.fill(cl_text)
                        log.info("personio: filled cover letter text via label %r", _lbl)
                    _cl_handled = True
                    break
            except Exception as exc:
                log.warning("personio: cover letter label %r failed: %s", _lbl, exc)

        if not _cl_handled:
            for _sel in (
                "input[type='file'][name*='cover' i]",
                "input[type='file'][name*='anschreiben' i]",
                "input[type='file'][name*='motivation' i]",
            ):
                try:
                    el = p.query_selector(_sel)
                    if el:
                        _cl_pdf = self.generate_cover_letter_pdf()
                        el.set_input_files(str(_cl_pdf))
                        _cl_handled = True
                        log.info("personio: uploaded cover letter PDF via selector %r", _sel)
                        break
                except Exception:
                    pass
        if not _cl_handled:
            self.fill_first([
                "textarea[name*='cover' i]",
                "textarea[name*='motivation' i]",
                "textarea[name*='anschreiben' i]",
                "textarea[placeholder*='cover letter' i]",
                "textarea[placeholder*='motivation' i]",
            ], cl_text)

        # -- Links -----------------------------------------------------------
        for _lbl in ("LinkedIn", "LinkedIn URL", "LinkedIn Profile", "LinkedIn-Profil"):
            if _fill_by_label(p, _lbl, c.linkedin_url):
                break
        for _lbl in ("GitHub", "GitHub URL", "Github", "Github Profile"):
            if _fill_by_label(p, _lbl, c.github_url):
                break
        for _lbl in ("Website", "Portfolio", "Personal website", "Personal Website"):
            if _fill_by_label(p, _lbl, c.portfolio_url):
                break

        # -- Custom questions -------------------------------------------------
        for el in p.locator(
            "input[required], textarea[required], select[required],"
            "input[aria-required='true'], textarea[aria-required='true'],"
            "select[aria-required='true']"
        ).all():
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue
                el_type = (el.get_attribute("type") or "text").lower()
                if el_type in ("file", "checkbox", "radio", "hidden"):
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
                    if lbl.count():
                        label_text = lbl.first.inner_text()
                if not label_text:
                    label_text = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("placeholder")
                        or el.get_attribute("name")
                        or ""
                    )
                ll = label_text.lower()
                if any(k in ll for k in _KNOWN_LABELS):
                    continue

                answered = _answer_question(label_text, el, tag, c, job)
                if not answered:
                    log.warning("personio: unanswered required field: %r", label_text)
            except Exception as exc:
                log.warning("personio: error on required field: %s", exc)

        # -- Consent / GDPR checkboxes ---------------------------------------
        for cb in p.locator("input[type='checkbox']").all():
            try:
                if cb.is_checked():
                    continue
                is_required = (
                    cb.get_attribute("required") is not None
                    or cb.get_attribute("aria-required") == "true"
                )
                label_text = ""
                cb_id = cb.get_attribute("id") or ""
                if cb_id:
                    lbl = p.locator(f"label[for='{cb_id}']")
                    if lbl.count():
                        label_text = lbl.first.inner_text()
                if not label_text:
                    try:
                        label_text = cb.evaluate(
                            "el => el.closest('label')?.innerText"
                            " || el.parentElement?.innerText"
                            " || el.parentElement?.parentElement?.innerText || ''"
                        )
                    except Exception:
                        pass
                if is_required or _CONSENT_PAT.search(label_text):
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
