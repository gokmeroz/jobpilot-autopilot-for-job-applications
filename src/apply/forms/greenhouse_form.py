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


def _fill_or_select(el, tag: str, text_value: str, select_opts: list[str]) -> bool:
    """Fill a text input with text_value, or pick a select option from select_opts."""
    if tag == "select":
        return _try_select_fn(el, *select_opts)
    return _safe_fill(el, text_value)


def _try_select_fn(el, *opts: str) -> bool:
    # First: exact label match (fastest)
    for opt in opts:
        try:
            el.select_option(label=opt)
            return True
        except Exception:
            pass
    # Second: partial text match (handles numbered options like "3. Located Elsewhere")
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


def _select_react_combobox(el, page, he_pattern: str) -> bool:
    """Click a React Select combobox and pick the first option matching he_pattern."""
    try:
        el_id = el.get_attribute("id") or ""
        el.click()
        listbox_sel = f"#react-select-{el_id}-listbox [role='option']"
        page.wait_for_selector(listbox_sel, timeout=3_000)
        for opt in page.locator(listbox_sel).all():
            if re.search(he_pattern, opt.inner_text().strip(), re.IGNORECASE):
                opt.click()
                return True
    except Exception:
        pass
    return False


def _pick_react_option(el, page, *candidates: str) -> bool:
    """
    Drive a Greenhouse React Select combobox (class 'select__input') to pick
    the best matching option from *candidates*.

    Greenhouse React Select renders options as:
      <div id="react-select-{field_id}-option-N" role="option" class="select__option ...">

    We scope to the specific listbox `#react-select-{field_id}-listbox` to
    avoid picking up options from other open dropdowns or the phone flag picker.
    """
    el_id = el.get_attribute("id") or ""
    # Scoped selector: options inside THIS field's listbox
    LISTBOX_ID = f"react-select-{el_id}-listbox" if el_id else ""
    OPT_SEL = f"#{LISTBOX_ID} [role='option']" if LISTBOX_ID else "div.select__option[role='option']"
    OPEN_SEL = f"#{LISTBOX_ID}" if LISTBOX_ID else "div.select__option[role='option']"

    def _scan_and_click(candidates_list: list) -> bool:
        for opt in page.locator(OPT_SEL).all():
            try:
                txt = opt.inner_text().strip()
                tl = txt.lower()
                for candidate in candidates_list:
                    cl = candidate.lower()
                    if cl in tl or tl in cl:
                        opt.click()
                        return True
            except Exception:
                pass
        return False

    try:
        # Scroll into view and dismiss any open dropdown before starting
        el.scroll_into_view_if_needed()
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

        # Step 1: click to open, wait for options (not just the container) to render
        el.click()
        log.debug("react_select %r: clicked, waiting for options %r", el_id, OPT_SEL)
        # OPT_SEL waits for actual <div role='option'> children — more reliable than
        # OPEN_SEL which resolves as soon as the container div appears but before
        # the option elements are injected by React.
        try:
            page.wait_for_selector(OPT_SEL, timeout=5_000)
        except Exception:
            log.warning("react_select %r: listbox did not appear after click — trying force-click via JS", el_id)
            # Fallback: dispatch click event via JS in case Playwright click missed
            el.evaluate("e => e.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}))")
            page.wait_for_timeout(300)
            try:
                page.wait_for_selector(OPT_SEL, timeout=3_000)
            except Exception:
                log.warning("react_select %r: listbox still absent — skipping", el_id)
                return False
        if _scan_and_click(list(candidates)):
            return True

        # Step 2: type first candidate to filter — do NOT re-click (dropdown is open)
        if candidates:
            for char in candidates[0]:
                el.type(char)
                page.wait_for_timeout(25)
            page.wait_for_timeout(400)
            try:
                page.wait_for_selector(OPT_SEL, timeout=2_000)
                if _scan_and_click(list(candidates)):
                    return True
                # Take first filtered result
                first = page.locator(OPT_SEL).first
                if first.count():
                    first.click()
                    return True
            except Exception:
                pass

        # Step 3: clear filter, re-open, click first option
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        el.click()
        try:
            page.wait_for_selector(OPT_SEL, timeout=2_000)
        except Exception:
            return False
        first = page.locator(OPT_SEL).first
        if first.count():
            first.click()
            return True
    except Exception as exc:
        log.warning("react_select %r: exception — %s", el_id, exc)
    return False


def _answer_custom_question(label: str, el, candidate, job, page=None) -> bool:
    """Try to fill a custom Greenhouse question field. Returns True if handled."""
    ll = label.lower().strip()
    tag = el.evaluate("e => e.tagName.toLowerCase()")
    el_type = (el.get_attribute("type") or "text").lower()

    # Detect Greenhouse React Select combobox (class "select__input").
    # These look like text inputs but require clicking + option picking.
    is_react_select = "select__input" in (el.get_attribute("class") or "")

    def _try_select(*opts: str) -> bool:
        return _try_select_fn(el, *opts)

    def _react_pick(*opts: str) -> bool:
        """Route to React Select picker when available, else fall back."""
        if is_react_select and page:
            return _pick_react_option(el, page, *opts)
        return _try_select_fn(el, *opts)

    def _fill_or_select_or_react(text_value: str, select_opts: list[str]) -> bool:
        if is_react_select:
            return _react_pick(*select_opts) if select_opts else _safe_fill(el, text_value)
        if tag == "select":
            return _try_select_fn(el, *select_opts)
        return _safe_fill(el, text_value)

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
        return _fill_or_select_or_react(str(candidate.yoe),
                               [str(candidate.yoe), "0", "1", "Less than 1", "Less than 2", "0-1"])
    if re.search(r"how did you hear|referred by|referral source|where did you (find|learn|hear)", ll):
        return _fill_or_select_or_react("Job board",
                               ["Job board", "LinkedIn", "Online Job Board", "Other"])
    if re.search(r"notice period|when can you start|available to start|earliest start|start date", ll):
        return _fill_or_select_or_react("Immediately available",
                               ["Immediately", "0 days", "Less than 1 month", "ASAP"])
    if re.search(r"salary|compensation|expected pay|desired pay|pay expectation|ctc", ll):
        val = candidate.salary_for(job.country)
        return _fill_or_select_or_react(val, [val])
    # Work-auth and sponsorship checks BEFORE location — "your current location" appears
    # in questions like "require sponsorship to remain in your current location?"
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
        return _fill_or_select_or_react(ans, [ans])
    if re.search(r"visa sponsor|require sponsor|need sponsor|sponsorship|work visa", ll):
        ans = "Yes" if candidate.needs_sponsorship(job.country) else "No"
        return _fill_or_select_or_react(ans, [ans])
    if re.search(r"preferred name|name.*prefer|prefer.*name|what name|name.*use|call you", ll):
        return _safe_fill(el, candidate.first_name)
    if re.search(r"previously worked|worked (at|for)|consulted for", ll):
        return _fill_or_select_or_react("No", ["No"])
    if re.search(r"employment agreement|post.?employment|non.?compete|restrictive covenant", ll):
        return _fill_or_select_or_react("No", ["No"])
    if re.search(r"proficien|fluenc|experienc|familiar|comfort|knowledge|skill|expert", ll):
        return _fill_or_select_or_react("Yes", ["Yes"])
    if re.search(r"location|city|where are you (currently )?based|current location|where do you live"
                 r"|country.*located|located.*country|country.*resid|resid.*country"
                 r"|choose.*country|current country|country of residence|currently based", ll):
        return _fill_or_select_or_react("Istanbul, Turkey",
                               ["Turkey", "Located Elsewhere", "Other",
                                "Outside United States", "International", "Europe"])
    if re.search(r"relocation|willing to relocate|open to reloc|relocate for", ll):
        return _fill_or_select_or_react("Yes",
                               ["Yes", "Open to relocation", "Yes, willing to relocate"])
    if re.search(r"open to remote|work remotely|remote work|remote position", ll):
        return _fill_or_select_or_react("Yes", ["Yes", "Remote"])

    # --- EEO fields — always select, never type ---
    if re.search(r"pronoun", ll):
        if tag == "select":
            try:
                options = el.evaluate(
                    "e => Array.from(e.options).map(o => ({v: o.value, t: o.text.trim()}))"
                )
                for opt in options:
                    if re.search(r"\bhe\b", opt["t"], re.IGNORECASE):
                        el.select_option(value=opt["v"])
                        return True
            except Exception:
                pass
        elif (el.get_attribute("role") or "") == "combobox" and page:
            return _select_react_combobox(el, page, r"\bhe\b")
        return False  # don't type pronouns into a text field

    if re.search(r"gender", ll):
        if tag == "select":
            return _try_select("Prefer not to say", "Prefer Not to Say", "Non-binary / third gender")
        return False

    if re.search(r"race|ethnicity|background", ll):
        if tag == "select":
            return _try_select(
                "Prefer not to say", "I don't wish to answer",
                "I do not wish to answer", "Decline to state",
                "Decline to identify", "Choose not to disclose", "Other",
            )
        return False

    # --- LLM fallback for open text/textarea (not React Select comboboxes) ---
    if not is_react_select and tag in ("input", "textarea") and el_type not in ("file", "hidden", "checkbox", "radio"):
        answer = _llm_answer_question(label, candidate, job)
        if answer:
            return _safe_fill(el, answer)

    # --- React Select fallback: type nothing, pick first option ---
    if is_react_select and page:
        return _pick_react_option(el, page, "Yes", "No")

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
        # Wait for React to settle after the standard field fills above — React Select
        # components can be in a transitional state immediately after focus moves away
        # from a prior field, causing click-to-open to fail silently.
        p.wait_for_timeout(600)
        for label_el in p.query_selector_all("label[for^='question_']"):
            try:
                q_id = label_el.get_attribute("for") or ""
                label_text = label_el.inner_text().strip()
                if not q_id or not label_text:
                    continue
                # IDs containing [] (Greenhouse multi-select checkbox groups) produce
                # invalid CSS selectors like #question_123[]_456 — skip them; they are
                # individual checkbox options, not the question container.
                if "[" in q_id or "]" in q_id:
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
                answered = _answer_custom_question(label_text, field_el, c, job, page=p)
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
                try:
                    options = sel.evaluate(
                        "e => Array.from(e.options).map(o => ({v: o.value, t: o.text.trim()}))"
                    )
                    for opt in options:
                        if re.search(r"\bhe\b", opt["t"], re.IGNORECASE):
                            sel.select_option(value=opt["v"])
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
        # Greenhouse new board uses React — aria-disabled must be false before clicking.
        # Wait up to 5s for the submit button to become enabled after React processes
        # the last field fill, then use Playwright's Locator API (not ElementHandle)
        # which correctly handles aria-disabled and synthesizes the full interaction.
        if not self.dry_run:
            p.wait_for_timeout(300)
            btn_sel = "#submit_app, button[type='submit']"
            try:
                # Wait for aria-disabled to clear
                p.wait_for_function(
                    """() => {
                        const btn = document.querySelector('button[type="submit"]')
                                 || document.querySelector('#submit_app');
                        return btn && btn.getAttribute('aria-disabled') !== 'true';
                    }""",
                    timeout=5_000,
                )
            except Exception:
                disabled = p.evaluate(
                    """() => {
                        const btn = document.querySelector('button[type="submit"]')
                                 || document.querySelector('#submit_app');
                        return btn ? btn.getAttribute('aria-disabled') : 'not-found';
                    }"""
                )
                log.warning("submit button still aria-disabled=%r — clicking anyway", disabled)

            btn_locator = p.locator(btn_sel).first
            btn_locator.scroll_into_view_if_needed()
            btn_locator.click()
            log.info("submit clicked for %s @ %s", self.job.title, self.job.company)
        else:
            log.info("dry_run=True — skipping submit for %s @ %s", self.job.title, self.job.company)
