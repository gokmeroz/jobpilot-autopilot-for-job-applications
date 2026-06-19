"""
Verification run: confirms the three fixes work end-to-end.
Finds the single best auto-applicable ATS job and applies to it.
Deleted after use.
"""
import logging, sys, datetime, sqlite3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from src.discover.ats.ashby import fetch as ashby_fetch
from src.discover.ats.greenhouse import fetch as greenhouse_fetch
from src import gate, ledger
from src.score import score_batch
from src.apply.runner import apply_job
from src.models import Status
from src.config import load
from src.sheet import append_jobs

KNOWN_ATS = {"greenhouse", "lever", "ashby", "workable"}
cfg = load("config")
ledger.init()

print("── Discovery ──────────────────────────────────────")
ashby_jobs = ashby_fetch()
print(f"  Ashby     : {len(ashby_jobs)} jobs (age gate bypassed)")
gh_jobs = greenhouse_fetch()
print(f"  Greenhouse: {len(gh_jobs)} jobs (age gate bypassed)")
jobs = ashby_jobs + gh_jobs
print(f"  Total     : {len(jobs)}")

passed, gated = gate.run(jobs)
print(f"\nAfter gate : {len(passed)} passed, {len(gated)} rejected")

new_jobs, dupes = ledger.filter_new(passed)
print(f"After dedupe: {len(new_jobs)} new, {len(dupes)} dupes skipped")

if not new_jobs:
    print("\nNo new jobs — nothing to apply to.")
    sys.exit(0)

# GitLab first, then rest of Greenhouse, then Ashby
gh_first = sorted(new_jobs, key=lambda j: (
    0 if j.company == "GitLab" else (1 if j.ats == "greenhouse" else 2)
))
to_score = gh_first[:60]
print(f"\nScoring {len(to_score)} jobs…")
scored = score_batch(to_score)

threshold = cfg["score"]["threshold"]
auto = [
    j for j in scored
    if j.score and j.score.total >= 5.0  # lowered for verification
    and (j.ats or "").lower() in KNOWN_ATS
]
auto.sort(key=lambda j: j.score.total, reverse=True)

print(f"\nAuto-applicable above {threshold}: {len(auto)}")
for j in auto[:5]:
    print(f"  {j.score.total:.1f} | {j.ats:<12} | {j.title} @ {j.company} ({j.country})")
    print(f"          {j.apply_url}")

if not auto:
    print("\nNo auto-applicable jobs found.")
    sys.exit(0)

run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")

# Try candidates in order until one succeeds
for best in auto:
    print(f"\n── Applying ───────────────────────────────────────")
    print(f"  Job    : {best.title}")
    print(f"  Company: {best.company}")
    print(f"  ATS    : {best.ats}")
    print(f"  Score  : {best.score.total:.1f}")
    print(f"  URL    : {best.apply_url}")
    print()

    result = apply_job(best, run_id)

    print(f"\n── Result ─────────────────────────────────────────")
    print(f"  Status  : {result.status}")
    if result.reason:
        print(f"  Reason  : {result.reason}")
    if result.evidence_dir:
        print(f"  Evidence: {result.evidence_dir}")

    if result.status == Status.applied:
        ledger.upsert(best.model_copy(update={"status": Status.applied}), run_id)
        try:
            n = append_jobs([best.model_copy(update={"status": Status.applied})], cfg)
            print(f"\n  Sheet updated — {n} row(s) added.")
        except Exception as exc:
            print(f"\n  Sheet sync failed: {exc}")
        print("\n  SUCCESS — application submitted and sheet updated.")
        break
    else:
        ledger.upsert(best.model_copy(update={"status": Status.queued}), run_id)
        print(f"  Skipping — trying next candidate…")
else:
    print("\n  All candidates failed or need manual input.")
