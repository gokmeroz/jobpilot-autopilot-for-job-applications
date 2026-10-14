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
    python main.py --source graduate      # only graduate programmes (JSearch + Adzuna)
    python main.py --dry-run              # discover + gate only, no LLM calls
    python main.py --countries TR         # this run only: keep Turkey jobs, drop abroad
    python main.py --eu --remote          # region presets: --turkey --eu --uk --eu-uk --remote --all
    python main.py --manual-only          # never auto-apply; everything goes to the manual queue
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
    "jsearch",
    "adzuna",
    "linkedin",
    "graduate",
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


def _discover(
    sources: list[str],
    run_id: str,
    *,
    force: bool = False,
    countries: list[str] | None = None,
) -> list:
    from src import ledger
    from src.discover.apis.arbeitnow import fetch as arbeitnow_fetch
    from src.discover.apis.remoteok import fetch as remoteok_fetch
    from src.discover.apis.remotive import fetch as remotive_fetch
    from src.discover.apis.weworkremotely import fetch as wwr_fetch
    from src.discover.apis.relocateme import fetch as relocateme_fetch
    from src.discover.apis.ukhired import fetch as ukhired_fetch
    from src.discover.apis.jsearch import fetch as jsearch_fetch
    from src.discover.apis.adzuna import fetch as adzuna_fetch
    from src.discover.apis.graduate import fetch as graduate_fetch
    from src.discover.ats.ashby import fetch as ashby_fetch
    from src.discover.ats.greenhouse import fetch as greenhouse_fetch
    from src.discover.ats.lever import fetch as lever_fetch
    from src.discover.scrapers.linkedin import fetch as linkedin_fetch
    from src.discover.scrapers.wellfound import fetch as wellfound_fetch
    from src.source_health import validate_source_health

    # (source_id, display label, fetch fn) — health-checked uniformly below.
    fetchers = [
        ("greenhouse", "Greenhouse", greenhouse_fetch),
        ("lever", "Lever", lever_fetch),
        ("ashby", "Ashby", ashby_fetch),
        ("arbeitnow", "Arbeitnow", arbeitnow_fetch),
        ("remotive", "Remotive", remotive_fetch),
        ("weworkremotely", "WeWorkRemotely", wwr_fetch),
        ("remoteok", "RemoteOK", remoteok_fetch),
        ("relocateme", "Relocate.me", relocateme_fetch),
        ("ukhired", "UKHired", ukhired_fetch),
        ("wellfound", "Wellfound (via Apify)", wellfound_fetch),
        ("jsearch", "JSearch (RapidAPI)", jsearch_fetch),
        ("adzuna", "Adzuna", adzuna_fetch),
        ("linkedin", "LinkedIn (via Apify)", linkedin_fetch),
        ("graduate", "graduate programmes (JSearch + Adzuna)", graduate_fetch),
    ]

    # Keyword-search sources only query the markets selected for this run;
    # company-board sources fetch everything and the gate filters by country.
    if countries is not None:
        from functools import partial
        _search_sources = {"jsearch", "adzuna", "linkedin", "graduate"}
        fetchers = [
            (sid, label, partial(fn, markets=countries) if sid in _search_sources else fn)
            for sid, label, fn in fetchers
        ]

    jobs = []
    health_results = []

    for source_id, label, fetch_fn in fetchers:
        if source_id not in sources:
            continue

        if not force and ledger.is_source_disabled(source_id):
            print(f"Skipping {label} — marked broken; run `--source {source_id}` to retry and re-enable.")
            continue

        print(f"Fetching from {label}…")
        source_jobs = fetch_fn()
        jobs += source_jobs

        health = validate_source_health(source_id, source_jobs, run_id, retry_fn=fetch_fn)
        health_results.append((label, health))
        if health.verdict != "healthy":
            print(f"  ⚠ source health: {health.verdict.upper()} — {health.reason}")

    if health_results:
        flagged = [(label, h) for label, h in health_results if h.verdict != "healthy"]
        if flagged:
            print(f"\n── Source Health ({len(flagged)} flagged) ─────────────────")
            for label, h in flagged:
                print(f"  {h.verdict.upper():<12} {label} — {h.reason}")
            print("────────────────────────────────────────────────────────")

    # Expand: for any job whose URL points to a new Greenhouse/Ashby/Lever
    # company not already in sources.yaml, fetch ALL their open jobs.
    if jobs:
        from src.discover.ats.expander import expand
        before = len(jobs)
        jobs = expand(jobs)
        added = len(jobs) - before
        if added:
            print(f"Expander: discovered {added} additional jobs from new ATS companies.")

    return jobs


def _dry_run(jobs: list, countries: list[str] | None = None) -> None:
    from src import gate, ledger

    ledger.init()
    passed, gated = gate.run(jobs, countries=countries)
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
            print(f"           {job.apply_url}")


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


def _interactive_review(result, manual_only: bool = False) -> None:
    from src import ledger
    from src.apply.runner import apply_batch
    from src.models import Route, Status

    all_jobs = result.jobs_for_review
    if not all_jobs:
        print("\nNo jobs above threshold — nothing to review.")
        return

    if manual_only:
        auto_candidates = []
        manual_candidates = list(all_jobs)
    else:
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
    elif manual_only:
        print(f"\n  ── STAGE 1: AUTO-APPLY ── skipped (--manual-only)")
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

    from src.apply.runner import _FILLERS
    from src.config import load as _load_cfg
    from src.normalize import _ATS_DOMAINS as _ALL_ATS_DOMAINS
    from src.models import Job, RemoteType, Route, Score, Status
    from src.normalize import build_job_key

    # Derived from runner._FILLERS so --resume-review routes all supported ATS
    # platforms to auto-apply, not just the original 5.
    _KNOWN_ATS: frozenset[str] = frozenset(_FILLERS.keys())
    _REMOTE_MAP: dict[str, RemoteType] = {
        "remote": RemoteType.remote,
        "hybrid": RemoteType.hybrid,
        "onsite": RemoteType.onsite,
        "unknown": RemoteType.unknown,
    }

    def _ats(url: str) -> str | None:
        try:
            host = _urlparse(url).netloc.lower()
            for domain, name in _ALL_ATS_DOMAINS.items():
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
        # | N | title | company | country | type | salary | score | url |
        # Pipes inside cells are written escaped (\|). Older files didn't escape
        # them, so a title like "Dev | AI Engineer" spills into extra cells —
        # anchor the other fields from the right and rejoin the spill into the title.
        if not line.startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|") for c in _re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(cells) < 8 or not cells[0].isdigit():
            continue
        *title_parts, company, country, remote_str, salary_raw, score_str, apply_url = cells[1:]
        title = " | ".join(title_parts)
        try:
            float(score_str)
        except ValueError:
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
        "--countries",
        metavar="CODES",
        default=None,
        help="Comma-separated country codes to keep for this run only, e.g. TR or "
             "TR,REMOTE (ISO2 or REMOTE). Everything else is gated out. Default: no restriction",
    )
    regions = parser.add_argument_group(
        "regions",
        "Limit the run to a region (combinable, e.g. --eu --remote; also unions with --countries). "
        "Narrows both the keyword searches and the country gate.",
    )
    for name, help_text in [
        ("turkey", "Turkey only (TR)"),
        ("eu",     "EU countries (searches DE, NL, IE)"),
        ("uk",     "United Kingdom only (GB)"),
        ("eu-uk",  "EU + United Kingdom"),
        ("remote", "Fully remote jobs only"),
        ("all",    "No country restriction (default)"),
    ]:
        regions.add_argument(
            f"--{name}", dest="regions", action="append_const", const=name, help=help_text,
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
        "--manual-only",
        action="store_true",
        help="Never auto-apply: every scored job goes to the manual queue + sheet. "
             "Same as mode: manual_only in config.yaml",
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

    from src.config import load as _load_cfg
    mode = _load_cfg("config").get("mode", "review_first")
    manual_only = args.manual_only or mode == "manual_only"

    # Replay a previous review file without re-running discovery/scoring
    if args.resume_review:
        from src.pipeline import PipelineResult
        run_id, jobs = _parse_review_file(args.resume_review)
        print(f"Replaying review file: {args.resume_review}")
        print(f"  Run ID : {run_id}")
        print(f"  Jobs   : {len(jobs)}")
        result = PipelineResult(run_id=run_id, jobs_for_review=jobs)
        _interactive_review(result, manual_only=manual_only)
        return 0

    from src import ledger
    from src.pipeline import _make_run_id

    ledger.init()
    run_id = args.run_id or _make_run_id()

    sources = _ALL_SOURCES if args.source == "all" else [args.source]
    explicit_single_source = args.source != "all"

    from src.regions import resolve as _resolve_regions
    countries = _resolve_regions(
        args.regions,
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None,
    )

    print(f"JobPilot — sources: {', '.join(sources)}"
          + (f" — regions: {', '.join(args.regions)}" if args.regions
             else f" — countries: {', '.join(countries)}" if countries else "")
          + (" [DRY RUN]" if args.dry_run else "")
          + (" [MANUAL ONLY]" if manual_only else ""))

    jobs = _discover(sources, run_id, force=explicit_single_source, countries=countries)

    if not jobs:
        print("No jobs discovered — check your network or source config.")
        return 1

    print(f"Discovered {len(jobs)} jobs total.")

    if args.dry_run:
        _dry_run(jobs, countries)
        return 0

    from src.pipeline import run as pipeline_run
    result = pipeline_run(jobs, run_id=run_id, countries=countries, manual_only=manual_only)

    if mode == "full_auto" and not manual_only:
        return 0

    _interactive_review(result, manual_only=manual_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
