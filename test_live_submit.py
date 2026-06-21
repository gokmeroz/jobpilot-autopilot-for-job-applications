#!/usr/bin/env python
"""
Live end-to-end submission test.
Job: Hostinger Backend Software Engineer (Node.js) — jobs.ashbyhq.com (Ashby ATS)
dry_run=false, headless=false  →  browser opens on screen, fills and submits
"""
import logging
import sys
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("live_test")
sys.path.insert(0, str(Path(__file__).parent))

from src.models import Job, Status, RemoteType

from src.apply.runner import apply_job

job = Job(
    job_key="hostinger__backend-software-engineer-nodejs__remote",
    title="Backend Software Engineer (Node.js)",
    company="Hostinger",
    country="REMOTE",
    location="Remote",
    remote=RemoteType.remote,
    source="manual_test",
    source_tier=1,
    ats="ashby",
    apply_url="https://jobs.ashbyhq.com/hostinger/f1a905a3-77f7-4ee5-bba3-1784f0b7633c/application",
    description=(
        "Backend Software Engineer at Hostinger Horizons. "
        "Build scalable backend systems and microservices. "
        "Node.js, TypeScript, cloud infrastructure."
    ),
)

RUN_ID = f"live_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
log.info("=== LIVE SUBMISSION TEST (ASHBY — HOSTINGER) ===")
log.info("run_id  : %s", RUN_ID)
log.info("job     : %s @ %s", job.title, job.company)
log.info("url     : %s", job.apply_url)
log.info("config  : dry_run=false  headless=false")

result = apply_job(job, run_id=RUN_ID)

print()
print("=" * 65)
print(f"  STATUS : {result.status.value}")
print(f"  JOB    : {result.job_key}")
if result.reason:
    print(f"  REASON : {result.reason}")
if result.evidence_dir and result.evidence_dir.exists():
    print(f"  SHOTS  : {result.evidence_dir}")
    for f in sorted(result.evidence_dir.glob("*.png")):
        print(f"    {f.name}")
print("=" * 65)

if result.status == Status.applied:
    print("\n  APPLIED — pipeline is end-to-end working!")
elif "NEEDS_USER_INPUT" in (result.reason or ""):
    print("\n  BLOCKED on a question — check 99_blocked.png")
else:
    print(f"\n  Unexpected status: {result.status}")
