"""
Quick smoke-test for the auto-apply form fillers.

Runs with dry_run=true and headless=false so you can watch the browser fill
the form without submitting anything.

Usage:
    python test_apply.py                  # tests Ashby (PostHog)
    python test_apply.py greenhouse       # tests Greenhouse (Webflow)
    python test_apply.py ashby            # tests Ashby (PostHog)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from src.apply.runner import apply_job
from src.config import load as _load_cfg
from src.models import Job, RemoteType, Route, Status
from src.normalize import build_job_key

# ── Test jobs (real URLs, won't be submitted — dry_run=true in config) ────────

_TEST_JOBS: dict[str, Job] = {
    "ashby": Job(
        job_key     = build_job_key("Checkout.com", "Software Engineer I", "REMOTE"),
        title       = "Software Engineer I",
        company     = "Checkout.com",
        country     = "REMOTE",
        location    = "Remote",
        remote      = RemoteType.remote,
        posted_at   = datetime.now(timezone.utc),
        source      = "ashby",
        source_tier = 1,
        ats         = "ashby",
        apply_url   = "https://jobs.ashbyhq.com/checkout.com/62246814-691c-49c9-b574-2cb03f6e7f38",
        route       = Route.auto,
        status      = Status.scored,
        description = (
            "Checkout.com is a global payments infrastructure company. "
            "As a Software Engineer I you will build payment processing systems "
            "using TypeScript, Node.js, and distributed systems at global scale."
        ),
    ),
    "greenhouse": Job(
        job_key     = build_job_key("Figma", "Software Engineer, AI Product", "GB"),
        title       = "Software Engineer, AI Product",
        company     = "Figma",
        country     = "GB",
        location    = "London, United Kingdom",
        remote      = RemoteType.onsite,
        posted_at   = datetime.now(timezone.utc),
        source      = "greenhouse",
        source_tier = 1,
        ats         = "greenhouse",
        apply_url   = "https://boards.greenhouse.io/figma/jobs/5551697004",
        route       = Route.auto,
        status      = Status.scored,
        description = (
            "Figma is building the next generation of design tools with AI at the core. "
            "As a Software Engineer on the AI Product team you will build AI-powered features "
            "that help designers and developers work faster. "
            "You will work across the full stack — TypeScript, React, Node.js — integrating "
            "LLMs, building product surfaces, and collaborating closely with design. "
            "We are looking for engineers who care deeply about product quality, "
            "move fast, and are excited about AI's potential to transform creative tools."
        ),
    ),
}

def main() -> None:
    ats = sys.argv[1].lower() if len(sys.argv) > 1 else "ashby"
    job = _TEST_JOBS.get(ats)
    if not job:
        print(f"Unknown ATS '{ats}'. Choose: {list(_TEST_JOBS)}")
        sys.exit(1)

    cfg = _load_cfg("config")
    dry_run = cfg["apply"].get("dry_run", True)
    headless = cfg["apply"].get("headless", True)

    print(f"\nTesting {ats.upper()} form filler")
    print(f"  Job      : {job.title} @ {job.company}")
    print(f"  URL      : {job.apply_url}")
    print(f"  dry_run  : {dry_run}  ({'form fills but never submits' if dry_run else 'WILL SUBMIT FOR REAL'})")
    print(f"  headless : {headless}\n")

    result = apply_job(job, run_id="test_apply")

    print(f"\nResult:")
    print(f"  status : {result.status}")
    print(f"  reason : {result.reason or '—'}")
    if result.evidence_dir:
        shots = list(result.evidence_dir.glob("*.png"))
        print(f"  screenshots: {len(shots)}")
        for s in sorted(shots):
            print(f"    {s}")

if __name__ == "__main__":
    main()
