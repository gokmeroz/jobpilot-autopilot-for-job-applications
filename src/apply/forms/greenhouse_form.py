"""
Greenhouse ATS form filler.

Standard form: https://boards.greenhouse.io/{token}/jobs/{id}
Company-embedded variant: apply URL redirects here after JS navigation.
"""
from __future__ import annotations

import logging
import re

from src.apply.base import BaseFormFiller, NeedsUserInput

log = logging.getLogger(__name__)


def _safe_fill(el, value: str) -> bool:
    try:
        el.fill(str(value))
        return True
    except Exception:
        return False


def _llm_answer_question(label: str, candidate, job) -> str:
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
            f"Experience: {candidate.yoe} year(s) professional\n"
            f"Education: {candidate.education}\n"
            f"Skills: Node.js, TypeScript, React, Python, MongoDB, AWS, AI/LLM integration\n"
            f"Recent: Nummoria (AI personal finance SaaS, full-stack co-founder)\n"
            f"Previous: Halkbank internship (Angular/.NET), Eyehub TÜBİTAK project (Node.js/AWS)\n"
            f"LinkedIn: {candidate.linkedin_url}\n"
            f"GitHub: {candidate.github_url}\n"
            f"Portfolio: {candidate.portfolio_url}"
        )
        prompt = (
            f"You are filling a job application for the candidate below.\n"
            f"Answer the following question concisely and professionally.\n\n"
            f"{profile}\n\n"
            f"Question: {label}\n\n"
            f"Rules:\n"
            f"- 1-3 sentences max unless clearly more is needed\n"
            f"- Factual and specific — never invent credentials\n"
            f"- Do not start with 'I would like to...' — be direct\n"
            f"- Return ONLY the answer text, nothing else"
        )
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.warning("LLM answer failed for %r: %s", label, exc)
        return ""


def _answer_custom_question(label: str, el, candidate, job) -> bool:
    """Try to fill a custom Greenhouse question field. Returns True if handled."""
    ll = label.lower().strip()
    tag = el.evaluate("e => e.tagName.toLowerCase()")
    el_type = (el.get_attribute("type") or "text").lower()

    def _try_select(*opts: str) -> bool:
        for opt in opts:
            try:
                el.select_option(label=opt)
                return True
            except Exception:
                pass
        return False

    # --- URL fields ---
    if re.search(r"linkedin", ll):
        return _safe_fill(el, candidate.linkedin_url)
    if re.search(r"github|git hub", ll):
        return _safe_fill(el, candidate.github_url)
    if re.search(r"portfolio|personal (site|url|website)|your website", ll):
        return _safe_fill(el, candidate.portfolio_url)
    if re.search(r"twitter|x\.com|x handle", ll):
        return _safe_fill(el, candidate.twitter_url)

    # --- Factual lookups ---
    if re.search(r"years?.*(professional|work|coding|programming|software|dev)|how many years", ll):
        return _safe_fill(el, candidate.yoe)
    if re.search(r"how did you hear|referred by|referral source|where did you (find|learn|hear)", ll):
        if tag == "select":
            return _try_select("Job board", "LinkedIn", "Online Job Board", "Other")
        return _safe_fill(el, "Job board")
    if re.search(r"notice period|when can you start|available to start|earliest start|start date", ll):
        if tag == "select":
            return _try_select("Immediately", "0 days", "Less than 1 month", "ASAP")
        return _safe_fill(el, "Immediately available")
    if re.search(r"salary|compensation|expected pay|desired pay|pay expectation|ctc", ll):
        if tag == "select":
            return _try_select(candidate.salary_for(job.country))
        return _safe_fill(el, candidate.salary_for(job.country))
    if re.search(r"location|city|where are you based|current location|where do you live", ll):
        return _safe_fill(el, "Istanbul, Turkey")
    if re.search(r"relocation|willing to relocate|open to reloc|relocate for", ll):
        if tag == "select":
            return _try_select("Yes", "Open to relocation", "Yes, willing to relocate")
        return _safe_fill(el, "Yes")
    if re.search(r"open to remote|work remotely|remote work|remote position", ll):
        if tag == "select":
            return _try_select("Yes", "Remote")
        return _safe_fill(el, "Yes")
    if re.search(r"authorized.*(work|employ)|right to work|eligible to work|work authorization", ll):
        country = (job.country or "").upper()
        if country in ("DE", "NL", "IE", "AT"):
            ans = "Yes" if candidate.authorized_eu else "No"
        elif country in ("GB", "UK"):
            ans = "Yes" if candidate.authorized_uk else "No"
        elif country == "US":
            ans = "Yes" if candidate.authorized_us else "No"
        else:
            ans = "No"
        if tag == "select":
            return _try_select(ans)
        return _safe_fill(el, ans)
    if re.search(r"visa sponsor|require sponsor|need sponsor|sponsorship|work visa", ll):
        ans = "Yes" if candidate.needs_sponsorship(job.country) else "No"
        if tag == "select":
            return _try_select(ans)
        return _safe_fill(el, ans)

    # --- LLM fallback for open text/textarea ---
    if tag in ("input", "textarea") and el_type not in ("file", "hidden", "checkbox", "radio"):
        answer = _llm_answer_question(label, candidate, job)
        if answer:
            return _safe_fill(el, answer)

    # --- Select fallback: pick first non-empty non-placeholder option ---
    if tag == "select":
        try:
            options = el.evaluate(
                "e => Array.from(e.options).map(o => ({v: o.value, t: o.text}))"
            )
            for opt in options:
                if opt["v"] and opt["t"] and opt["t"].strip() not in ("", "Select...", "Please select"):
                    el.select_option(value=opt["v"])
                    return True
        except Exception:
            pass

    return False


class GreenhouseForm(BaseFormFiller):

    def prefetch(self) -> None:
        short = self.job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        self._cl_text = self.candidate.cover_letter_text(
            self.job.title, self.job.company, short=short, description=self.job.description
        )

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        p.wait_for_load_state("networkidle", timeout=20_000)

        # Guard: companies with custom career portals redirect away from
        # greenhouse.io and their form won't have #first_name — catch early.
        if "greenhouse.io" not in p.url and not p.query_selector("#first_name"):
            raise NeedsUserInput(
                f"Redirected to custom career portal ({p.url}) — apply manually"
            )

        # -- Identity --------------------------------------------------------
        self.fill("#first_name", c.first_name)
        self.fill("#last_name", c.last_name)
        self.fill("#email", c.email)
        self.fill("#phone", c.phone)

        # -- Location --------------------------------------------------------
        # Greenhouse v2 uses #candidate-location; classic boards use #location
        self.fill_first([
            "#candidate-location",
            "#location",
            "input[id*='location' i]",
            "input[placeholder*='city' i]",
            "input[placeholder*='location' i]",
        ], "Istanbul, Turkey")

        # -- Resume ----------------------------------------------------------
        uploaded = self.upload("input#resume", c.resume_path)
        if not uploaded:
            # Some Greenhouse boards use a different file input
            self.upload("input[name='resume']", c.resume_path)

        # -- Cover letter ----------------------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)

        self.fill_first([
            "#cover_letter_text",
            "textarea[name='job_application[cover_letter]']",
            "textarea[aria-label*='cover letter' i]",
        ], cl_text)

        # -- Links -----------------------------------------------------------
        self.fill_first(
            ["#job_application_linkedin_url", "input[id*='linkedin' i]", "input[name*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["#job_application_github_url", "input[id*='github' i]", "input[name*='github' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[id*='website' i]", "input[id*='portfolio' i]", "input[name*='website' i]"],
            c.portfolio_url,
        )

        # -- Custom questions (question_XXXXXXXXX and other company fields) ---
        # Greenhouse lets companies add per-job questions; we answer them via
        # pattern matching first, then LLM fallback for open-ended text fields.
        for label_el in p.query_selector_all("label[for^='question_']"):
            try:
                q_id = label_el.get_attribute("for") or ""
                label_text = label_el.inner_text().strip()
                if not q_id or not label_text:
                    continue
                field_el = p.query_selector(f"#{q_id}")
                if not field_el:
                    continue
                # Skip if already filled
                try:
                    if field_el.input_value():
                        continue
                except Exception:
                    pass
                answered = _answer_custom_question(label_text, field_el, c, job)
                if not answered:
                    log.warning("unanswered custom question: %r", label_text)
            except Exception as exc:
                log.warning("error on custom question: %s", exc)

        # -- Work authorization ----------------------------------------------
        # "Are you authorized to work in [country]?"
        # Greenhouse renders these as <select> with options Yes/No
        for sel in p.query_selector_all("select"):
            label_el = p.query_selector(f"label[for='{sel.get_attribute('id')}']")
            label_text = (label_el.inner_text() if label_el else "").lower()

            if "authorized to work" in label_text or "right to work" in label_text:
                if "eu" in label_text or any(c in label_text for c in ["germany", "netherlands", "ireland"]):
                    sel.select_option(label="No") if not self.candidate.authorized_eu else sel.select_option(label="Yes")
                elif "uk" in label_text or "united kingdom" in label_text:
                    sel.select_option(label="No") if not self.candidate.authorized_uk else sel.select_option(label="Yes")
                elif "us" in label_text or "united states" in label_text:
                    sel.select_option(label="No") if not self.candidate.authorized_us else sel.select_option(label="Yes")

            elif "sponsor" in label_text or "visa" in label_text:
                # "Will you now or in future require sponsorship?"
                needs = self.candidate.needs_sponsorship(job.country)
                sel.select_option(label="Yes" if needs else "No")

        # -- EEO (optional, best-effort) -------------------------------------
        for sel in p.query_selector_all("select"):
            label_el = p.query_selector(f"label[for='{sel.get_attribute('id')}']")
            label_text = (label_el.inner_text() if label_el else "").lower()

            if "gender" in label_text:
                try:
                    sel.select_option(label="Prefer not to say")
                except Exception:
                    pass
            elif "pronoun" in label_text:
                for opt in ["He/Him", "He/his", "he/him", "he/his"]:
                    try:
                        sel.select_option(label=opt)
                        break
                    except Exception:
                        pass
            elif "race" in label_text or "ethnicity" in label_text or "background" in label_text:
                # Prefer decline; only fall back to "Other" if no decline option exists
                declined = False
                for opt in ["Prefer not to say", "I don't wish to answer",
                            "I do not wish to answer", "Decline to state",
                            "Decline to identify", "Choose not to disclose"]:
                    try:
                        sel.select_option(label=opt)
                        declined = True
                        break
                    except Exception:
                        pass
                if not declined:
                    for opt in ["Other", "Other (please specify)"]:
                        try:
                            sel.select_option(label=opt)
                            break
                        except Exception:
                            pass
            elif "veteran" in label_text:
                try:
                    sel.select_option(label="I am not a protected veteran")
                except Exception:
                    pass
            elif "disability" in label_text:
                try:
                    sel.select_option(label="I don't wish to answer")
                except Exception:
                    sel.select_option(label="I do not wish to answer")

        # -- Unknown required fields → flag for manual review ----------------
        _known_ids = {
            "first_name", "last_name", "email", "phone",
            "resume", "cover_letter", "linkedin", "github",
            "website", "portfolio", "twitter",
            "location", "candidate-location", "city", "country",
        }
        _known_labels = {
            "name", "email", "phone", "location", "city", "country", "address",
            "linkedin", "github", "website", "portfolio", "resume", "cover",
            "twitter", "zip", "postal", "pronouns", "gender", "ethnicity",
            "race", "veteran", "disability", "salary", "compensation",
        }
        for el in p.query_selector_all("input[required], textarea[required], select[required]"):
            el_type = el.get_attribute("type") or "text"
            if el_type == "file":
                continue
            val = el.input_value()
            if val:
                continue  # already filled

            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            identifier = el_id or el_name

            if any(k in identifier.lower() for k in _known_ids):
                continue

            # Resolve a human-readable label
            label_text = ""
            if el_id:
                label_el = p.query_selector(f"label[for='{el_id}']")
                if label_el:
                    label_text = label_el.inner_text()
            if not label_text:
                label_text = (
                    el.get_attribute("aria-label")
                    or el.get_attribute("placeholder")
                    or ""
                )

            # Skip if label maps to a known concept we handle
            if any(k in label_text.lower() for k in _known_labels):
                continue

            # Skip unidentifiable elements (UI widgets with no id/name/label)
            if not identifier and not label_text:
                continue

            raise NeedsUserInput(f"Unknown required field: '{label_text or identifier}'")

        # -- Submit ----------------------------------------------------------
        self.submit("#submit_app, button[type='submit']")
