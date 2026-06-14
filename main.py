"""
JobPilot CLI

Usage:
    python main.py                        # run all sources
    python main.py --source greenhouse    # only Greenhouse companies
    python main.py --source arbeitnow     # only Arbeitnow
    python main.py --dry-run              # discover + gate only, no LLM calls
    python main.py --run-id my-run-001    # override the run ID
    python main.py -v                     # verbose logging
"""
from __future__ import annotations

import argparse
import logging
import sys


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.WARNING)


def _discover(sources: list[str]) -> list:
    from src.discover.apis.arbeitnow import fetch as arbeitnow_fetch
    from src.discover.ats.greenhouse import fetch as greenhouse_fetch

    jobs = []

    if "greenhouse" in sources:
        print("Fetching from Greenhouse…")
        jobs += greenhouse_fetch()

    if "arbeitnow" in sources:
        print("Fetching from Arbeitnow…")
        jobs += arbeitnow_fetch()

    return jobs


def _dry_run(jobs: list) -> None:
    """Gate only — no LLM calls, no sheet writes."""
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
        print("\nSample jobs that would be scored:")
        for job in new[:10]:
            age = f"{job.age_hours():.0f}h" if job.age_hours() is not None else "age?"
            print(f"  [{age:>5}] {job.title} @ {job.company} ({job.country})")
        if len(new) > 10:
            print(f"  … and {len(new) - 10} more")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jobpilot",
        description="Autonomous job search and application agent.",
    )
    parser.add_argument(
        "--source", "-s",
        choices=["greenhouse", "arbeitnow", "all"],
        default="all",
        help="Which source(s) to discover from (default: all)",
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

    sources = ["greenhouse", "arbeitnow"] if args.source == "all" else [args.source]

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
    pipeline_run(jobs, run_id=args.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
