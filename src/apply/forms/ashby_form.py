"""
Ashby ATS form filler.

Apply URL pattern: https://jobs.ashbyhq.com/{company}/{uuid}
React-based forms — relies on ARIA roles and labels rather than id/name attrs.
"""
from __future__ import annotations

import logging
import re as _re

from playwright.sync_api import Page

from src.apply.base import BaseFormFiller, NeedsUserInput, FILL_TIMEOUT

log = logging.getLogger(__name__)


def _llm_answer(label: str, candidate, job) -> str:
    """Use Claude Haiku to answer an open-ended application question."""
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


def _answer_custom_field(el, tag: str, label: str, candidate, job, cfg: dict | None = None) -> bool:
    """Pattern-match or LLM-answer an unfilled custom required field."""
    ll = label.lower().strip()

    def _fill(v: str) -> bool:
        try:
            el.fill(str(v))
            return True
        except Exception:
            return False

    def _select(*opts: str) -> bool:
        # Exact label match first, then partial text match
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

    def _fill_or_select(text_val: str, select_opts: list) -> bool:
        if tag == "select":
            return _select(*select_opts)
        return _fill(text_val)

    # URLs
    if _re.search(r"linkedin", ll):
        return _fill(candidate.linkedin_url)
    if _re.search(r"github", ll):
        return _fill(candidate.github_url)
    if _re.search(r"portfolio|personal.*(site|url|website)|your website", ll):
        return _fill(candidate.portfolio_url)
    if _re.search(r"twitter|x\.com|x handle", ll):
        return _fill(candidate.twitter_url)

    # Factual lookups
    if _re.search(r"years?.*(professional|work|coding|programming|software|dev)|how many years", ll):
        return _fill_or_select(str(candidate.yoe),
                               [str(candidate.yoe), "0", "1", "Less than 1", "Less than 2", "0-1"])
    if _re.search(r"how did you hear|referral|where did you (find|learn|hear)", ll):
        return _fill_or_select("Job board", ["Job board", "LinkedIn", "Online Job Board", "Other"])
    if _re.search(r"notice period|when can you start|available to start|earliest start", ll):
        return _fill_or_select("Immediately available",
                               ["Immediately", "0 days", "Less than 1 month", "ASAP"])
    if _re.search(r"location|city|where are you based|current location|where do you live"
                  r"|country.*located|located.*country|country.*resid|resid.*country"
                  r"|choose.*country|current country|country of residence", ll):
        return _fill_or_select("Istanbul, Turkey",
                               ["Turkey", "Located Elsewhere", "Other",
                                "Outside United States", "International", "Europe"])
    if _re.search(r"salary|compensation|expected pay|desired pay|pay expectation", ll):
        val = candidate.salary_for(job.country)
        return _fill_or_select(val, [val])
    if _re.search(r"relocation|willing to relocate|open to reloc|relocate for", ll):
        return _fill_or_select("Yes", ["Yes", "Open to relocation", "Yes, willing to relocate"])
    if _re.search(r"open to remote|work remotely|remote work|remote position", ll):
        return _fill_or_select("Yes", ["Yes", "Remote"])

    # Work authorization
    if _re.search(r"gender", ll):
        return _fill_or_select("Male", ["Male", "Man", "Prefer not to say"])
    if _re.search(r"pronoun", ll):
        return _fill_or_select("He/Him", ["He/Him", "He / Him", "Prefer not to say"])

    if _re.search(r"visa.*status|right to work|work.*authoriz|work.*status", ll):
        ans = ("I am a Turkish national based in Istanbul and would require visa sponsorship "
               "to work in your country.")
        return _fill_or_select(ans, ["Require sponsorship", "Need sponsorship", "No", "Other"])
    if _re.search(r"authorized.*work|eligible.*work", ll):
        ans = "No" if not candidate.authorized_eu else "Yes"
        return _fill_or_select(ans, [ans])
    if _re.search(r"require.*sponsor|visa sponsor|need.*sponsor|sponsorship", ll):
        ans = "Yes" if candidate.needs_sponsorship(job.country) else "No"
        return _fill_or_select(ans, [ans])

    # LLM fallback for open text / textarea
    if tag in ("input", "textarea"):
        answer = _llm_answer(label, candidate, job)
        if answer:
            return _fill(answer)

    # Select fallback is intentionally opt-in. Unknown dropdowns are often
    # work-auth, eligibility, EEO, or office-location questions where guessing
    # the first option can submit a wrong answer.
    allow_select_fallback = bool((cfg or {}).get("apply", {}).get("allow_select_fallback", False))
    if tag == "select" and allow_select_fallback:
        try:
            opts = el.evaluate("e => Array.from(e.options).map(o => ({v: o.value, t: o.text}))")
            for opt in opts:
                if opt["v"] and opt["t"].strip() not in ("", "Select...", "Please select"):
                    el.select_option(value=opt["v"])
                    return True
        except Exception:
            pass

    return False


def _fill_by_label(page: Page, label_fragment: str, value: str, slow: bool = False) -> bool:
    """Fill the input associated with a label containing label_fragment."""
    import time, random
    try:
        el = page.get_by_label(label_fragment, exact=False)
        if el.count() > 0:
            target = el.first
            target.click()
            target.fill("")
            if slow:
                # Human-like typing with random inter-key delay to bypass bot detection
                target.type(value, delay=random.randint(30, 80))
                time.sleep(random.uniform(0.1, 0.3))
            else:
                target.fill(value)
            return True
    except Exception:
        pass
    return False


def _upload_by_label(page: Page, label_fragment: str, path: str) -> bool:
    try:
        el = page.get_by_label(label_fragment, exact=False)
        if el.count() > 0:
            el.first.set_input_files(path)
            return True
    except Exception:
        pass
    # Fallback: bare file input
    try:
        fi = page.locator("input[type='file']").first
        fi.set_input_files(path)
        return True
    except Exception:
        pass
    return False


class AshbyForm(BaseFormFiller):

    def prefetch(self) -> None:
        short = self.job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        self._cl_text = self.candidate.cover_letter_text(
            self.job.title, self.job.company, short=short, description=self.job.description
        )

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        # Ashby job listing pages show the description + an "Apply for this Job"
        # button.  Clicking it navigates the React app to the /application route
        # with job context loaded.  Direct navigation to /application fails (404)
        # because the React router requires that context to be in memory first.
        try:
            apply_btn = p.get_by_text("Apply for this Job", exact=False)
            apply_btn.first.wait_for(state="visible", timeout=20_000)
            apply_btn.first.click()
            # Wait for React Router to finish loading the /application route
            # (lazy-loads JS chunks; without this, subsequent .all() calls can
            # race against the navigation and get "Target page...closed")
            try:
                p.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
        except Exception:
            pass  # already on the form page

        # Wait for React to render the form fields
        try:
            p.wait_for_selector(
                "input[type='text'], input[type='email'], textarea",
                timeout=20_000,
            )
        except Exception:
            raise NeedsUserInput(
                "Ashby application form did not render — job may be closed or URL changed"
            )

        # -- Identity --------------------------------------------------------
        # Some Ashby forms use separate "First name" / "Last name" fields;
        # others (e.g. Linear) use a single "Name" field.
        import time as _time, random as _random

        filled_first = _fill_by_label(p, "First name", c.first_name, slow=True)
        filled_last = _fill_by_label(p, "Last name", c.last_name, slow=True)
        if not filled_first and not filled_last:
            try:
                # ^name matches "Name" but not "First name" / "Last name"
                el = p.get_by_label(_re.compile(r"^name", _re.IGNORECASE))
                if el.count() > 0:
                    el.first.click()
                    el.first.fill("")
                    el.first.type(f"{c.first_name} {c.last_name}", delay=_random.randint(30, 80))
            except Exception:
                pass

        _time.sleep(_random.uniform(0.3, 0.6))
        _fill_by_label(p, "Email", c.email, slow=True)
        _time.sleep(_random.uniform(0.2, 0.5))
        _fill_by_label(p, "Phone", c.phone, slow=True)

        # -- Location --------------------------------------------------------
        # "Current location" → candidate's home city
        # "Which location are you applying for?" / "Preferred location" → job's location
        _time.sleep(_random.uniform(0.2, 0.4))
        _fill_by_label(p, "Current location", c.location, slow=True)
        _fill_by_label(p, "City", "Istanbul", slow=True)
        _job_location = job.location or job.country or c.location
        _fill_by_label(p, "Which location", _job_location)
        _fill_by_label(p, "Preferred location", _job_location)
        # Ashby sometimes uses div/span labels — find by proximity to label text via JS
        try:
            p.evaluate("""(val) => {
                const labels = document.querySelectorAll('label, span, div, p');
                for (const lbl of labels) {
                    if (/which location|preferred location/i.test(lbl.innerText?.trim())) {
                        const container = lbl.closest('[class]') || lbl.parentElement;
                        const inp = container?.querySelector('input[type="text"]');
                        if (inp) { inp.focus(); inp.value = val;
                            inp.dispatchEvent(new Event('input', {bubbles:true}));
                            inp.dispatchEvent(new Event('change', {bubbles:true})); }
                        break;
                    }
                }
            }""", _job_location)
        except Exception:
            pass

        # -- Links -----------------------------------------------------------
        _time.sleep(_random.uniform(0.3, 0.6))
        _fill_by_label(p, "LinkedIn", c.linkedin_url)
        _fill_by_label(p, "GitHub", c.github_url)
        _fill_by_label(p, "Twitter", c.twitter_url)
        _fill_by_label(p, "Website", c.portfolio_url)
        _fill_by_label(p, "Portfolio", c.portfolio_url)
        if c.projects_text:
            _fill_by_label(p, "Projects", c.projects_text)

        # -- Resume ----------------------------------------------------------
        _upload_by_label(p, "Resume", str(c.resume_path))

        # -- Cover letter (file upload takes precedence over textarea) -------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)
        _cl_uploaded = False
        try:
            _cl_loc = p.get_by_label("Cover letter", exact=False)
            if _cl_loc.count() > 0:
                _cl_el = _cl_loc.first
                _is_file = _cl_el.evaluate("e => e.type === 'file'")
                if _is_file:
                    _cl_pdf = self.generate_cover_letter_pdf()
                    _cl_el.set_input_files(str(_cl_pdf))
                    _cl_uploaded = True
                    log.info("uploaded cover letter PDF for %s @ %s", job.title, job.company)
        except Exception as _exc:
            log.warning("cover letter detection failed: %s", _exc)
        if not _cl_uploaded:
            if not _fill_by_label(p, "Cover letter", cl_text):
                _fill_by_label(p, "Additional information", cl_text)

        # -- Work authorization (select dropdowns) ---------------------------
        _wa_rules = [
            ("authorized to work", "No" if not c.authorized_eu else "Yes"),
            (r"require.*sponsor", "Yes" if c.needs_sponsorship(job.country) else "No"),
            ("visa sponsor", "Yes" if c.needs_sponsorship(job.country) else "No"),
        ]
        for label_pat, answer in _wa_rules:
            try:
                for sel in p.locator("select").all():
                    sel_id = sel.get_attribute("id") or ""
                    assoc_label = ""
                    if sel_id:
                        lbl = p.locator(f"label[for='{sel_id}']")
                        if lbl.count():
                            assoc_label = lbl.first.inner_text()
                    if _re.search(label_pat, assoc_label, _re.IGNORECASE):
                        try:
                            sel.select_option(label=answer)
                        except Exception:
                            pass
            except Exception:
                pass

        # -- Yes/No button questions (Ashby boolean question groups) ---------
        # Ashby renders these as a widget with class *yesno* in the obfuscated
        # CSS module name.  The parent element contains the question label text.
        _yn_rules = [
            (_re.compile(r"us[\s\xa0]+or[\s\xa0]+canada|north[\s\xa0]+america", _re.IGNORECASE), "No"),
            (_re.compile(r"based[\s\xa0]+in.*(?:us|uk|eu|europe|ireland)", _re.IGNORECASE), "No"),
            (_re.compile(r"right[\s\xa0]+to[\s\xa0]+work|authoris?ed.*work|work.*authoriz|eligible.*work", _re.IGNORECASE), "No"),
            (_re.compile(r"visa[\s\xa0]*sponsor|require.*visa|sponsor.*requir", _re.IGNORECASE), "Yes"),
            (_re.compile(r"hybrid|office.*day|day.*office|willing.*office|work.*in.*office|in.?person.*day", _re.IGNORECASE), "Yes"),
            (_re.compile(r"willing.*relocat|open.*relocat|relocat.*willing", _re.IGNORECASE), "Yes"),
        ]
        try:
            for yn_group in p.locator("[class*='yesno']").all():
                try:
                    question_text = yn_group.evaluate(
                        "el => el.parentElement?.innerText || ''"
                    )
                    for pattern, answer in _yn_rules:
                        if pattern.search(question_text or ""):
                            btn = yn_group.get_by_text(answer, exact=True)
                            if btn.count() > 0:
                                btn.first.click()
                            break
                except Exception:
                    pass
        except Exception:
            pass

        # -- Consent / acknowledgement checkboxes and buttons -------------------
        _consent_text = _re.compile(
            r"i agree|i accept|i understand|i acknowledge|i confirm|i consent"
            r"|i have read|agree to|accept the|terms|privacy policy|gdpr",
            _re.IGNORECASE,
        )
        # Required checkboxes
        for cb in p.locator("input[type='checkbox']").all():
            try:
                if cb.is_checked():
                    continue
                is_required = cb.get_attribute("required") is not None or cb.get_attribute("aria-required") == "true"
                cb_id = cb.get_attribute("id") or ""
                label_text = ""
                if cb_id:
                    lbl = p.locator(f"label[for='{cb_id}']")
                    if lbl.count():
                        label_text = lbl.first.inner_text()
                if not label_text:
                    label_text = cb.get_attribute("aria-label") or ""
                # Ashby often has no <label for="...">; read parent/sibling text instead
                if not label_text:
                    try:
                        label_text = cb.evaluate(
                            "el => el.closest('label')?.innerText "
                            "|| el.parentElement?.innerText "
                            "|| el.parentElement?.parentElement?.innerText || ''"
                        )
                    except Exception:
                        pass
                if is_required or _consent_text.search(label_text):
                    try:
                        cb.check()
                    except Exception:
                        cb.click()
            except Exception:
                pass
        # Button-style consent (e.g. "I agree", "I understand", "Accept")
        for btn in p.locator("button").all():
            try:
                btn_text = btn.inner_text().strip()
                if _consent_text.search(btn_text):
                    btn.click()
            except Exception:
                pass

        # -- EEO (best-effort) -----------------------------------------------
        for gender_label in ["gender", "Gender identity"]:
            for _opt in ["Male", "Man", "Prefer not to say"]:
                try:
                    p.get_by_label(gender_label, exact=False).first.select_option(label=_opt)
                    break
                except Exception:
                    pass

        for pronoun_label in ["pronoun", "Pronouns"]:
            try:
                sel = p.get_by_label(pronoun_label, exact=False).first
                options = sel.evaluate(
                    "e => Array.from(e.options).map(o => ({v: o.value, t: o.text.trim()}))"
                )
                for opt in options:
                    if _re.search(r"\bhe\b", opt["t"], _re.IGNORECASE):
                        sel.select_option(value=opt["v"])
                        break
            except Exception:
                pass

        for ethnicity_label in ["race", "ethnicity", "background"]:
            try:
                el = p.get_by_label(ethnicity_label, exact=False)
                if el.count() == 0:
                    continue
                declined = False
                for opt in ["Prefer not to say", "I don't wish to answer",
                            "I do not wish to answer", "Decline to state",
                            "Decline to identify", "Choose not to disclose"]:
                    try:
                        el.first.select_option(label=opt)
                        declined = True
                        break
                    except Exception:
                        pass
                if not declined:
                    for opt in ["Other", "Other (please specify)"]:
                        try:
                            el.first.select_option(label=opt)
                            break
                        except Exception:
                            pass
            except Exception:
                pass

        # -- Required fields check — attempt to fill, raise only if truly stuck --
        _known = {
            "first", "last", "name", "email", "phone", "linkedin", "github",
            "website", "portfolio", "location", "city", "twitter",
            "cover", "letter", "resume", "projects",
        }
        try:
            required_els = p.locator("[aria-required='true'], [required]").all()
        except Exception:
            required_els = []
        for el in required_els:
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue
                if tag == "input" and (el.get_attribute("type") or "text") == "file":
                    continue
                if tag != "select" and el.input_value():
                    continue  # already filled

                # Resolve label
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
                field_name = label_text.lower()

                if any(k in field_name for k in _known):
                    continue  # already handled above

                answered = _answer_custom_field(el, tag, label_text or field_name, c, job, self.cfg)
                if not answered:
                    raise NeedsUserInput(f"Unknown required field: '{label_text or field_name}'")
            except NeedsUserInput:
                raise
            except Exception:
                pass

        # -- Walk through any paginated steps before submit ------------------
        self._walk_steps()

        # -- Submit ----------------------------------------------------------
        # Brief human-like pause before clicking submit (helps bypass spam detection)
        import time as _time, random as _random
        _time.sleep(_random.uniform(1.5, 3.0))

        # Ashby uses a plain <button> with no type='submit'; try specific then broad
        _submit_sel = (
            "button[type='submit'], "
            "button[data-button-type='submit'], "
            "button:has-text('Submit Application'), "
            "button:has-text('Submit'), "
            "button:has-text('Apply')"
        )
        self.submit(_submit_sel)
