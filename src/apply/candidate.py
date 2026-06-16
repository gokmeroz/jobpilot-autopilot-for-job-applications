"""
Loads structured candidate facts from CANDIDATE.md.
All form fillers read from CandidateData — never hardcode answers elsewhere.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from src.config import ROOT, env, load

log = logging.getLogger(__name__)


def _fill_cover_letter_placeholders(
    job_title: str,
    company: str,
    description: str,
) -> dict[str, str]:
    """Return filled values for <WHAT_COMPANY_DOES> and <PERSONAL_REASON_ABOUT_COMPANY>.

    Calls Claude Haiku with the job description.  Falls back to empty strings
    (which removes the placeholder lines) if the API is unavailable.
    """
    try:
        from anthropic import Anthropic
        from src.config import load as _load_cfg

        cfg = _load_cfg("config")
        model = cfg.get("score", {}).get("model", "claude-haiku-4-5-20251001")
        client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))

        desc_snippet = (description or "")[:1500]
        prompt = f"""\
You are helping personalise a job-application cover letter.

Job title: {job_title}
Company: {company}
Job description excerpt:
{desc_snippet}

Return ONLY a JSON object with exactly two keys:
- "what_company_does": A 5-10 word lowercase phrase (no period) describing what the company/product does. \
Example: "developer-focused project management and issue tracking"
- "personal_reason": A short clause (NOT a full sentence) that completes "What draws me to {company} is ___". \
Start with "the", "how", "their", or a similar article/adverb — never with a proper noun or "I". \
Be specific to the product. Max 20 words. \
Example: "the relentless focus on speed and developer experience over feature bloat"

Return ONLY the JSON object."""

        resp = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL)
        data = json.loads(raw)
        return {
            "what_company_does": str(data.get("what_company_does", "")).strip(),
            "personal_reason": str(data.get("personal_reason", "")).strip(),
        }
    except Exception as exc:
        log.warning("cover letter placeholder fill failed: %s", exc)
        return {"what_company_does": "", "personal_reason": ""}


@dataclass
class WorkAuth:
    right_to_work: bool
    needs_sponsorship: bool
    notes: str = ""


@dataclass
class CandidateData:
    # Identity
    full_name: str
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str
    linkedin_url: str
    github_url: str
    portfolio_url: str
    twitter_url: str
    projects_text: str

    # File paths
    resume_path: Path
    cover_letter_short: Path
    cover_letter_long: Path

    # Canonical answers
    yoe: str
    education: str
    english_level: str
    german_level: str
    notice_period: str
    authorized_eu: bool
    authorized_uk: bool
    authorized_us: bool

    # Tables
    work_auth: dict[str, WorkAuth] = field(default_factory=dict)
    salary: dict[str, dict] = field(default_factory=dict)

    # EEO defaults (submitted verbatim on EEO forms)
    gender: str = "Prefer not to say"
    ethnicity: str = "Prefer not to say"   # fallback if forced: "Other"
    disability: str = "I do not wish to answer"
    veteran: str = "I am not a protected veteran"
    pronouns: str = "He/Him"

    # -----------------------------------------------------------------------

    def salary_for(self, country: str) -> str:
        """Return the salary range string for a given market, or the default."""
        row = self.salary.get(country)
        if row:
            return f"{row['currency']} {row['range']}"
        if country in ("US", "REMOTE"):
            row = self.salary.get("Remote (US co.)")
            if row:
                return f"{row['currency']} {row['range']}"
        return "Negotiable and market competitive."

    def needs_sponsorship(self, country: str) -> bool:
        auth = self.work_auth.get(country)
        return auth.needs_sponsorship if auth else True

    def cover_letter_text(
        self,
        job_title: str,
        company: str,
        short: bool = True,
        description: str = "",
    ) -> str:
        path = self.cover_letter_short if short else self.cover_letter_long
        text = path.read_text()
        text = text.replace("<ROLE>", job_title)
        text = text.replace("<COMPANY>", company)

        # Fill remaining dynamic placeholders via LLM if description is available.
        needs_fill = "<WHAT_COMPANY_DOES>" in text or "<PERSONAL_REASON_ABOUT_COMPANY>" in text
        if needs_fill:
            fills = _fill_cover_letter_placeholders(job_title, company, description)
            text = text.replace("<WHAT_COMPANY_DOES>", fills.get("what_company_does", ""))
            reason = fills.get("personal_reason", "")
            # Lowercase first char so it fits after "is " in the sentence
            if reason:
                reason = reason[0].lower() + reason[1:]
            text = text.replace("<PERSONAL_REASON_ABOUT_COMPANY>", reason)

        return text


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _extract(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


def _parse_work_auth(text: str) -> dict[str, WorkAuth]:
    result: dict[str, WorkAuth] = {}
    # Match table rows after the header
    for m in re.finditer(
        r"^\|\s*([A-Z]{2}(?:\s*\([^)]*\))?)\s*\|"
        r"\s*(yes|no)\s*\|"
        r"\s*(yes|no)\s*\|"
        r"\s*([^|]*)\|",
        text,
        re.MULTILINE | re.IGNORECASE,
    ):
        country = m.group(1).strip().upper().split()[0]   # "US (remote)" → "US"
        result[country] = WorkAuth(
            right_to_work=m.group(2).strip().lower() == "yes",
            needs_sponsorship=m.group(3).strip().lower() == "yes",
            notes=m.group(4).strip(),
        )
    return result


def _parse_projects_text(text: str) -> str:
    """Return a plain-text project list suitable for ATS 'Projects' fields."""
    m = re.search(r"## Projects\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not m:
        return ""
    lines = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # "Name (desc): url1 | url2" → keep name + first URL
        content = line[2:]
        url_match = re.search(r"https?://\S+", content)
        url = url_match.group(0) if url_match else ""
        # Name is everything before the first ":"
        name_part = content.split(":")[0].strip()
        lines.append(f"{name_part}: {url}" if url else name_part)
    return "\n".join(lines)


def _parse_salary(text: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for m in re.finditer(
        r"^\|\s*([^|]+?)\s*\|\s*([A-Z]{3})\s*\|\s*([^|]+?)\s*\|",
        text,
        re.MULTILINE,
    ):
        market = m.group(1).strip()
        if market.lower() in ("market", "---", ""):
            continue
        result[market] = {
            "currency": m.group(2).strip(),
            "range": m.group(3).strip(),
        }
    return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_candidate() -> CandidateData:
    cfg = load("config")
    text = (ROOT / "CANDIDATE.md").read_text()

    full_name = _extract(r"^- Full name:\s*(.+)$", text)
    # rsplit on last space: "Goktug Mert Ozdogan" → first="Goktug Mert", last="Ozdogan"
    parts = full_name.rsplit(None, 1)

    # Email may be wrapped in markdown link: [addr](mailto:addr)
    raw_email = _extract(r"^- Email:\s*(.+)$", text)
    email = re.search(r"[\w.+-]+@[\w-]+\.\w+", raw_email)
    email = email.group(0) if email else raw_email

    return CandidateData(
        full_name=full_name,
        first_name=parts[0] if parts else "",
        last_name=parts[1] if len(parts) > 1 else "",
        email=email,
        phone=_extract(r"^- Phone:\s*(.+)$", text),
        location=_extract(r"^- Current location:\s*(.+)$", text),
        linkedin_url=_extract(r"^- LinkedIn:\s*(https?://\S+)", text),
        github_url=_extract(r"^- GitHub:\s*(https?://\S+)", text),
        portfolio_url=_extract(r"^- Portfolio:\s*(https?://\S+)", text),
        twitter_url=_extract(r"^- Twitter:\s*(https?://\S+)", text),
        projects_text=_parse_projects_text(text),

        resume_path=ROOT / cfg["apply"]["resume_file"],
        cover_letter_short=ROOT / cfg["apply"]["cover_letter_short"],
        cover_letter_long=ROOT / cfg["apply"]["cover_letter_long"],

        yoe=_extract(r"^- Years of professional experience:\s*(.+)$", text),
        education=_extract(r"^- Highest education:\s*(.+)$", text),
        english_level=_extract(r"^- English level:\s*(.+)$", text),
        german_level=_extract(r"^- German level:\s*(.+)$", text),
        notice_period=_extract(r"^- Notice period:\s*(.+)$", text, default="immediately available"),
        authorized_eu=_extract(r"^- Authorized to work in the EU:\s*(.+)$", text).lower() == "yes",
        authorized_uk=_extract(r"^- Authorized to work in the UK:\s*(.+)$", text).lower() == "yes",
        authorized_us=_extract(r"^- Authorized to work in the US:\s*(.+)$", text).lower() == "yes",

        work_auth=_parse_work_auth(text),
        salary=_parse_salary(text),
    )
