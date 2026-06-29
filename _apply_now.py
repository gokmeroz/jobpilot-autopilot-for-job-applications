"""One-shot: apply to jobs approved from today's review queue."""
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

from src.apply.runner import apply_job
from src import ledger as _ledger
from src.config import load as _load_cfg
from src.models import Job, RemoteType, Route, Status
from src.normalize import build_job_key

RUN_ID = "20260629_manual"
CFG = _load_cfg("config")

JOBS = [
    Job(
        job_key     = build_job_key("N26", "Backend Engineer - Investments & Savings", "DE"),
        title       = "Backend Engineer - Investments & Savings",
        company     = "N26",
        country     = "DE",
        location    = "Berlin, Germany",
        remote      = RemoteType.hybrid,
        posted_at   = datetime.now(timezone.utc),
        source      = "greenhouse",
        source_tier = 1,
        ats         = "greenhouse",
        apply_url   = "https://n26.com/en-eu/careers/positions/8020517?gh_jid=8020517",
        route       = Route.auto,
        status      = Status.scored,
        description = (
            "N26 is a European digital bank headquartered in Berlin. "
            "Backend Engineer on the Investments & Savings team — build financial products "
            "using Java, Kotlin, and AWS at scale across Europe. "
            "Hybrid role based in Berlin with relocation support."
        ),
    ),
]

for job in JOBS:
    print(f"\n── Applying: {job.title} @ {job.company} ({job.country}) ──")
    print(f"   URL: {job.apply_url}")
    result = apply_job(job, run_id=RUN_ID)
    print(f"   status : {result.status.value}")
    print(f"   reason : {result.reason or '—'}")
    if result.evidence_dir and result.evidence_dir.exists():
        for s in sorted(result.evidence_dir.glob("*.png")):
            print(f"   screenshot: {s.name}")
    # Update ledger
    _ledger.mark_status(job, result.status, RUN_ID)

print("\nDone.")
