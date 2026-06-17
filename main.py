"""
JobPilot CLI

Usage:
    python main.py                        # run all sources
    python main.py --source greenhouse    # only Greenhouse companies
    python main.py --source arbeitnow     # only Arbeitnow
    python main.py --source lever         # only Lever companies
    python main.py --source ashby         # only Ashby companies
    python main.py --source remotive      # only Remotive API
    python main.py --source weworkremotely  # only WeWorkRemotely RSS
    python main.py --source remoteok      # only RemoteOK API
    python main.py --source relocateme    # only Relocate.me (EU relocation jobs)
    python main.py --source ukhired       # only UKHired (UK visa sponsorship jobs)
    python main.py --source wellfound     # only Wellfound via Apify (needs APIFY_TOKEN)
    python main.py --source linkedin      # only LinkedIn via Apify (needs APIFY_TOKEN)
    python main.py --dry-run              # discover + gate only, no LLM calls
    python main.py --run-id my-run-001    # override the run ID
    python main.py -v                     # verbose logging
"""
from __future__ import annotations

import argparse
import logging
import sys

_ALL_SOURCES = [
    "greenhouse",
    "lever",
    "ashby",
    "arbeitnow",
    "remotive",
    "weworkremotely",
    "remoteok",
    "relocateme",
    "ukhired",
    "wellfound",
    "linkedin",
]


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.WARNING)


def _discover(sources: list[str]) -> list:
    from src.discover.apis.arbeitnow import fetch as arbeitnow_fetch
    from src.discover.apis.remoteok import fetch as remoteok_fetch
    from src.discover.apis.remotive import fetch as remotive_fetch
    from src.discover.apis.weworkremotely import fetch as wwr_fetch
    from src.discover.apis.relocateme import fetch as relocateme_fetch
    from src.discover.apis.ukhired import fetch as ukhired_fetch
    from src.discover.ats.ashby import fetch as ashby_fetch
    from src.discover.ats.greenhouse import fetch as greenhouse_fetch
    from src.discover.ats.lever import fetch as lever_fetch
    from src.discover.scrapers.linkedin import fetch as linkedin_fetch
    from src.discover.scrapers.wellfound import fetch as wellfound_fetch

    jobs = []

    if "greenhouse" in sources:
        print("Fetching from Greenhouse…")
        jobs += greenhouse_fetch()

    if "lever" in sources:
        print("Fetching from Lever…")
        jobs += lever_fetch()

    if "ashby" in sources:
        print("Fetching from Ashby…")
        jobs += ashby_fetch()

    if "arbeitnow" in sources:
        print("Fetching from Arbeitnow…")
        jobs += arbeitnow_fetch()

    if "remotive" in sources:
        print("Fetching from Remotive…")
        jobs += remotive_fetch()

    if "weworkremotely" in sources:
        print("Fetching from WeWorkRemotely…")
        jobs += wwr_fetch()

    if "remoteok" in sources:
        print("Fetching from RemoteOK…")
        jobs += remoteok_fetch()

    if "relocateme" in sources:
        print("Fetching from Relocate.me…")
        jobs += relocateme_fetch()

    if "ukhired" in sources:
        print("Fetching from UKHired…")
        jobs += ukhired_fetch()

    if "wellfound" in sources:
        print("Fetching from Wellfound (via Apify)…")
        jobs += wellfound_fetch()

    if "linkedin" in sources:
        print("Fetching from LinkedIn (via Apify)…")
        jobs += linkedin_fetch()

    return jobs


def _dry_run(jobs: list) -> None:
    from src import gate, ledger

    ledger.init()
    passed, gated = gate.run(jobs)
    new, dupes = ledger.filter_new(passed)

    print(f"\n── Dry Run Results ───────────────────────────────")
    print(f"  Discovered : {len(jobs)}")
    print(f"  Gated out  : {len(gated)}")
    print(f"  Duplicates : {len(dupes)}")
    print(f"  Would score: {len(new)}")
    print(f"──────────────────────────────────────────────────")

    if new:
        print("\nJobs that would be scored:")
        for job in new:
            age = f"{job.age_hours():.0f}h" if job.age_hours() is not None else "age?"
            print(f"  [{age:>5}] {job.title} @ {job.company} ({job.country})")


W = 108  # table width constant


def _print_review_table(jobs: list, offset: int = 0) -> None:
    print(f"\n{'─' * W}")
    print(f"  {'#':<4} {'Score':<6} {'ATS':<14} {'Role':<34} {'Company':<20} {'Ctry':<6} Salary")
    print(f"{'─' * W}")
    for i, job in enumerate(jobs, offset + 1):
        score   = f"{job.score.total:.1f}" if job.score else "—"
        ats     = (job.ats or "—")[:13]
        title   = (job.title[:31] + "…") if len(job.title) > 32 else job.title
        company = (job.company[:17] + "…") if len(job.company) > 18 else job.company
        salary  = job.salary or "—"
        print(f"  {i:<4} {score:<6} {ats:<14} {title:<34} {company:<20} {job.country:<6} {salary}")
    print(f"{'─' * W}")


def _ask_remove(jobs: list, offset: int = 0) -> list:
    """Prompt user to remove jobs by number. Returns the kept list."""
    print("  Enter IDs to REMOVE (comma-separated), Enter / 'none' = keep all, 'all' = skip all:")
    try:
        raw = input("\n  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return []

    if raw == "all":
        return []
    if raw in ("", "none", "null"):
        return list(jobs)
    try:
        remove_ids = {int(x.strip()) - offset for x in raw.split(",") if x.strip()}
        kept = [j for i, j in enumerate(jobs, 1) if i not in remove_ids]
        removed = len(jobs) - len(kept)
        if removed:
            print(f"  Removed {removed} job(s).")
        return kept
    except ValueError:
        print("  Could not parse — keeping all.")
        return list(jobs)


def _sync_sheet(applied: list, queued: list) -> None:
    from src.config import load as _load_cfg
    from src.models import Status as _Status
    from src.sheet import append_jobs as _append_jobs

    sheet_jobs = (
        [j.model_copy(update={"status": _Status.applied}) for j in applied] +
        [j.model_copy(update={"status": _Status.queued})  for j in queued]
    )
    if not sheet_jobs:
        return
    try:
        n = _append_jobs(sheet_jobs, _load_cfg("config"))
        if n:
            print(f"\n  Sheet updated — {n} row(s) added.\n")
    except Exception as exc:
        print(f"\n  Sheet sync failed: {exc}\n")


def _interactive_review(result) -> None:
    from src import ledger
    from src.apply.runner import apply_batch
    from src.models import Route, Status

    all_jobs = result.jobs_for_review
    if not all_jobs:
        print("\nNo jobs above threshold — nothing to review.")
        return

    auto_candidates = [j for j in all_jobs if j.route == Route.auto]
    manual_candidates = [j for j in all_jobs if j.route != Route.auto]

    applied:      list = []
    needs_manual: list = []   # auto-apply failures fall back here
    queued:       list = []   # user-approved manual jobs

    # ════════════════════════════════════════════════════════════════════════
    # STAGE 1 — AUTO-APPLY
    # ════════════════════════════════════════════════════════════════════════
    print(f"\n{'═' * W}")
    if auto_candidates:
        print(f"\n  ── STAGE 1: AUTO-APPLY ({len(auto_candidates)} job(s)) ──")
        print("  These will be submitted automatically via ATS form filler.")
        _print_review_table(auto_candidates)
        to_auto = _ask_remove(auto_candidates)

        if to_auto:
            print(f"\n  Auto-applying to {len(to_auto)} job(s)…")
            apply_results = apply_batch(to_auto, result.run_id)
            result_by_key = {r.job_key: r for r in apply_results}

            for job in to_auto:
                ar = result_by_key.get(job.job_key)
                if ar:
                    ledger.mark_status(job, ar.status, result.run_id)
                    if ar.status == Status.applied:
                        applied.append(job)
                    else:
                        needs_manual.append(job)

        # Print auto-apply results
        if applied:
            print(f"\n  ✓ Auto-applied ({len(applied)}):")
            for job in applied:
                print(f"    • {job.title} @ {job.company} ({job.country})")
                print(f"      {job.apply_url}")

        if needs_manual:
            print(f"\n  ✗ Auto-apply failed — moved to manual ({len(needs_manual)}):")
            for job in needs_manual:
                reason = getattr(job, "fail_reason", "") or ""
                print(f"    • {job.title} @ {job.company} — {reason[:80]}")
    else:
        print(f"\n  ── STAGE 1: AUTO-APPLY ──")
        print("  No auto-apply jobs this run. (Need Greenhouse/Ashby/Lever/Workable URL + score > 6.5)")

    _sync_sheet(applied, [])

    # ════════════════════════════════════════════════════════════════════════
    # STAGE 2 — MANUAL
    # ════════════════════════════════════════════════════════════════════════
    manual_all = manual_candidates + needs_manual
    print(f"\n{'═' * W}")
    if manual_all:
        print(f"\n  ── STAGE 2: MANUAL APPLICATION ({len(manual_all)} job(s)) ──")
        print("  Review the list. Remove any you don't want added to the sheet.")
        _print_review_table(manual_all)
        to_queue = _ask_remove(manual_all)

        if to_queue:
            queued = to_queue
            print(f"\n  Queued {len(queued)} job(s) for manual application:")
            for i, job in enumerate(queued, 1):
                title   = (job.title[:33] + "…") if len(job.title) > 34 else job.title
                company = (job.company[:19] + "…") if len(job.company) > 20 else job.company
                print(f"  {i:<3} {title:<35} {company:<21} {job.country}  {job.apply_url}")
        else:
            print("  No manual jobs queued.")
    else:
        print(f"\n  ── STAGE 2: MANUAL APPLICATION ──")
        print("  No manual jobs this run.")

    print(f"\n{'═' * W}\n")

    _sync_sheet([], queued)


def _parse_review_file(path: str):
    """Reconstruct Job objects from a manual_queue review markdown file."""
    import re as _re
    from pathlib import Path as _Path
    from urllib.parse import urlparse as _urlparse

    from src.config import load as _load_cfg
    from src.models import Job, RemoteType, Route, Score, Status
    from src.normalize import build_job_key

    _ATS_DOMAINS: dict[str, str] = {
        "greenhouse.io": "greenhouse",
        "lever.co": "lever",
        "ashbyhq.com": "ashby",
        "workable.com": "workable",
        "smartrecruiters.com": "smartrecruiters",
    }
    _KNOWN_ATS = set(_ATS_DOMAINS.values())
    _REMOTE_MAP: dict[str, RemoteType] = {
        "remote": RemoteType.remote,
        "hybrid": RemoteType.hybrid,
        "onsite": RemoteType.onsite,
        "unknown": RemoteType.unknown,
    }

    def _ats(url: str) -> str | None:
        try:
            host = _urlparse(url).netloc.lower()
            for domain, name in _ATS_DOMAINS.items():
                if domain in host:
                    return name
        except Exception:
            pass
        return None

    p = _Path(path)
    run_id = p.stem.replace("_review", "")
    cfg = _load_cfg("config")
    cover_letter = cfg["apply"]["cover_letter_short"]

    jobs: list[Job] = []
    for line in p.read_text().splitlines():
        # New format: | N | score | ats | title | company | country | salary | (no url in review file)
        # Old format: | N | title | company | country | type | salary | score | url |
        # Try old format first (review .md files have the url)
        m = _re.match(
            r"\|\s*\d+\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*([\d.]+)\s*\|\s*(.+?)\s*\|",
            line,
        )
        if not m:
            continue
        title, company, country, remote_str, salary_raw, score_str, apply_url = m.groups()
        if title in ("Role", "#", "Score"):
            continue
        salary = salary_raw.strip() if salary_raw.strip() not in ("—", "-", "") else None
        remote_type = _REMOTE_MAP.get(remote_str.strip().lower(), RemoteType.unknown)
        score_total = float(score_str)
        detected_ats = _ats(apply_url.strip())
        route = Route.auto if detected_ats in _KNOWN_ATS else Route.manual
        jobs.append(Job(
            job_key=build_job_key(company, title, country),
            title=title,
            company=company,
            country=country,
            location=country,
            remote=remote_type,
            source="replay",
            source_tier=2,
            ats=detected_ats,
            apply_url=apply_url.strip(),
            salary=salary,
            language="EN",
            description="",
            visa_signal=False,
            score=Score(role=score_total, stack=score_total, seniority=score_total,
                        visa=score_total, culture=score_total, total=score_total,
                        rationale="Loaded from review file"),
            status=Status.scored,
            route=route,
            cover_letter=cover_letter,
        ))
    return run_id, jobs


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jobpilot",
        description="Autonomous job search and application agent.",
    )
    parser.add_argument(
        "--source", "-s",
        choices=_ALL_SOURCES + ["all"],
        default="all",
        help="Which source to discover from (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and gate only — no LLM scoring, no sheet writes",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Override the auto-generated run ID",
    )
    parser.add_argument(
        "--resume-review",
        metavar="PATH",
        default=None,
        help="Replay a previous review file: parse jobs from it and go straight to apply",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _setup_logging(args.verbose)

    # Replay a previous review file without re-running discovery/scoring
    if args.resume_review:
        from src.pipeline import PipelineResult
        run_id, jobs = _parse_review_file(args.resume_review)
        print(f"Replaying review file: {args.resume_review}")
        print(f"  Run ID : {run_id}")
        print(f"  Jobs   : {len(jobs)}")
        result = PipelineResult(run_id=run_id, jobs_for_review=jobs)
        _interactive_review(result)
        return 0

    sources = _ALL_SOURCES if args.source == "all" else [args.source]

    print(f"JobPilot — sources: {', '.join(sources)}"
          + (" [DRY RUN]" if args.dry_run else ""))

    jobs = _discover(sources)

    if not jobs:
        print("No jobs discovered — check your network or source config.")
        return 1

    print(f"Discovered {len(jobs)} jobs total.")

    if args.dry_run:
        _dry_run(jobs)
        return 0

    from src.pipeline import run as pipeline_run
    result = pipeline_run(jobs, run_id=args.run_id)

    _interactive_review(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
