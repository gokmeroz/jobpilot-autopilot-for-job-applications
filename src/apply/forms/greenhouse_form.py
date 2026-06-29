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
        # page may be a Frame (no .keyboard) when form is inside an iframe;
        # keyboard events target the currently focused element regardless of frame.
        if hasattr(page, "keyboard"):
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
        if hasattr(page, "keyboard"):
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


def _answer_custom_question(label: str, el, candidate, job, page=None, cfg: dict | None = None) -> bool:
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
        if el_type == "checkbox":
            try:
                if not el.is_checked():
                    el.check()
                return True
            except Exception:
                try:
                    el.click()
                    return True
                except Exception:
                    return False
        return _safe_fill(el, text_value)

    # --- Checkbox questions (GDPR, consent, certify, agree) — check the box ---
    if el_type == "checkbox":
        try:
            if not el.is_checked():
                el.check()
            return True
        except Exception:
            try:
                el.click()
                return True
            except Exception:
                return False

    # --- Cover letter file attachment ------------------------------------
    if re.search(r"cover.?letter|motivation.*letter|letter.*motivation", ll):
        if el_type == "file":
            # PDF path is set by the filler when it processes the custom questions loop;
            # if it reaches here it means the loop's early-exit didn't fire — upload resume.
            try:
                el.set_input_files(str(candidate.resume_path))
                return True
            except Exception:
                pass
        return False

    # --- File attach (extra doc upload) ---
    if re.search(r"\battach\b|attach.*file|attach.*doc|upload.*doc", ll):
        if el_type == "file":
            try:
                el.set_input_files(str(candidate.resume_path))
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
        return _fill_or_select_or_react(str(candidate.yoe),
                               [str(candidate.yoe), "0", "1", "Less than 1", "Less than 2", "0-1"])
    if re.search(r"how did you hear|referred by|referral source|where did you (find|learn|hear)", ll):
        return _fill_or_select_or_react("Careers Website",
                               ["Careers Website", "Career Website", "Company Website",
                                "LinkedIn", "Job board", "Online Job Board"])
    if re.search(r"notice period|when can you start|available to start|earliest start|start date", ll):
        return _fill_or_select_or_react("Immediately available",
                               ["Immediately", "0 days", "Less than 1 month", "ASAP"])
    if re.search(r"salary|compensation|expected pay|desired pay|pay expectation|ctc", ll):
        val = candidate.salary_for(job.country)
        return _fill_or_select_or_react(val, [val])
    # Work-auth and sponsorship checks BEFORE location — "your current location" appears
    # in questions like "require sponsorship to remain in your current location?"
    # "are you based/located in [city]?" — yes/no about a specific location
    if re.search(r"are you (currently )?(based|located|living|residing|working) in\b", ll):
        # If the question names Istanbul or Turkey → Yes; any other city → No
        if re.search(r"istanbul|turkey|türkiye", ll):
            return _fill_or_select_or_react("Yes", ["Yes"])
        return _fill_or_select_or_react("No", ["No", "No, I am not"])

    # "on-site / office-first" willingness — candidate is open to relocating
    if re.search(r"work on.?site|office.first|able to work.*office|work.*from.*office"
                 r"|willing.*office|office.*day|day.*office|hybrid.*office|office.*hybrid"
                 r"|in.?person.*day|days.*per.*week.*office|office.*days.*week", ll):
        return _fill_or_select_or_react("Yes", ["Yes", "I am", "I can", "I agree"])

    # British spelling "authorised" as well as American "authorized"
    if re.search(r"authoris?ed.*(work|employ)|right to work|eligible to work|work authori[sz]ation", ll):
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
    if re.search(r"preferred.*working.*location|preferred.*work.*location|work.*location.*prefer"
                 r"|which.*location.*prefer|prefer.*office.*location|preferred.*office", ll):
        # Pick the job city, defaulting to the most common options
        _loc_city = (job.location or "").split(",")[0].strip() if job.location else ""
        _loc_opts = [_loc_city] if _loc_city else []
        _loc_opts += ["Berlin", "Barcelona", "Vienna", "London", "Amsterdam", "Remote",
                      "Flexible", "Other"]
        return _fill_or_select_or_react(_loc_city or "Berlin", _loc_opts)
    if re.search(r"relative|domestic partner|family member.*employ|employ.*family"
                 r"|friends.*employ|employ.*friend|personal relationship.*employ"
                 r"|currently working for|work.*for n26|n26.*employ|conflict.*interest", ll):
        return _fill_or_select_or_react("No", ["No", "None", "I do not have", "N/A"])
    if re.search(r"previously worked|worked (at|for)|consulted for", ll):
        return _fill_or_select_or_react("No", ["No"])
    if re.search(r"employment agreement|post.?employment|non.?compete|restrictive covenant", ll):
        return _fill_or_select_or_react("No", ["No"])
    if re.search(r"proficien|fluenc|experienc|familiar|comfort|knowledge|skill|expert", ll):
        return _fill_or_select_or_react("Yes", ["Yes"])
    if re.search(r"location.*city|city.*location|location \(city\)", ll):
        # React autocomplete (Google Places): type then accept
        if page:
            try:
                el.click()
                el.fill("")
                el.type("Istanbul", delay=40)
                page.wait_for_timeout(900)
                sug = page.query_selector(
                    ".pac-item, [class*='suggestion'], [class*='option'][role='option']"
                )
                if sug:
                    sug.click()
                else:
                    el.press("Tab")
                return True
            except Exception:
                pass
        return _safe_fill(el, "Istanbul, Turkey")
    if re.search(r"location|city|where are you (currently )?based|current location|where do you live"
                 r"|country.*located|located.*country|country.*resid|resid.*country"
                 r"|choose.*country|current country|country of residence|currently based", ll):
        return _fill_or_select_or_react("Istanbul, Turkey",
                               ["Istanbul", "Turkey", "Türkiye", "Located Elsewhere",
                                "Outside United States", "International", "Europe",
                                "Other"])
    if re.search(r"(currently|presently).*(work|employed|based)|which country.*(work|current|employ)"
                 r"|country.*do you (currently|presently)|where.*(currently|presently).*(work|employ)", ll):
        return _fill_or_select_or_react("Turkey",
                               ["Turkey", "Türkiye", "Other", "Outside United States", "Europe"])
    if re.search(r"relocation|willing to relocate|open to reloc|relocate for", ll):
        return _fill_or_select_or_react("Yes",
                               ["Yes", "Open to relocation", "Yes, willing to relocate"])
    if re.search(r"open to remote|work remotely|remote work|remote position", ll):
        return _fill_or_select_or_react("Yes", ["Yes", "Remote"])
    if re.search(r"time.?zone|timezone", ll):
        return _fill_or_select_or_react("UTC+3",
                               ["UTC+3", "UTC +3", "GMT+3", "Europe/Istanbul",
                                "(UTC+03:00)", "Other", "Asia/Istanbul"])
    if re.search(r"in.person|travel.*required|onsite.*require|meet.*in.person|colleagues.*meet", ll):
        return _fill_or_select_or_react("Yes", ["Yes", "I agree", "I understand", "I accept"])
    if re.search(r"high school|secondary school|gcse|a.level|mathematics.*school|school.*maths"
                 r"|native language.*school|school.*language|school.*performance", ll):
        return _fill_or_select_or_react("A grade",
                               ["A", "A+", "A grade", "Excellent", "High Distinction",
                                "Distinction", "Above Average", "Outstanding", "Top"])
    if re.search(r"(degree|bachelor|university|college).*(result|grade|class|gpa)"
                 r"|what.*gpa|cumulative.*gpa|degree classification", ll):
        return _fill_or_select_or_react("Distinction",
                               ["First Class Honours", "First", "High Distinction",
                                "Distinction", "2:1", "Merit", "3.5", "3.7", "A"])
    if re.search(r"\bagree\b|\bconfirm\b|\baccept\b|\backnowledge\b|\bcertif"
                 r"|privacy notice|terms|consent|declaration", ll):
        return _fill_or_select_or_react("Yes",
                               ["Yes", "I agree", "I confirm", "I accept", "Agree",
                                "I acknowledge", "I have read"])
    if re.search(r"nationality|citizen|passport", ll):
        return _fill_or_select_or_react("Turkish",
                               ["Turkey", "Turkish", "Türkiye", "Other"])
    if re.search(r"from where.*intend.*work|intend.*work.*from|where.*work.*from|where.*plan.*work"
                 r"|work.*location.*intent|intended.*work.*location", ll):
        return _fill_or_select_or_react("Istanbul, Turkey (Remote)",
                               ["Remote", "Turkey", "Other", "International", "Outside US"])

    # OFAC / export-control countries question — always "No"
    if re.search(r"cuba|iran\b|north korea|syria|crimea|ofac|sanctioned countr|restricted countr"
                 r"|citizen.*resident.*(?:cuba|iran|north korea|syria|crimea)"
                 r"|(?:cuba|iran|north korea|syria|crimea).*citizen", ll):
        return _fill_or_select_or_react("No", ["No", "None of the above"])

    # "Which US state will you reside / work from?" — candidate is in Turkey
    if re.search(r"which state.*reside|which state.*work|state.*plan.*reside"
                 r"|state do you plan|state.*will you.*work|which (us )?state", ll):
        # Try non-US options first; if not available raise for manual handling
        if is_react_select and page:
            picked = _pick_react_option(el, page,
                "Outside the United States", "Outside US", "International",
                "I do not reside in a US state", "Other")
            if picked:
                return True
        raise NeedsUserInput(
            f"US state residency question requires manual answer: {label!r}"
        )
    if re.search(r"why.*want.*join|why.*join|why.*apply|what.*excites|why.*interest|why.*work (at|for|with)", ll):
        answer = _llm_answer_question(label, candidate, job)
        return _safe_fill(el, answer) if answer else False
    if re.search(r"current.*company|most recent company|current employer|last employer|present company", ll):
        return _safe_fill(el, "Nummoria")
    if re.search(r"email.*future|future.*job|job.*alert|future.*opening|notify.*job", ll):
        return _fill_or_select_or_react("Yes", ["Yes", "Opt in", "Subscribe"])
    if re.search(r"hybrid|office.*model|work from.*office|willing.*office|office.*willing", ll):
        return _fill_or_select_or_react("Yes", ["Yes", "I am willing", "Willing"])
    if re.search(r"full.time.*engineer|professional.*setting|maintained.*web|built.*web|software engineer.*professional", ll):
        return _fill_or_select_or_react("Yes", ["Yes"])
    if re.search(r"internal tools|developer platform|build.*internal|experience.*tools", ll):
        return _fill_or_select_or_react("No", ["No", "N/A"])

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
            return _try_select("Male", "Man", "Prefer not to say", "Prefer Not to Say", "Non-binary / third gender")
        if is_react_select and page:
            return _react_pick("Male", "Man", "Prefer not to say")
        return False

    if re.search(r"race|ethnicity|ethnic background", ll):
        # Always decline — NEVER pick an actual racial/ethnic group.
        # Do NOT include "Other" here: partial match hits "Native Hawaiian or Other…".
        _decline_race = [
            "Decline to Self-Identify", "Decline to self-identify",
            "Decline to State", "Decline to state",
            "Decline to identify",
            "I prefer not to disclose", "I prefer not to answer",
            "Prefer not to say", "Prefer not to disclose",
            "I don't wish to answer", "I do not wish to answer",
            "I wish not to identify", "Choose not to disclose",
        ]
        if tag == "select":
            return _try_select(*_decline_race)
        if is_react_select and page:
            return _react_pick("Decline", "Prefer not", "not to answer")
        return False  # never type a race into a text field

    if re.search(r"disabilit|disabled", ll):
        # Always select "no disability" or the decline option — never invent one.
        _no_disab = [
            "No, I Don't Have a Disability",
            "No, I don't have a disability",
            "No, I do not have a disability",
            "I don't have a disability",
            "I do not have a disability",
            "Not disabled", "Not Disabled", "No disability",
            "No",
            "I prefer not to answer", "I don't wish to answer",
            "Prefer not to disclose", "Decline to state",
        ]
        if tag == "select":
            return _try_select(*_no_disab)
        if is_react_select and page:
            return _react_pick("No, I don", "not have", "No disability", "prefer not")
        return False  # never type disability status

    if re.search(r"veteran|protected veteran|military service|armed forces", ll):
        # Candidate has no military service.
        _not_vet = [
            "I am not a protected veteran",
            "I am not a veteran",
            "Not a Protected Veteran", "Not a protected veteran",
            "Not a Veteran", "Not a veteran",
            "I am not a veteran or a separated service member",
            "I don't wish to answer", "I prefer not to answer",
            "Prefer not to disclose", "Decline to state",
            "No",
        ]
        if tag == "select":
            return _try_select(*_not_vet)
        if is_react_select and page:
            return _react_pick("not a protected veteran", "not a veteran", "prefer not")
        return False  # never type veteran status

    # --- LLM fallback for open text/textarea (not React Select comboboxes) ---
    if not is_react_select and tag in ("input", "textarea") and el_type not in ("file", "hidden", "checkbox", "radio"):
        answer = _llm_answer_question(label, candidate, job)
        if answer:
            return _safe_fill(el, answer)

    # --- React Select fallback: try Yes/No — never type long LLM text into a dropdown ---
    if is_react_select and page:
        return _pick_react_option(el, page, "Yes", "No")

    # --- Select fallback: opt-in only. Unknown dropdowns are too risky to guess.
    allow_select_fallback = bool((cfg or {}).get("apply", {}).get("allow_select_fallback", False))
    if tag == "select" and allow_select_fallback:
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

        try:
            p.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            # Some boards (e.g. SumUp) keep persistent WebSocket connections and
            # never reach networkidle. Fall through and wait for the form field.
            pass

        # Guard: companies like SumUp and N26 embed the Greenhouse form in an
        # iframe on their own domain (URL never becomes greenhouse.io).
        # Strategy:
        #   1. Click the page's "Apply" button to activate the embedded form.
        #   2. Wait for a greenhouse.io frame to appear in the browser context.
        #   3. If the frame shows the job description first, click "Apply" again
        #      inside the frame to reach the application form.
        #   4. Switch self.page to the frame so all subsequent fills target it.
        #      (self._parent_page keeps the original Page for screenshots/keyboard.)
        # --- Cookie / consent popup dismissal (host pages like N26) ----------
        # Must happen BEFORE clicking "Apply for this position" — overlaying
        # consent banners block Playwright clicks on elements underneath them.
        _CONSENT_BTNS = [
            "Accept All", "Accept all", "Accept all cookies",
            "Agree", "Agree to all", "Allow All", "Allow all",
            "OK", "Got it", "I Accept", "I agree",
            "Close", "Dismiss",
        ]
        try:
            for _ct in _CONSENT_BTNS:
                _cb = p.get_by_role("button", name=_ct, exact=False).first
                if _cb.count() > 0 and _cb.is_visible(timeout=500):
                    _cb.click()
                    p.wait_for_timeout(800)
                    log.debug("dismissed cookie popup via %r", _ct)
                    break
        except Exception:
            pass

        if "greenhouse.io" not in p.url and not p.query_selector("#first_name"):
            _on_form  = False
            _parent   = p   # original Page — needed for screenshots & keyboard

            # Step 1: click the visible "Apply" button on the host page.
            # Snapshot pages BEFORE clicking so we can detect new tabs.
            try:
                _pages_before = set(_parent.context.pages)
            except Exception:
                _pages_before = set()
            for _apply_text in [
                "Apply now", "Apply Now",
                "Apply for this Job", "Apply for this job",
                "Apply to this position", "Apply for this position",
            ]:
                try:
                    _btn = p.get_by_text(_apply_text, exact=False).first
                    if _btn.count() > 0 and _btn.is_visible():
                        _btn.click()
                        p.wait_for_timeout(2_000)
                        break
                except Exception:
                    continue

            # Step 2a: check if the button opened a new browser tab (N26-style).
            # Some companies open the embedded Greenhouse form in a fresh tab rather
            # than an iframe, so we detect new pages in the browser context.
            p.wait_for_timeout(2_000)
            if not _on_form:
                try:
                    _new_pages = [
                        pg for pg in _parent.context.pages
                        if pg not in _pages_before
                    ]
                    for _new_page in _new_pages:
                        try:
                            _new_page.wait_for_load_state("domcontentloaded", timeout=12_000)
                        except Exception:
                            pass
                        _np_url = _new_page.url or ""
                        if "greenhouse.io" in _np_url or _new_page.query_selector("#first_name"):
                            self._parent_page = _parent
                            self.page = _new_page
                            p = _new_page
                            _on_form = True
                            log.info("switched to Greenhouse new tab: %s", _np_url[:80])
                            break
                        # The new tab may show the GH job description — click Apply inside it
                        if not _on_form:
                            for _inner_text in [
                                "Apply for this Job", "Apply for this job",
                                "Apply to this job", "Apply now", "Apply",
                            ]:
                                try:
                                    _ib = _new_page.get_by_text(_inner_text, exact=False).first
                                    if _ib.count() > 0 and _ib.is_visible():
                                        _ib.click()
                                        _new_page.wait_for_selector("#first_name", timeout=10_000)
                                        self._parent_page = _parent
                                        self.page = _new_page
                                        p = _new_page
                                        _on_form = True
                                        log.info("switched to Greenhouse new tab (after inner click): %s",
                                                 _new_page.url[:80])
                                        break
                                except Exception:
                                    continue
                        if _on_form:
                            break
                except Exception as _nt_exc:
                    log.debug("new-tab detection failed: %s", _nt_exc)

            # Step 2b: find the greenhouse.io frame (may load lazily via JS)
            if not _on_form:
                p.wait_for_timeout(1_500)
                if not any(f.url and "greenhouse.io" in f.url for f in p.frames):
                    try:
                        p.wait_for_selector("iframe[src*='greenhouse']", timeout=8_000)
                        p.wait_for_timeout(1_000)
                    except Exception:
                        pass
                try:
                    _gh_frames = [f for f in p.frames if f.url and "greenhouse.io" in f.url]
                    if _gh_frames:
                        _frame = _gh_frames[0]

                        # Step 3: the frame might show the job description first —
                        # click its own "Apply" button if #first_name isn't visible yet.
                        try:
                            _frame.wait_for_selector("#first_name", timeout=3_000)
                            _on_form = True
                        except Exception:
                            for _inner_text in [
                                "Apply for this Job", "Apply for this job",
                                "Apply to this job", "Apply",
                            ]:
                                try:
                                    _inner_btn = _frame.get_by_text(_inner_text, exact=False).first
                                    if _inner_btn.count() > 0:
                                        _inner_btn.click()
                                        _frame.wait_for_selector("#first_name", timeout=8_000)
                                        _on_form = True
                                        break
                                except Exception:
                                    continue

                        if _on_form:
                            # Switch filler context to the iframe
                            self._parent_page = _parent
                            self.page = _frame
                            p = _frame
                            log.info("switched to greenhouse iframe at %s", _frame.url[:80])
                except Exception as _exc:
                    log.debug("iframe strategy failed: %s", _exc)

            if not _on_form:
                raise NeedsUserInput(
                    f"Redirected to custom career portal ({_parent.url}) — apply manually"
                )

        # -- Email verification code detection ----------------------------------
        # Some Greenhouse-embedded portals ask the user to verify their email
        # before showing the form.  Pause and read the code from stdin.
        _VERIF_SELS = [
            "input[placeholder*='verification code' i]",
            "input[placeholder*='enter code' i]",
            "input[aria-label*='verification code' i]",
            "input[name*='verification' i]",
            "input[name*='confirm_code' i]",
            "input[name*='otp' i]",
            "input[type='number'][maxlength='6']",
            "input[type='tel'][maxlength='6']",
        ]
        try:
            for _vsel in _VERIF_SELS:
                _vc = p.query_selector(_vsel)
                if _vc and _vc.is_visible():
                    _code = input(
                        f"\n>>> Verification code sent to {c.email}.\n"
                        f"    Check your inbox, then type the code and press Enter: "
                    ).strip()
                    if _code:
                        _vc.fill(_code)
                        _vc.press("Enter")
                        p.wait_for_timeout(2_000)
                    break
        except Exception:
            pass

        # Wait for the identity form field to be present and interactive.
        # This handles boards that skip networkidle (e.g. SumUp WebSocket).
        try:
            p.wait_for_selector("#first_name", timeout=15_000)
        except Exception:
            pass

        # -- Identity --------------------------------------------------------
        self.fill("#first_name", c.first_name)
        self.fill("#last_name", c.last_name)
        self.fill("#email", c.email)
        self.fill("#phone", c.phone)
        # Preferred-name field (Twilio and others): single first name only
        self.fill("#preferred_name", c.first_name.split()[0])

        # -- Country React Select (Greenhouse v2 new board) ------------------
        try:
            _country_el = p.query_selector("[id='country']")
            if _country_el and "select__input" in (_country_el.get_attribute("class") or ""):
                _pick_react_option(_country_el, p, "Turkey", "Türkiye")
        except Exception:
            pass

        # -- Location --------------------------------------------------------
        # Greenhouse v2 uses #candidate-location which is a React autocomplete
        # (Google Places).  A plain .fill() sets the <input> value but doesn't
        # fire the change event React listens to, leaving the field aria-invalid.
        # Strategy: type the city name, wait for the suggestion dropdown, then
        # press Enter or click the first suggestion.
        _loc_filled = False
        try:
            loc_el = p.wait_for_selector("#candidate-location", timeout=3_000)
            if loc_el:
                loc_el.click()
                loc_el.fill("")
                loc_el.type("Istanbul", delay=40)
                p.wait_for_timeout(900)
                # Accept the first autocomplete suggestion if one appeared
                suggestion = p.query_selector(
                    ".pac-item, [class*='suggestion'], [class*='option'][role='option']"
                )
                if suggestion:
                    suggestion.click()
                else:
                    loc_el.press("Tab")   # commit the typed value
                _loc_filled = True
        except Exception:
            pass
        if not _loc_filled:
            self.fill_first([
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

        # -- Cover letter (file upload takes precedence over textarea) -------
        cl_text = self._cl_text or c.cover_letter_text(job.title, job.company)
        _cl_file_uploaded = False
        for _cl_sel in [
            "input[type='file'][name*='cover']",
            "input[type='file'][id*='cover']",
            "input[type='file'][aria-label*='cover letter' i]",
        ]:
            try:
                _cl_el = p.query_selector(_cl_sel)
                if _cl_el:
                    _cl_pdf = self.generate_cover_letter_pdf()
                    _cl_el.set_input_files(str(_cl_pdf))
                    _cl_file_uploaded = True
                    log.info("uploaded cover letter PDF for %s @ %s", job.title, job.company)
                    break
            except Exception as _exc:
                log.warning("cover letter PDF file upload failed (%s): %s", _cl_sel, _exc)

        if not _cl_file_uploaded:
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
        _checked_groups: set = set()   # tracks which checkbox groups already have a pick
        _group_question_cache: dict[str, str] = {}  # group_base → fieldset legend text (cached)
        for label_el in p.query_selector_all("label[for^='question_']"):
            try:
                q_id = label_el.get_attribute("for") or ""
                label_text = label_el.inner_text().strip()
                if not q_id or not label_text:
                    continue
                # IDs containing [] are individual options inside a checkbox/radio group.
                if "[" in q_id or "]" in q_id:
                    option_lbl = label_text.lower()
                    group_base = q_id.split("[")[0]  # e.g. "question_64120995"
                    _career = ["careers website", "career website", "company website", "career page"]
                    _secondary = ["linkedin"]
                    _ack = ["acknowledge", "confirm", "certify", "i agree", "i accept"]

                    if any(s in option_lbl for s in _career):
                        try:
                            label_el.click()
                            _checked_groups.add(group_base)
                        except Exception:
                            pass
                    elif any(s in option_lbl for s in _secondary) and group_base not in _checked_groups:
                        # Only pick LinkedIn if no career-site option was already checked
                        try:
                            label_el.click()
                            _checked_groups.add(group_base)
                        except Exception:
                            pass
                    elif any(s in option_lbl for s in _ack):
                        try:
                            label_el.click()
                        except Exception:
                            pass
                    elif group_base not in _checked_groups:
                        # Context-aware routing: look up the group question via fieldset legend
                        if group_base not in _group_question_cache:
                            try:
                                _gq = p.evaluate(
                                    "(qid) => {"
                                    "  const l = document.querySelector(`label[for='${qid}']`);"
                                    "  if (!l) return '';"
                                    "  const fs = l.closest('fieldset');"
                                    "  if (!fs) return '';"
                                    "  const lg = fs.querySelector('legend');"
                                    "  return lg ? lg.innerText.trim() : '';"
                                    "}",
                                    q_id,
                                )
                                _group_question_cache[group_base] = (_gq or "").lower()
                            except Exception:
                                _group_question_cache[group_base] = ""

                        gq = _group_question_cache.get(group_base, "")
                        _want: str | None = None

                        if gq and re.search(
                            r"authoris?ed.*(work|employ)|right to work|eligible to work"
                            r"|work authori[sz]ation|legally.*work|permitted.*work",
                            gq,
                        ):
                            _country_upper = (job.country or "").upper()
                            if _country_upper == "US":
                                _want = "yes" if c.authorized_us else "no"
                            elif _country_upper in ("DE", "NL", "IE", "AT"):
                                _want = "yes" if c.authorized_eu else "no"
                            elif _country_upper in ("GB", "UK"):
                                _want = "yes" if c.authorized_uk else "no"
                            else:
                                _want = "no"

                        elif gq and re.search(
                            r"visa sponsor|require sponsor|need sponsor|sponsorship required"
                            r"|require.*work.*visa|need.*work.*visa|will.*require.*visa",
                            gq,
                        ):
                            _needs = c.needs_sponsorship(job.country)
                            _want = "yes" if _needs else "no"

                        elif gq and re.search(
                            r"relocation|willing.*relocat|open.*relocat|relocat.*for", gq
                        ):
                            _want = "yes"

                        if _want is not None and option_lbl.startswith(_want):
                            try:
                                label_el.click()
                                _checked_groups.add(group_base)
                                log.debug(
                                    "radio group %r (q=%r): clicked %r",
                                    group_base, gq[:60], option_lbl,
                                )
                            except Exception:
                                pass
                    # All other options (Twitter, Glassdoor, Indeed, Other…) → skip
                    continue
                field_el = p.query_selector(f"#{q_id}")
                if not field_el:
                    continue
                # Cover letter file upload via custom question (some ATS embed it here)
                _fq_type = (field_el.get_attribute("type") or "text").lower()
                if _fq_type == "file" and re.search(r"cover.?letter|motivation.*letter", label_text, re.IGNORECASE):
                    try:
                        _cl_pdf = self.generate_cover_letter_pdf()
                        field_el.set_input_files(str(_cl_pdf))
                        log.info("uploaded cover letter PDF via custom question: %r", label_text)
                    except Exception as _pdf_exc:
                        log.warning("cover letter PDF upload failed for %r: %s", label_text, _pdf_exc)
                    continue
                # Skip if already filled
                try:
                    if field_el.input_value():
                        continue
                except Exception:
                    pass
                answered = _answer_custom_question(label_text, field_el, c, job, page=p, cfg=self.cfg)
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

        # -- EEO React Select comboboxes (numeric IDs on Greenhouse new board) --
        # On job-boards.greenhouse.io, EEO fields use React Select comboboxes
        # (class="select__input", role="combobox") NOT regular <select> elements.
        # We iterate labels and target only these fields (non-question_ IDs).
        p.wait_for_timeout(400)
        for _eeo_lbl in p.query_selector_all("label[for]"):
            try:
                _fid = _eeo_lbl.get_attribute("for") or ""
                if not _fid or "[" in _fid or _fid.startswith("question_"):
                    continue
                if _fid in ("first_name", "last_name", "email", "phone",
                            "resume", "cover_letter", "country",
                            "candidate-location", "location", "preferred_name"):
                    continue
                _eeo_el = p.query_selector(f"[id='{_fid}']")
                if not _eeo_el:
                    continue
                _is_combo = (
                    _eeo_el.get_attribute("role") == "combobox"
                    or "select__input" in (_eeo_el.get_attribute("class") or "")
                )
                if not _is_combo:
                    continue
                _eeo_lbl_text = _eeo_lbl.inner_text().strip().lower()

                if "gender" in _eeo_lbl_text:
                    _pick_react_option(_eeo_el, p, "Male", "Man", "Prefer not to say")
                elif "pronoun" in _eeo_lbl_text:
                    _pick_react_option(_eeo_el, p, "He/Him", "He / Him", "He", "Prefer not to say")
                elif "race" in _eeo_lbl_text or "ethnicity" in _eeo_lbl_text:
                    _pick_react_option(_eeo_el, p,
                        "Decline to Self-Identify", "Decline to state",
                        "Prefer not to say", "I don't wish to answer")
                elif "disability" in _eeo_lbl_text:
                    _pick_react_option(_eeo_el, p,
                        "No, I Don't Have a Disability",
                        "No, I do not have a disability",
                        "I Don't Wish to Answer",
                        "I don't wish to answer")
                elif "veteran" in _eeo_lbl_text:
                    _pick_react_option(_eeo_el, p,
                        "I am not a protected veteran",
                        "I Am Not a Protected Veteran",
                        "Not a Protected Veteran",
                        "I Don't Wish to Answer")
                elif "sexual orientation" in _eeo_lbl_text or "lgbtq" in _eeo_lbl_text:
                    _pick_react_option(_eeo_el, p,
                        "Prefer not to say", "I prefer not to answer",
                        "I don't wish to answer", "Decline to Self-Identify",
                        "I prefer not to disclose",
                        "Heterosexual", "Straight", "Heterosexual/Straight")
                elif "gdpr" in _fid.lower() or "consent" in _eeo_lbl_text:
                    pass  # handled below as a checkbox
            except Exception as _eeo_exc:
                log.debug("EEO React field %r error: %s", _fid, _eeo_exc)

        # -- GDPR / demographic data consent checkbox ------------------------
        try:
            _gdpr = p.query_selector(
                "input[id*='gdpr_demographic'], input[id*='consent_given'],"
                "input[id*='demographic_consent']"
            )
            if _gdpr and not _gdpr.is_checked():
                _gdpr.check()
        except Exception:
            pass

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
            "sexual orientation", "lgbtq", "demographic", "gdpr", "consent",
        }
        for el in p.query_selector_all(
            "input[required], textarea[required], select[required],"
            "input[aria-required='true'], textarea[aria-required='true'], select[aria-required='true']"
        ):
            try:
                el_type = el.get_attribute("type") or "text"
                if el_type in ("file", "checkbox", "radio", "hidden"):
                    continue

                # React Select comboboxes always have input_value()=="" even when an
                # option is selected — the value lives in a sibling .select__single-value.
                # Check that sibling before assuming the field is empty.
                is_combobox = el.get_attribute("role") == "combobox"
                if is_combobox:
                    has_value = el.evaluate("""e => {
                        const ctrl = e.closest('[class*="select__control"]')
                                  || e.closest('[class*="select"]')
                                  || e.parentElement;
                        if (!ctrl) return false;
                        const sv = ctrl.querySelector('[class*="single-value"]');
                        return sv && sv.innerText.trim().length > 0;
                    }""")
                    if has_value:
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

                # One more attempt: try to answer via custom question handler
                if label_text and _answer_custom_question(label_text, el, c, job, page=p, cfg=self.cfg):
                    continue

                raise NeedsUserInput(f"Unknown required field: '{label_text or identifier}'")
            except NeedsUserInput:
                raise
            except Exception:
                pass

        # -- Required checkboxes (GDPR consent, certification, privacy) ------
        # input_value() returns the value attribute on checkboxes ("on"), not
        # the checked state — so the required-field sweep above misses unchecked
        # boxes. Check every required unchecked checkbox we can find.
        for cb in p.query_selector_all("input[type='checkbox'][required]"):
            try:
                if not cb.is_checked():
                    cb.check()
            except Exception:
                try:
                    cb.click()
                except Exception:
                    pass

        # -- Walk through any paginated steps before submit ------------------
        self._walk_steps()

        # -- Submit ----------------------------------------------------------
        # Greenhouse new board uses React — aria-disabled must be false before clicking.
        # Wait up to 5s for the submit button to become enabled after React processes
        # the last field fill, then use Playwright's Locator API (not ElementHandle)
        # which correctly handles aria-disabled and synthesizes the full interaction.
        if not self.dry_run:
            p.wait_for_timeout(1_200)   # give React time to process all EEO picks
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

            # -- Post-submit: detect validation errors and retry once ----------
            p.wait_for_timeout(2_000)
            try:
                invalid_els = p.query_selector_all("[aria-invalid='true']")
                if invalid_els:
                    log.info("found %d aria-invalid field(s) after first submit:", len(invalid_els))
                    fixed = 0
                    for inv_el in invalid_els:
                        try:
                            eid = inv_el.get_attribute("id") or ""
                            el_name = inv_el.get_attribute("name") or ""
                            lbl_el = p.query_selector(f"label[for='{eid}']") if eid else None
                            lbl_text = lbl_el.inner_text().strip() if lbl_el else (
                                inv_el.get_attribute("aria-label")
                                or inv_el.get_attribute("placeholder") or ""
                            )
                            cur_val = ""
                            try:
                                cur_val = inv_el.input_value()
                            except Exception:
                                pass
                            log.info(
                                "  invalid field  id=%r  name=%r  label=%r  current_val=%r",
                                eid, el_name, lbl_text, cur_val,
                            )
                            if lbl_text:
                                answered = _answer_custom_question(lbl_text, inv_el, c, job, page=p, cfg=self.cfg)
                                log.info("  → _answer_custom_question returned %s", answered)
                                if answered:
                                    fixed += 1
                        except Exception as _fix_exc:
                            log.warning("  error fixing invalid field: %s", _fix_exc)
                    if fixed:
                        p.wait_for_timeout(500)
                        btn_locator.click()
                        log.info("submit re-clicked after fixing %d/%d invalid field(s) for %s @ %s",
                                 fixed, len(invalid_els), self.job.title, self.job.company)
                    # Check if still invalid after retry
                    p.wait_for_timeout(1_500)
                    still_invalid = p.query_selector_all("[aria-invalid='true']")
                    if still_invalid:
                        log.warning("%d field(s) still aria-invalid after retry:", len(still_invalid))
                        for inv_el in still_invalid:
                            try:
                                eid = inv_el.get_attribute("id") or ""
                                lbl_el = p.query_selector(f"label[for='{eid}']") if eid else None
                                lbl_text = lbl_el.inner_text().strip() if lbl_el else (
                                    inv_el.get_attribute("aria-label")
                                    or inv_el.get_attribute("placeholder") or ""
                                )
                                cur_val = ""
                                try:
                                    cur_val = inv_el.input_value()
                                except Exception:
                                    pass
                                log.warning("  still-invalid  id=%r  label=%r  val=%r", eid, lbl_text, cur_val)
                            except Exception:
                                pass
            except Exception:
                pass
        else:
            log.info("dry_run=True — skipping submit for %s @ %s", self.job.title, self.job.company)
