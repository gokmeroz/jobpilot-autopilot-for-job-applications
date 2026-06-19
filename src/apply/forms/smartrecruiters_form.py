"""
SmartRecruiters ATS form filler.

URL patterns:
  https://jobs.smartrecruiters.com/{Company}/{job-id}
  https://careers.smartrecruiters.com/{Company}/...

SmartRecruiters renders a multi-section React app. The "Apply" button on the
job page navigates to an /apply sub-route where the actual form lives.
"""
from __future__ import annotations

import logging
import re

from src.apply.base import BaseFormFiller, NeedsUserInput

log = logging.getLogger(__name__)


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


def _answer_custom(page, el, tag: str, label: str, candidate, job) -> bool:
    ll = label.lower().strip()

    def _fill(v: str) -> bool:
        try:
            el.fill(str(v))
            return True
        except Exception:
            return False

    def _select(*opts: str) -> bool:
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
        return _select(*select_opts) if tag == "select" else _fill(text_val)

    if re.search(r"linkedin", ll):
        return _fill(candidate.linkedin_url)
    if re.search(r"github", ll):
        return _fill(candidate.github_url)
    if re.search(r"portfolio|website|personal\s+url", ll):
        return _fill(candidate.portfolio_url)
    if re.search(r"twitter|x\.com|x handle", ll):
        return _fill(candidate.twitter_url)

    if re.search(r"years?.*(professional|work|coding|experience)|how many years", ll):
        return _fill_or_select(str(candidate.yoe),
                               [str(candidate.yoe), "0", "1", "Less than 1", "0-1"])
    if re.search(r"how did you hear|referral|where did you (find|learn|hear)", ll):
        return _fill_or_select("Job board", ["Job board", "Online Job Board", "LinkedIn", "Other"])
    if re.search(r"notice period|when can you start|available to start", ll):
        return _fill_or_select("Immediately available",
                               ["Immediately", "0 days", "Less than 1 month", "ASAP"])
    if re.search(r"location|city|where are you based|current.*(location|country)|country", ll):
        return _fill_or_select("Istanbul, Turkey",
                               ["Turkey", "Other", "Outside United States", "International"])
    if re.search(r"salary|compensation|expected pay|desired pay", ll):
        val = candidate.salary_for(job.country)
        return _fill_or_select(val, [val])
    if re.search(r"relocation|willing to relocate|open to reloc", ll):
        return _fill_or_select("Yes", ["Yes", "Open to relocation"])
    if re.search(r"authorized.*work|eligible.*work|right to work", ll):
        ans = "No" if not candidate.authorized_eu else "Yes"
        return _fill_or_select(ans, [ans])
    if re.search(r"require.*sponsor|visa sponsor|need.*sponsor|sponsorship", ll):
        ans = "Yes" if candidate.needs_sponsorship(job.country) else "No"
        return _fill_or_select(ans, [ans])
    if re.search(r"us.*citizen|citizen.*us", ll):
        return _fill_or_select("No", ["No"])

    if tag in ("input", "textarea"):
        answer = _llm_answer(label, candidate, job)
        if answer:
            return _fill(answer)

    if tag == "select":
        try:
            opts = el.evaluate("e => Array.from(e.options).map(o => ({v: o.value, t: o.text}))")
            for opt in opts:
                if opt["v"] and opt["t"].strip() not in ("", "Select...", "Please select", "-- Select --"):
                    el.select_option(value=opt["v"])
                    return True
        except Exception:
            pass

    return False


class SmartRecruitersForm(BaseFormFiller):

    def prefetch(self) -> None:
        short = self.job.cover_letter == self.cfg["apply"]["cover_letter_short"]
        self._cl_text = self.candidate.cover_letter_text(
            self.job.title, self.job.company, short=short, description=self.job.description
        )

    def fill_form(self) -> None:
        c = self.candidate
        job = self.job
        p = self.page

        # SmartRecruiters job pages have an "Apply" button that navigates to
        # the /apply sub-route. If we land on the listing page, click it.
        try:
            apply_btn = p.locator(
                "a[href*='/apply'], button:has-text('Apply'), a:has-text('Apply for this job')"
            ).first
            if apply_btn.count() > 0:
                apply_btn.click()
        except Exception:
            pass

        try:
            p.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass

        # Wait for the form to render
        try:
            p.wait_for_selector(
                "input[name='firstName'], input[name='first_name'], input[id*='firstName' i]",
                timeout=15_000,
            )
        except Exception:
            raise NeedsUserInput(
                "SmartRecruiters form did not render — page may require login or job is closed"
            )

        # -- Identity --------------------------------------------------------
        self.fill_first(
            ["input[name='firstName']", "input[id*='firstName' i]", "input[placeholder*='First name' i]"],
            c.first_name,
        )
        self.fill_first(
            ["input[name='lastName']", "input[id*='lastName' i]", "input[placeholder*='Last name' i]"],
            c.last_name,
        )
        self.fill_first(
            ["input[name='email']", "input[type='email']"],
            c.email,
        )
        self.fill_first(
            ["input[name='phone']", "input[type='tel']", "input[id*='phone' i]"],
            c.phone,
        )

        # -- Location --------------------------------------------------------
        self.fill_first(
            ["input[name='location']", "input[id*='location' i]", "input[placeholder*='city' i]"],
            c.location,
        )

        # -- Resume ----------------------------------------------------------
        uploaded = self.upload(
            "input[type='file'][name*='resume' i], input[type='file'][name*='cv' i]",
            c.resume_path,
        )
        if not uploaded:
            self.upload("input[type='file']", c.resume_path)

        # -- Cover letter ----------------------------------------------------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)
        self.fill_first(
            [
                "textarea[name='coverLetter']",
                "textarea[name='cover_letter']",
                "textarea[placeholder*='cover letter' i]",
                "textarea[placeholder*='motivation' i]",
                "textarea[id*='coverLetter' i]",
            ],
            cl_text,
        )

        # -- Links -----------------------------------------------------------
        self.fill_first(
            ["input[name='web.LinkedIn']", "input[placeholder*='LinkedIn' i]", "input[id*='linkedin' i]"],
            c.linkedin_url,
        )
        self.fill_first(
            ["input[name='web.GitHub']", "input[placeholder*='GitHub' i]", "input[id*='github' i]"],
            c.github_url,
        )
        self.fill_first(
            ["input[name='web.Portfolio']", "input[name='web.Other']",
             "input[placeholder*='portfolio' i]", "input[placeholder*='website' i]"],
            c.portfolio_url,
        )

        # -- Work authorisation (SmartRecruiters renders these as dropdowns) --
        _wa_rules: list[tuple[str, str, list[str]]] = [
            (r"authorized.*work|right to work|work.*authoriz",
             "No" if not c.authorized_eu else "Yes",
             ["No", "Yes"]),
            (r"require.*sponsor|visa sponsor|sponsorship",
             "Yes" if c.needs_sponsorship(job.country) else "No",
             ["Yes", "No"]),
        ]
        for pattern, text_ans, select_opts in _wa_rules:
            for sel in p.locator("select").all():
                try:
                    sel_id = sel.get_attribute("id") or ""
                    label_text = ""
                    if sel_id:
                        lbl = p.locator(f"label[for='{sel_id}']")
                        if lbl.count():
                            label_text = lbl.first.inner_text()
                    if re.search(pattern, label_text, re.IGNORECASE):
                        try:
                            sel.select_option(label=text_ans)
                        except Exception:
                            for opt in select_opts:
                                try:
                                    sel.select_option(label=opt)
                                    break
                                except Exception:
                                    pass
                except Exception:
                    pass

        # -- EEO (best-effort) -----------------------------------------------
        for label_frag in ["gender", "Gender identity", "race", "ethnicity"]:
            try:
                el = p.get_by_label(label_frag, exact=False)
                if el.count() == 0:
                    continue
                for opt in ["Prefer not to say", "I don't wish to answer",
                            "Decline to state", "Choose not to disclose"]:
                    try:
                        el.first.select_option(label=opt)
                        break
                    except Exception:
                        pass
            except Exception:
                pass

        # -- Required field sweep --------------------------------------------
        _known = {
            "firstname", "lastname", "email", "phone", "location",
            "cover", "letter", "resume", "linkedin", "github", "portfolio",
            "website", "motivation",
        }
        for el in p.locator("[required], [aria-required='true']").all():
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag not in ("input", "textarea", "select"):
                    continue
                if tag == "input" and (el.get_attribute("type") or "text") == "file":
                    continue
                if tag != "select":
                    try:
                        if el.input_value():
                            continue
                    except Exception:
                        continue

                el_id = el.get_attribute("id") or el.get_attribute("name") or ""
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
                    continue

                answered = _answer_custom(p, el, tag, label_text or field_name, c, job)
                if not answered:
                    raise NeedsUserInput(f"Unknown required field: '{label_text or field_name}'")
            except NeedsUserInput:
                raise
            except Exception:
                pass

        # -- Submit ----------------------------------------------------------
        self.submit(
            "button[type='submit'][data-ui='submit-btn'], "
            "button[type='submit']:has-text('Submit'), "
            "button[type='submit']"
        )
