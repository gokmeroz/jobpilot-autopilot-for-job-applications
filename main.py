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
    # "linkedin",  # enable once Apify actor ID is confirmed: python main.py --source linkedin
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
    from src.discover.ats.ashby import fetch as ashby_fetch
    from src.discover.ats.greenhouse import fetch as greenhouse_fetch
    from src.discover.ats.lever import fetch as lever_fetch
    from src.discover.scrapers.linkedin import fetch as linkedin_fetch

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


def _print_review_table(jobs: list) -> None:
    W = 108
    print(f"\n{'─' * W}")
    print(f"  {'#':<4} {'Score':<6} {'Role':<36} {'Company':<22} {'Ctry':<6} {'Type':<10} Salary")
    print(f"{'─' * W}")
    for i, job in enumerate(jobs, 1):
        score   = f"{job.score.total:.1f}" if job.score else "—"
        title   = (job.title[:33] + "…") if len(job.title) > 34 else job.title
        company = (job.company[:19] + "…") if len(job.company) > 20 else job.company
        salary  = job.salary or "—"
        print(f"  {i:<4} {score:<6} {title:<36} {company:<22} {job.country:<6} {job.remote.value:<10} {salary}")
    print(f"{'─' * W}")


def _interactive_review(result) -> None:
    from src import ledger
    from src.apply.runner import apply_batch
    from src.models import Route, Status

    jobs = result.jobs_for_review
    if not jobs:
        print("\nNo jobs above threshold — nothing to review.")
        return

    _print_review_table(jobs)

    print(f"\n  {len(jobs)} job(s) scored above threshold.")
    print("  Enter IDs to REMOVE (comma-separated), or press Enter / type 'none' to apply all:")
    print("  Example: 2,5,7   |   none = keep all   |   all = remove all")

    try:
        raw = input("\n  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return

    if raw in ("", "none", "null", "nan"):
        to_apply = list(jobs)
    elif raw == "all":
        print("Removed all — nothing to apply.")
        return
    else:
        try:
            remove_ids = {int(x.strip()) for x in raw.split(",") if x.strip()}
            to_apply = [j for i, j in enumerate(jobs, 1) if i not in remove_ids]
            removed = len(jobs) - len(to_apply)
            if removed:
                print(f"  Removed {removed} job(s).")
        except ValueError:
            print("  Could not parse input — keeping all jobs.")
            to_apply = list(jobs)

    if not to_apply:
        print("\nNo jobs remaining — nothing to apply.")
        return

    auto_jobs   = [j for j in to_apply if j.route == Route.auto]
    manual_jobs = [j for j in to_apply if j.route != Route.auto]

    # ── Auto-apply ────────────────────────────────────────────────────────────
    applied: list = []
    failed_auto: list = []

    if auto_jobs:
        print(f"\n  Auto-applying to {len(auto_jobs)} job(s)…")
        apply_results = apply_batch(auto_jobs, result.run_id)
        result_by_key = {r.job_key: r for r in apply_results}

        for job in auto_jobs:
            ar = result_by_key.get(job.job_key)
            if ar:
                ledger.mark_status(job, ar.status, result.run_id)
                if ar.status == Status.applied:
                    applied.append(job)
                else:
                    failed_auto.append(job)
                    manual_jobs.append(job)

    # ── Results ───────────────────────────────────────────────────────────────
    W = 108
    print(f"\n{'═' * W}")

    if applied:
        print(f"\n  ✓ Applied ({len(applied)})")
        for job in applied:
            print(f"    • {job.title} @ {job.company} ({job.country})")
            print(f"      {job.apply_url}")

    if manual_jobs:
        print(f"\n  ✎ Manual application required ({len(manual_jobs)})")
        print(f"  {'#':<4} {'Role':<36} {'Company':<22} {'Ctry':<6} {'Type':<10} Link")
        print(f"  {'─' * 100}")
        for i, job in enumerate(manual_jobs, 1):
            title   = (job.title[:33] + "…") if len(job.title) > 34 else job.title
            company = (job.company[:19] + "…") if len(job.company) > 20 else job.company
            print(f"  {i:<4} {title:<36} {company:<22} {job.country:<6} {job.remote.value:<10} {job.apply_url}")

    if not applied and not manual_jobs:
        print("\n  Nothing was applied.")

    print(f"\n{'═' * W}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jobpilot",
        description="Autonomous job search and application agent.",
    )
    parser.add_argument(
        "--source", "-s",
        choices=_ALL_SOURCES + ["linkedin", "all"],
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
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _setup_logging(args.verbose)

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
