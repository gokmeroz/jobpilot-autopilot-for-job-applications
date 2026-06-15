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
from src.models import Job, RemoteType, Route, Status
from src.normalize import build_job_key

# ── Test jobs (real URLs, won't be submitted — dry_run=true in config) ────────

_TEST_JOBS: dict[str, Job] = {
    "ashby": Job(
        job_key     = build_job_key("Linear", "Product Engineer", "REMOTE"),
        title       = "Product Engineer",
        company     = "Linear",
        country     = "REMOTE",
        location    = "Remote, North America",
        remote      = RemoteType.remote,
        posted_at   = datetime.now(timezone.utc),
        source      = "ashby",
        source_tier = 1,
        ats         = "ashby",
        apply_url   = "https://jobs.ashbyhq.com/linear/0c7c2e26-0a98-42cf-a47c-9a3999fb513b",
        route       = Route.auto,
        status      = Status.scored,
        description = (
            "Linear is a purpose-built tool for planning and building products. "
            "We help software teams move faster by combining issue tracking, project management, "
            "and roadmaps into one streamlined workflow. "
            "As a Product Engineer you will build features end-to-end, "
            "own the full stack from frontend to backend, and collaborate closely with design. "
            "We are looking for engineers who care deeply about product quality and user experience."
        ),
    ),
    "greenhouse": Job(
        job_key     = build_job_key("Figma", "Data Engineer", "REMOTE"),
        title       = "Data Engineer",
        company     = "Figma",
        country     = "REMOTE",
        location    = "Remote",
        remote      = RemoteType.remote,
        posted_at   = datetime.now(timezone.utc),
        source      = "greenhouse",
        source_tier = 1,
        ats         = "greenhouse",
        apply_url   = "https://boards.greenhouse.io/figma/jobs/5220003004",
        route       = Route.auto,
        status      = Status.scored,
        description = (
            "Figma is building tools to make design accessible to everyone. "
            "As a Data Engineer you will build and maintain pipelines that power "
            "product analytics, experimentation, and business intelligence. "
            "You will work with large-scale data infrastructure using Python, SQL, "
            "Spark, and cloud data warehouses."
        ),
    ),
}

def main() -> None:
    ats = sys.argv[1].lower() if len(sys.argv) > 1 else "ashby"
    job = _TEST_JOBS.get(ats)
    if not job:
        print(f"Unknown ATS '{ats}'. Choose: {list(_TEST_JOBS)}")
        sys.exit(1)

    print(f"\nTesting {ats.upper()} form filler")
    print(f"  Job    : {job.title} @ {job.company}")
    print(f"  URL    : {job.apply_url}")
    print(f"  dry_run: True  (form fills but never submits)")
    print(f"  headless: False  (browser window will open)\n")

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
