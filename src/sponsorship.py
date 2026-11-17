"""
Visa sponsorship classification — a ranking dimension, separate from fit.

  A  Explicit sponsorship available (posting text says so — quoted)
  B  Possible: employer-level evidence (sponsor register / directory / visa board)
  C  Unknown
  D  Unavailable / restricted (no sponsorship, existing work rights, citizenship,
     clearance)
  -  Not needed (home country, see sponsorship.home_countries)

Rules: posting-level negatives beat positives; directory/register membership
never yields A; an LLM-proposed A/D is accepted only when its quote appears
verbatim in the posting (see apply_llm_claim).

The candidate is a Turkish citizen with no existing US/UK/EU/CA/AU work rights.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from src import sponsors
from src.config import load
from src.models import Job, Sponsorship

_NEG = re.compile(
    r"(?:no|not\s+(?:able|in\s+a\s+position)\s+to\s+(?:offer|provide)|unable\s+to\s+(?:offer|provide)|"
    r"(?:do|does|will|can)\s+not\s+(?:offer|provide))\s+(?:visa\s+|immigration\s+)?sponsorship|"
    r"(?:unable|not\s+able)\s+to\s+sponsor|(?:will|can|do|does)\s*(?:not|n't)\s+sponsor|"
    r"without\s+(?:the\s+need\s+for\s+)?(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship|"
    r"sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)|"
    r"must\s+(?:have|hold|possess)\s+(?:the\s+|an?\s+)?(?:full\s+|unrestricted\s+|existing\s+|valid\s+|current\s+)*"
    r"(?:right|authori[sz]ation|work\s+rights|working\s+rights)\s+to\s+work|"
    r"(?:full|unrestricted|existing)\s+(?:australian\s+)?work(?:ing)?\s+rights|"
    r"(?:australian|u\.?s\.?|uk|british|canadian)\s+citizens?(?:hip)?\s+(?:or|and|/)\s+permanent\s+residen|"
    r"citizens?\s+only|security\s+clearance|(?:nv1|nv2|baseline|sc|dv)\s+clearance|itar",
    re.IGNORECASE,
)
_POS = re.compile(
    r"visa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered|possible)|"
    r"(?:we|employer)\s+(?:can|will|are\s+able\s+to|is\s+able\s+to|do)\s+sponsor|"
    r"sponsorship\s+(?:is\s+)?available|(?:offer|provide)s?\s+(?:visa\s+)?sponsorship|"
    r"(?:482|tss|skills\s+in\s+demand|skilled\s+worker|h-?1b|blue\s+card|critical\s+skills|"
    r"highly\s+skilled\s+migrant|kennismigrant)\s+(?:visa\s+)?(?:sponsorship|support)|"
    r"(?:relocation|immigration)\s+(?:and|&)\s+visa\s+(?:support|assistance)|"
    r"visa\s+(?:and|&)\s+relocation\s+(?:support|assistance)|"
    r"(?:visa|immigration)\s+(?:support|assistance)\s+(?:is\s+)?(?:available|provided|offered)",
    re.IGNORECASE,
)
_OFFSHORE_NO = re.compile(
    r"must\s+(?:currently\s+)?(?:be\s+)?(?:based|located|residing|reside|live)\s+in\s+(?:australia|the\s+uk|the\s+united\s+kingdom|"
    r"ireland|the\s+us|the\s+united\s+states|canada|germany|the\s+netherlands|switzerland|the\s+eu)|"
    r"(?:only|currently)\s+(?:considering|accepting)\s+(?:local|onshore|domestic)\s+(?:candidates|applicants)|"
    r"onshore\s+(?:candidates|applicants)\s+only|no\s+(?:offshore|overseas)\s+(?:candidates|applicants)",
    re.IGNORECASE,
)
_OFFSHORE_YES = re.compile(
    r"(?:open\s+to|welcome|consider|accepting)\s+(?:applications?\s+from\s+)?(?:international|overseas|offshore|global)\s+"
    r"(?:candidates|applicants|talent)?|relocat\w*\s+(?:from\s+)?(?:overseas|abroad|internationally)|"
    r"relocation\s+(?:package|support|assistance)\s+(?:is\s+)?(?:available|provided|offered)",
    re.IGNORECASE,
)
_SALARY_NUM = re.compile(r"(\d{2,3})(?:,(\d{3})|\s?k)\b", re.IGNORECASE)

PATHWAYS = {
    "AU": "Skills in Demand (subclass 482) — employer nominated",
    "GB": "Skilled Worker visa (licensed sponsor)",
    "IE": "Critical Skills Employment Permit",
    "DE": "EU Blue Card / skilled worker visa",
    "NL": "Highly Skilled Migrant (recognised sponsor)",
    "CH": "Non-EU work permit (cantonal quota)",
    "CA": "LMIA-based work permit / Global Talent Stream",
    "US": "H-1B (lottery) — employer sponsored",
    "AT": "Red-White-Red Card", "BE": "Single permit / EU Blue Card",
    "IT": "EU Blue Card", "LU": "EU Blue Card",
}


def _snippet(text: str, m: re.Match, pad: int = 80) -> str:
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return " ".join(text[a:b].split())


def _au_salary(job: Job) -> str:
    threshold = int(load("config").get("sponsorship", {}).get("au_csit_aud", 0))
    if not job.salary or not threshold:
        return "unverified (salary not published)"
    nums = []
    for m in _SALARY_NUM.finditer(job.salary):
        nums.append(int(m.group(1)) * 1000 + int(m.group(2) or 0))
    annual = [n for n in nums if n >= 30000]
    if not annual:
        return "unverified (salary not annual / unparsed)"
    lo = min(annual)
    return (f"meets CSIT AUD {threshold:,} at low end (AUD {lo:,}); market-salary rule still applies"
            if lo >= threshold else f"below CSIT AUD {threshold:,} at low end (AUD {lo:,})")


def classify(job: Job) -> Sponsorship:
    cfg = load("config").get("sponsorship", {})
    text = f"{job.title}\n{job.description or ''}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    country = job.country.upper()
    sp = Sponsorship(verified_at=now, visa_pathway=PATHWAYS.get(country, ""))

    if country in {c.upper() for c in cfg.get("home_countries", ["TR"])}:
        sp.category, sp.evidence, sp.offshore, sp.visa_pathway = "-", "Home country — no visa needed", "Confirmed", ""
        return sp

    neg, pos = _NEG.search(text), _POS.search(text)
    if neg:
        sp.category, sp.evidence, sp.evidence_type = "D", _snippet(text, neg), "posting"
        sp.evidence_url = job.apply_url
    elif pos:
        sp.category, sp.evidence, sp.evidence_type = "A", _snippet(text, pos), "posting"
        sp.evidence_url = job.apply_url
    else:
        hit = sponsors.lookup(job.company, country)
        if hit:
            sp.category, sp.evidence, sp.evidence_url, sp.evidence_type = "B", hit["detail"], hit["url"], "sponsor_register"
        elif job.source in {"ukhired", "relocateme", "landed"} or job.visa_signal:
            sp.category, sp.evidence_type = "B", "board"
            sp.evidence = {"ukhired": "Listed on UKHired (UK sponsor-licence holders)",
                           "relocateme": "Listed on Relocate.me (relocation-support roles)",
                           "landed": "Listed on Landed visa-sponsorship board"}.get(job.source, "Sponsorship/relocation keyword in posting (unquoted)")
        if country == "REMOTE":
            sp.visa_pathway = "Remote — depends on accepted hiring countries"

    off_no, off_yes = _OFFSHORE_NO.search(text), _OFFSHORE_YES.search(text)
    if off_no:
        sp.offshore = "Restricted"
        if sp.category in ("A", "B", "C"):
            sp.evidence = (sp.evidence + " | " if sp.evidence else "") + "Offshore: " + _snippet(text, off_no, 40)
    elif off_yes:
        sp.offshore = "Confirmed"
    elif sp.category == "A":
        sp.offshore = "Possible"
    elif sp.category == "D":
        sp.offshore = "Restricted"

    if country == "AU":
        sp.salary_eligibility = _au_salary(job)
    return sp


def _norm_ws(s: str) -> str:
    return " ".join(s.lower().split())


def apply_llm_claim(job: Job, sp: Sponsorship, category: str | None, quote: str | None) -> Sponsorship:
    """Accept an LLM-proposed A or D only when its quote is verbatim in the posting."""
    if category not in ("A", "D") or not quote or sp.category in ("-", category):
        return sp
    if len(quote) < 15 or _norm_ws(quote) not in _norm_ws(job.description or ""):
        return sp
    if category == "A" and sp.category == "D":
        return sp   # a regex-detected restriction is never overridden upward
    return sp.model_copy(update={"category": category, "evidence": quote[:300],
                                 "evidence_type": "llm_quote", "evidence_url": job.apply_url})


def hard_restricted(sp: Sponsorship, job: Job) -> str | None:
    """Mandatory immigration mismatch → reason (job is 'Not recommended')."""
    if sp.category == "D" and job.country.upper() != "REMOTE":
        return f"Sponsorship restricted: {sp.evidence[:120]}"
    if sp.offshore == "Restricted" and job.country.upper() not in ("REMOTE",):
        return "Requires candidates already based in-country"
    return None
