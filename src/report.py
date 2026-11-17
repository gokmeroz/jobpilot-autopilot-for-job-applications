"""
Job Search Report — sections A–G of the search brief, plus the CLAUDE.md
session summary. Rendered as Markdown, printed to the terminal in full and
saved to history/<run_id>_report.md (the apply flow and application_history
need a file; everything is also shown inline).
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from datetime import datetime

from src import freshness, tracker
from src.config import ROOT, load
from src.models import Job

log = logging.getLogger(__name__)

_PATHS = {
    "ai_ml": "AI / ML / LLM Engineering",
    "backend_fullstack": "Backend / Full-stack Software Engineering",
    "forward_deployed": "Forward Deployed / Applied Engineering",
    "graduate": "Graduate and Early-Career Programmes",
}


def _c(v) -> str:
    return str(v if v not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _link(job: Job) -> str:
    return f"[Apply]({job.apply_url})"


def _posted(job: Job) -> str:
    return job.posted_at.strftime("%Y-%m-%d") if job.posted_at else "unavailable"


def _score(job: Job) -> str:
    # floor, so 79.8 reads "79 (Good fit)" rather than "80 (Good fit)"
    return str(int(job.score.total)) if job.score else "—"


def _sp(job: Job) -> str:
    return job.sponsorship.category if job.sponsorship else "C"


# ---------------------------------------------------------------------------
# Section A — resume assessment (LLM, cached per resume version)
# ---------------------------------------------------------------------------

def resume_assessment(offline: bool = False) -> str:
    from src.resume import resume_markdown
    from src.score import _PROFILE_EXCLUDED_SECTIONS, _strip_sections

    ai, swe = resume_markdown("ai") or "", resume_markdown("swe") or ""
    profile = _strip_sections((ROOT / "CANDIDATE.md").read_text(), _PROFILE_EXCLUDED_SECTIONS)
    key = hashlib.sha256((ai + swe + profile).encode()).hexdigest()[:16]
    cache = ROOT / "data" / f"resume_assessment_{key}.md"
    if cache.exists():
        return cache.read_text()
    if offline:
        return "_Resume assessment skipped (dry run)._"
    from src.score import _make_client
    prompt = f"""Two resumes (AI/ML-focused and SWE-focused) and a candidate profile follow.
Using ONLY what they document (never invent experience, credentials or dates), write a concise
Markdown assessment with these bullet groups: **Strongest skills**, **Realistic seniority**
(distinguish paid employment from internships/projects), **Target job families** (ranked),
**Weaknesses / missing qualifications**, **Transferable strengths**, **Best-fit markets**
(considering a Turkish citizen needing sponsorship outside Türkiye). No preamble.

## Candidate profile
{profile}

## AI resume
{ai}

## SWE resume
{swe}"""
    try:
        cfg = load("config")["score"]
        msg = _make_client().messages.create(model=cfg.get("model"), max_tokens=1500,
                                             messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text.strip()
    except Exception as exc:   # report must still render
        log.warning("resume assessment failed: %s", exc)
        return f"_Resume assessment unavailable: {exc}_"
    cache.write_text(text)
    return text


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _best_matches(jobs: list[Job]) -> list[str]:
    out = ["| Rank | Company | Job Title | Country / City | Date Posted | Fit Score | Sponsorship | Salary | Verified | Apply |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for i, j in enumerate(jobs, 1):
        out.append(f"| {i} | {_c(j.company)} | {_c(j.title)} | {_c(j.country)} / {_c(j.location)} | {_posted(j)} "
                   f"| {_score(j)} ({_c(j.fit.fit_class if j.fit else '')}) | {_sp(j)} | {_c(j.salary)} "
                   f"| {_c((j.verification or 'unverified').split(' ')[0])} | {_link(j)} |")
    return out


def _sponsorship_section(jobs: list[Job]) -> list[str]:
    out: list[str] = []
    for cat, label in (("A", "Explicit sponsorship available"), ("B", "Sponsorship possible — company-level evidence only")):
        rows = [j for j in jobs if _sp(j) == cat]
        out += [f"#### {cat}. {label} ({len(rows)})", ""]
        if not rows:
            out += ["None this run.", ""]
            continue
        out += ["| Company | Position | Country | Fit | Category | Supporting Evidence | Apply |", "|---|---|---|---|---|---|---|"]
        for j in rows:
            sp = j.sponsorship
            ev = sp.evidence[:160] + (f" ([source]({sp.evidence_url}))" if sp.evidence_url and sp.evidence_url != j.apply_url else "")
            out.append(f"| {_c(j.company)} | {_c(j.title)} | {j.country} | {_score(j)} | {cat} | {_c(ev)} | {_link(j)} |")
        out.append("")
    au = [j for j in jobs if j.country == "AU" and j.sponsorship]
    out += [f"#### Australia — subclass 482 detail ({len(au)})", ""]
    if au:
        out += ["| Company | Position | Category | 482 pathway | Offshore eligibility | Salary eligibility | Evidence type | Verified | Apply |",
                "|---|---|---|---|---|---|---|---|---|"]
        for j in au:
            sp = j.sponsorship
            out.append(f"| {_c(j.company)} | {_c(j.title)} | {sp.category} | {_c(sp.visa_pathway if sp.category in ('A', 'B') else 'unconfirmed')} "
                       f"| {sp.offshore} | {_c(sp.salary_eligibility)} | {_c(sp.evidence_type)} | {_c(j.verification)} | {_link(j)} |")
    else:
        out.append("No Australian roles passed scoring this run.")
    return out + [""]


def _new_jobs(jobs: list[Job]) -> list[str]:
    groups: dict[str, list[Job]] = {}
    for j in jobs:
        groups.setdefault(freshness.bucket(j.age_hours()), []).append(j)
    order = ["last 24h", "last 72h", "last 7 days", "older but still open", "original publication date unavailable"]
    out: list[str] = []
    for g in order:
        rows = groups.get(g, [])
        out += [f"#### {g.capitalize()} ({len(rows)})", ""]
        out += [f"- {_c(j.title)} — {_c(j.company)} ({j.country}) · posted {_posted(j)} · fit {_score(j)} · {_sp(j)} · {_link(j)}"
                for j in sorted(rows, key=lambda x: -(x.score.total if x.score else 0))] or ["None."]
        out.append("")
    return out


def _by_path(jobs: list[Job]) -> list[str]:
    out: list[str] = []
    for key, label in _PATHS.items():
        rows = [j for j in jobs if j.fit and j.fit.career_path == key]
        out += [f"#### {label} ({len(rows)})", ""]
        out += [f"- {_c(j.title)} — {_c(j.company)} ({j.country}) · fit {_score(j)} · {_sp(j)} · {_link(j)}" for j in rows] or ["None."]
        out.append("")
    return out


def _priority_score(j: Job) -> float:
    s = j.score.total if j.score else 0
    s += {"A": 12, "B": 6, "-": 8, "C": 0, "D": -20}.get(_sp(j), 0)
    age = j.age_hours()
    s += 6 if age is not None and age <= 72 else 3 if age is not None and age <= 168 else 0
    s -= 5 if (j.verification or "").startswith("unverified") else 0
    return s


def _top10(jobs: list[Job]) -> list[str]:
    out: list[str] = []
    for i, j in enumerate(sorted(jobs, key=_priority_score, reverse=True)[:10], 1):
        f = j.fit
        sp = j.sponsorship
        out += [
            f"**{i}. {_c(j.title)} — {_c(j.company)} ({j.country})** · fit {_score(j)} · sponsorship {_sp(j)}",
            f"1. Why apply: {_c(j.score.rationale if j.score else '')}",
            f"2. Resume: **{(j.recommended_resume or 'swe').upper()}**",
            f"3. Competitive because: {_c(', '.join(f.matching_skills[:8]) if f else '')}",
            f"4. Could block you: {_c('; '.join((f.missing_mandatory + f.risks)[:5]) if f else '')}"
            + (f"; offshore eligibility {sp.offshore}" if sp and sp.offshore in ('Restricted', 'Unknown') and j.country not in ('TR', 'REMOTE') else ""),
            f"5. Emphasize: {_c(f.strongest_project if f else '')} — {_c(f.positioning if f else '')}",
            f"6. Apply: {j.apply_url}",
            "",
        ]
    return out or ["No eligible matches this run."]


def _coverage(result, started: datetime) -> list[str]:
    from src.discover.ats.career_pages import COVERAGE as CAREER
    from src.discover.scrapers.apify_runner import BUDGET
    from src import sponsors

    spons = Counter(_sp(j) for j in result.eligible)
    out = [
        f"- Executed: {started.astimezone().strftime('%Y-%m-%d %H:%M %Z')} · freshness window {result.window_hours:.0f}h",
        f"- Jobs retrieved: {result.total_discovered} · unique: {result.unique} · passed gate: {result.passed_gate} · "
        f"duplicates of earlier runs: {result.duplicates_skipped}",
        f"- Scored: {result.scored} · verified open: {result.verified_open} · unverified: {result.unverified} · closed: {result.closed}",
        f"- Eligible matches (≥ {load('config')['score'].get('stretch_min', 50)}, no hard blocker): {len(result.eligible)} "
        f"· sponsorship A {spons['A']} / B {spons['B']} / C {spons['C']} / D {spons['D']} / not needed {spons['-']}",
        "", "**Top exclusion reasons**", "",
    ]
    out += [f"- {n} × {r}" for r, n in result.exclusions.most_common(10)] or ["- none"]
    out += ["", "**Sources**", "", "| Source | Queries | Records | Health | Notes |", "|---|---|---|---|---|"]
    for c in result.coverage:
        out.append(f"| {_c(c['label'])} | {_c(c.get('queries'))} | {c.get('records', 0)} | {_c(c.get('verdict'))} | {_c(c.get('note'))} |")
    out += ["", "**Sponsor evidence lists**", ""]
    out += [f"- {k}: {v}" for k, v in sponsors.STATUS.items()] or ["- not loaded"]
    out += ["", "**Direct career pages**", ""]
    out += [f"- {c['company']} — {c['status']} ({', '.join(c['boards']) or c['url']})" for c in CAREER] or ["- not run"]
    out += ["", f"**Apify usage**: ~${BUDGET.spent_usd:.2f} of ${BUDGET.limit():.2f} budget across {len(BUDGET.runs)} run(s)"]
    out += [f"- {r['actor']}: {r['status']}, {r['items']} items, ~${r['cost_usd']:.3f}" for r in BUDGET.runs]
    au = [c for c in result.coverage if c["source"] in ("seek", "landed", "linkedin", "career_pages")]
    out += ["", "**Australia coverage**", ""]
    out += [f"- {c['label']}: {'searched' if c.get('records') or c.get('verdict') == 'healthy' else 'no results / failed'} ({c.get('records', 0)} records)" for c in au]
    out += [f"- {k}: {v}" for k, v in sponsors.STATUS.items() if k.startswith("au_")]
    out += ["", "_Not covered: Indeed, Glassdoor, Wellfound search beyond configured actor, Workday tenants not linked from a "
            "discovered posting, and any site without a public listing. This report does not claim complete coverage of all jobs._"]
    return out


def _tracker_changes(changes: dict) -> list[str]:
    if not changes:
        return ["Tracker unavailable this run."]
    out = [f"- New: {len(changes['new'])} · still open: {len(changes['still_open'])} · closed/removed: {len(changes['closed'])} "
           f"· description changed: {len(changes['changed'])} · new sponsorship info: {len(changes['new_sponsorship'])} "
           f"· marked applied: {len(changes['applied'])}"]
    for key, label in (("closed", "Closed or removed"), ("changed", "Description changed"), ("new_sponsorship", "New sponsorship info")):
        out += [f"- {label}: {r['title']} — {r['company']} ({r['country']})" for r in changes[key][:20]]
    return out + ["", "Full tracker: `jobpilot --tracker md|csv|json`"]


def build(result, started: datetime, assessment: str) -> str:
    eligible = sorted(result.eligible, key=lambda j: j.score.total if j.score else 0, reverse=True)
    review = result.jobs_for_review
    countries = Counter(j.country for j in eligible)
    lines = [
        f"# Job Search Session Report — {result.run_id}", "",
        "## Summary", "",
        "| Metric | Count |", "|---|---|",
        f"| Jobs Found | {result.total_discovered} |",
        f"| Jobs Passed Gate | {result.passed_gate} |",
        f"| Jobs Scored Above Threshold | {result.above_threshold} |",
        f"| Jobs Awaiting Review | {len(review)} |",
        f"| Jobs Applied | {result.applied} |",
        f"| Needs User Input | {result.needs_user_input} |",
        f"| Duplicates Skipped | {result.duplicates_skipped} |", "",
        "## Section A — Resume Assessment", "", assessment, "",
        "## Section B — Best Overall Job Matches", "", *_best_matches(eligible), "",
        "## Section C — Visa Sponsorship Opportunities", "", *_sponsorship_section(eligible),
        "## Section D — Newly Published Jobs", "", *_new_jobs(eligible),
        "## Section E — Opportunities by Career Path", "", *_by_path(eligible),
        "## Section F — Top 10 Applications to Prioritize", "", *_top10(eligible),
        "## Section G — Search Coverage & Limitations", "", *_coverage(result, started), "",
        "## Tracker Changes", "", *_tracker_changes(result.tracker_changes), "",
        "## Review List", "",
        f"Approve in `{result.review_file.name if result.review_file else '—'}` (score ≥ {load('config')['score']['threshold']}).", "",
        "| # | Role | Company | Country | Type | Salary | Score | Apply Link |", "|---|---|---|---|---|---|---|---|",
        *[f"| {i} | {_c(j.title)} | {_c(j.company)} | {j.country} | {j.remote.value.title()} | {_c(j.salary)} | {_score(j)} | {_c(j.apply_url)} |"
          for i, j in enumerate(review, 1)], "",
        "## Country Breakdown", "", *[f"- {k}: {v}" for k, v in countries.most_common()], "",
        "## Problems Encountered", "",
        *([f"- {c['label']}: {c['verdict']} — {c.get('note')}" for c in result.coverage if c.get("verdict") not in (None, "healthy")] or ["- none"]), "",
        "## Recommended Next Actions", "",
        "- Review the list above, then confirm `apply` / `apply to #1, #3` (or answer the interactive prompts).",
        "- Sponsorship B/C roles: confirm with the recruiter before investing in a custom application.",
        "- Re-run daily: `jobpilot` (window auto-adjusts to the time since the last run).",
    ]
    return "\n".join(lines) + "\n"


def tracker_markdown(rows: list[dict]) -> str:
    cols = ["job_id", "company", "title", "country", "fit_score", "sponsorship_status", "application_status",
            "posted_at", "last_verified_at", "apply_url"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_c(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(out)
