"""
Main pipeline orchestrator.

Flow:
    jobs (injected by discover modules)
      → gate       (age / seniority / language / YOE filter)
      → dedupe     (ledger + sheet cross-check)
      → score      (GPT-5.5 scoring against CANDIDATE.md)
      → plan       (assign cover letter + route)
      → review     (write manual_queue/<run_id>_review.md, stop if review_first)
      → sheet      (append above-threshold jobs)
      → report     (session summary written to history/)
"""
from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src import gate, ledger
from src.apply.runner import apply_batch
from src.config import ROOT, load
from src.models import Job, Route, Status
from src.score import score_batch
from src.sheet import append_jobs
from src.resolve import resolve_apply_urls

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    run_id: str
    total_discovered: int = 0
    gated_out: int = 0
    duplicates_skipped: int = 0
    scored: int = 0
    above_threshold: int = 0
    applied: int = 0
    needs_user_input: int = 0
    appended_to_sheet: int = 0
    review_file: Path | None = None
    report_file: Path | None = None
    jobs_for_review: list[Job] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan stage
# ---------------------------------------------------------------------------

_KNOWN_ATS = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters"}

# Sources that strongly suggest a startup / small company → short cover letter
_STARTUP_SOURCES = {"wellfound", "ycombinator", "yc", "weworkremotely"}


def _assign_plan(job: Job, cfg: dict) -> Job:
    """Choose route (auto/manual) and cover letter path."""
    # Route: auto only if we have a recognised ATS
    route = Route.auto if (job.ats or "").lower() in _KNOWN_ATS else Route.manual

    # Cover letter: short for startups/small companies, long otherwise
    source_lower = (job.source or "").lower()
    is_startup_signal = any(s in source_lower for s in _STARTUP_SOURCES)
    use_short = is_startup_signal or job.source_tier >= 3

    cover_key = "cover_letter_short" if use_short else "cover_letter_long"
    cover_path = cfg["apply"][cover_key]

    return job.model_copy(update={"route": route, "cover_letter": cover_path})


# ---------------------------------------------------------------------------
# Review file
# ---------------------------------------------------------------------------

def _write_review_file(jobs: list[Job], run_id: str, cfg: dict) -> Path:
    """Write the mandatory human-review markdown table."""
    out_dir = ROOT / cfg["paths"]["manual_queue"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}_review.md"

    lines = [
        f"# Review — {run_id}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Jobs above threshold: {len(jobs)}",
        "",
        "**Instructions:** strike through or delete rows to skip, then reply `apply` / `apply to #1, #3` to proceed.",
        "",
        "| # | Role | Company | Country | Type | Salary | Score | Apply Link |",
        "|---|------|---------|---------|------|--------|-------|------------|",
    ]

    for i, job in enumerate(jobs, 1):
        score = f"{job.score.total:.1f}" if job.score else "—"
        salary = job.salary or "—"
        remote = job.remote.value.title()
        lines.append(
            f"| {i} | {job.title} | {job.company} | {job.country} "
            f"| {remote} | {salary} | {score} | {job.apply_url} |"
        )

    path.write_text("\n".join(lines) + "\n")
    log.info("review file written → %s", path.relative_to(ROOT))
    return path


# ---------------------------------------------------------------------------
# Session report
# ---------------------------------------------------------------------------

def _write_report(result: PipelineResult, cfg: dict) -> Path:
    hist_dir = ROOT / cfg["paths"]["history"]
    hist_dir.mkdir(parents=True, exist_ok=True)
    path = hist_dir / f"{result.run_id}_report.md"

    country_counts: dict[str, int] = {}
    for job in result.jobs_for_review:
        country_counts[job.country] = country_counts.get(job.country, 0) + 1

    top = sorted(result.jobs_for_review, key=lambda j: j.score.total if j.score else 0, reverse=True)[:5]

    lines = [
        f"# Job Search Session Report — {result.run_id}",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Jobs Discovered | {result.total_discovered} |",
        f"| Gated Out | {result.gated_out} |",
        f"| Duplicates Skipped | {result.duplicates_skipped} |",
        f"| Scored | {result.scored} |",
        f"| Above Threshold | {result.above_threshold} |",
        f"| Applied | {result.applied} |",
        f"| Needs User Input | {result.needs_user_input} |",
        f"| Appended to Sheet | {result.appended_to_sheet} |",
        "",
        "## Review List",
        "",
        f"See: `{result.review_file.name if result.review_file else '—'}`",
        "",
        "| # | Role | Company | Country | Type | Salary | Score | Apply Link |",
        "|---|------|---------|---------|------|--------|-------|------------|",
    ]

    for i, job in enumerate(result.jobs_for_review, 1):
        score = f"{job.score.total:.1f}" if job.score else "—"
        lines.append(
            f"| {i} | {job.title} | {job.company} | {job.country} "
            f"| {job.remote.value.title()} | {job.salary or '—'} | {score} | {job.apply_url} |"
        )

    lines += [
        "",
        "## Country Breakdown",
        "",
    ]
    for country, count in sorted(country_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {country}: {count}")

    lines += [
        "",
        "## Top Opportunities",
        "",
    ]
    for job in top:
        score = f"{job.score.total:.1f}" if job.score else "—"
        rationale = job.score.rationale if job.score else ""
        lines.append(f"- **{job.title}** @ {job.company} ({job.country}) — Score {score}")
        if rationale:
            lines.append(f"  _{rationale}_")

    path.write_text("\n".join(lines) + "\n")
    log.info("report written → %s", path.relative_to(ROOT))
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _prefer_ats_within_batch(jobs: list[Job]) -> list[Job]:
    """
    Within a single discovery batch, for the same job_key keep whichever version
    has a recognised ATS URL (Greenhouse, Ashby, Lever, etc.).  This prevents a
    LinkedIn URL from shadowing an ATS URL when both adapters return the same job.
    """
    best: dict[str, Job] = {}
    for job in jobs:
        key = job.job_key
        if key not in best:
            best[key] = job
        else:
            existing = best[key]
            if job.ats is not None and existing.ats is None:
                best[key] = job  # upgrade to ATS version
    kept   = len(best)
    merged = len(jobs) - kept
    if merged:
        log.info("within-batch dedup: preferred ATS URL for %d job(s)", merged)
    return list(best.values())


def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run(jobs: list[Job], *, run_id: str | None = None) -> PipelineResult:
    """
    Run the full pipeline on a list of discovered Job objects.

    Callers (discover modules, CLI) inject jobs; this function handles
    everything from gating through review file generation.
    """
    cfg = load("config")
    run_id = run_id or _make_run_id()
    mode = cfg.get("mode", "review_first")
    threshold = cfg["score"]["threshold"]

    result = PipelineResult(run_id=run_id, total_discovered=len(jobs))
    log.info("pipeline run %s — %d jobs discovered", run_id, len(jobs))

    # 1. Initialise ledger
    ledger.init()

    # 2. Gate
    passed, gated = gate.run(jobs)
    result.gated_out = len(gated)

    # 2b. Within-batch dedup: for same job_key prefer ATS URL over LinkedIn URL
    passed = _prefer_ats_within_batch(passed)

    # 3. Dedupe against ledger
    new_jobs, dupes = ledger.filter_new(passed)
    result.duplicates_skipped = len(dupes)

    if not new_jobs:
        log.info("no new jobs after gate + dedupe — nothing to score")
        result.review_file = _write_review_file([], run_id, cfg)
        result.report_file = _write_report(result, cfg)
        return result

    # 4. Score
    scored_jobs = score_batch(new_jobs)
    result.scored = sum(1 for j in scored_jobs if j.status == Status.scored)

    # 5. Filter above threshold
    above = [j for j in scored_jobs if j.score and j.score.total >= threshold]
    result.above_threshold = len(above)

    # 5b. Resolve external apply URLs for above-threshold LinkedIn jobs
    above = resolve_apply_urls(above)

    # 6. Plan (assign cover letter + route)
    planned = [_assign_plan(j, cfg) for j in above]

    # 7. Record everything in ledger (including gated-out and dupes)
    ledger.record_batch(scored_jobs, run_id)

    # 8. Review file — always generated
    sorted_planned = sorted(planned, key=lambda j: j.score.total if j.score else 0, reverse=True)
    result.jobs_for_review = sorted_planned
    result.review_file = _write_review_file(sorted_planned, run_id, cfg)

    # 9. Stop here if review_first — human must confirm before applying
    if mode == "review_first":
        log.info(
            "mode=review_first — stopping. Review %s then confirm to apply.",
            result.review_file.name,
        )
        result.report_file = _write_report(result, cfg)
        _print_summary(result)
        return result

    # 10. full_auto: split by route, apply to auto jobs, queue manual ones
    auto_jobs   = [j for j in sorted_planned if j.route == Route.auto]
    manual_jobs = [j for j in sorted_planned if j.route != Route.auto]

    log.info("full_auto: %d auto-apply, %d manual", len(auto_jobs), len(manual_jobs))

    apply_results = apply_batch(auto_jobs, run_id)

    # Update ledger + job statuses from apply results
    applied_jobs: list[Job] = []
    queued_jobs: list[Job] = list(manual_jobs)

    result_by_key = {r.job_key: r for r in apply_results}
    for job in auto_jobs:
        ar = result_by_key.get(job.job_key)
        if ar:
            ledger.mark_status(job, ar.status, run_id)
            updated = job.model_copy(update={"status": ar.status})
            if ar.status == Status.applied:
                applied_jobs.append(updated)
                result.applied += 1
            else:
                queued_jobs.append(updated)
                result.needs_user_input += 1

    for job in manual_jobs:
        ledger.mark_status(job, Status.queued, run_id)

    # 11. Sync to sheet — applied + queued (manual)
    all_to_sync = [j.model_copy(update={"status": Status.applied}) for j in applied_jobs] + \
                  [j.model_copy(update={"status": Status.queued})  for j in queued_jobs]
    try:
        result.appended_to_sheet = append_jobs(all_to_sync, cfg)
    except Exception as exc:
        log.error("sheet sync failed: %s", exc)

    # 12. Report
    result.report_file = _write_report(result, cfg)
    _print_summary(result)
    return result


def _print_summary(result: PipelineResult) -> None:
    print(textwrap.dedent(f"""
    ── JobPilot Run {result.run_id} ──────────────────────────
      Discovered : {result.total_discovered}
      Gated out  : {result.gated_out}
      Duplicates : {result.duplicates_skipped}
      Scored     : {result.scored}
      Above {load("config")["score"]["threshold"]}    : {result.above_threshold}
      Applied    : {result.applied}
      Needs input: {result.needs_user_input}
      Sheet rows : {result.appended_to_sheet}

      Review → {result.review_file}
      Report → {result.report_file}
    ─────────────────────────────────────────────────────
    """).strip())
