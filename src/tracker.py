"""
Persistent job tracker (SQLite table `tracker` in the ledger DB).

One row per scored opportunity with the fields of the search brief, plus
reconciliation between runs: new / still open / closed / description changed /
newly found sponsorship / already applied.

application_status starts as "Not Applied" and only changes when JobPilot
itself submits the application (auto-apply success) or the user sets it with
`jobpilot --set-status <job_id>=<status>`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone

from src import ledger
from src.models import Job

STATUSES = ["Not Applied", "Applied", "Assessment", "Interview", "Rejected", "Offer", "Closed"]

FIELDS = [
    "job_id", "company", "title", "location", "country", "remote_type", "source", "source_url",
    "apply_url", "posted_at", "first_seen_at", "last_verified_at", "fit_score", "fit_class",
    "match_reasons", "skill_gaps", "sponsorship_status", "sponsorship_evidence", "sponsorship_evidence_url",
    "salary", "recommended_resume", "application_status", "verification", "desc_hash", "closed_at",
    # Australia-specific
    "offshore_eligibility", "australian_visa_pathway", "salary_eligibility", "sponsor_evidence_type",
]

_DDL = "CREATE TABLE IF NOT EXISTS tracker (" + ", ".join(
    f"{f} TEXT" + (" PRIMARY KEY" if f == "job_id" else "") for f in FIELDS
) + ");\nCREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, started_at TEXT, window_hours REAL);"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    with ledger._conn() as con:
        con.executescript(_DDL)


def last_run_at() -> datetime | None:
    init()
    with ledger._conn() as con:
        row = con.execute("SELECT MAX(started_at) AS t FROM runs").fetchone()
    return datetime.fromisoformat(row["t"]) if row and row["t"] else None


def record_run(run_id: str, window_hours: float) -> None:
    init()
    with ledger._conn() as con:
        con.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?)", (run_id, _now(), window_hours))


def _desc_hash(job: Job) -> str:
    return hashlib.sha256(" ".join((job.description or "").split()).encode()).hexdigest()[:16]


def _row(job: Job) -> dict:
    sp, fit, sc = job.sponsorship, job.fit, job.score
    return {
        "job_id": job.job_key, "company": job.company, "title": job.title,
        "location": job.location or "", "country": job.country, "remote_type": job.remote.value,
        "source": job.source, "source_url": job.apply_url, "apply_url": job.apply_url,
        "posted_at": job.posted_at.isoformat() if job.posted_at else "",
        "last_verified_at": _now() if job.verification else "",
        "fit_score": str(int(sc.total)) if sc else "", "fit_class": fit.fit_class if fit else "",
        "match_reasons": "; ".join(fit.matching_skills) if fit else "",
        "skill_gaps": "; ".join((fit.missing_mandatory + fit.missing_preferred)) if fit else "",
        "sponsorship_status": sp.category if sp else "", "sponsorship_evidence": sp.evidence if sp else "",
        "sponsorship_evidence_url": (sp.evidence_url or "") if sp else "",
        "salary": job.salary or "", "recommended_resume": job.recommended_resume or "",
        "verification": job.verification or "", "desc_hash": _desc_hash(job),
        "offshore_eligibility": sp.offshore if sp and job.country == "AU" else "",
        "australian_visa_pathway": sp.visa_pathway if sp and job.country == "AU" else "",
        "salary_eligibility": sp.salary_eligibility if sp and job.country == "AU" else "",
        "sponsor_evidence_type": sp.evidence_type if sp and job.country == "AU" else "",
    }


def all_rows() -> list[dict]:
    init()
    with ledger._conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM tracker ORDER BY CAST(fit_score AS REAL) DESC")]


def reconcile(jobs: list[Job], seen_keys: set[str], crawled_boards: set[tuple[str, str]]) -> dict[str, list[dict]]:
    """Upsert this run's scored jobs and diff against the previous tracker state.

    seen_keys: every job_key discovered this run (before gating).
    crawled_boards: (source, company) ATS boards fetched successfully this run —
    a tracked job from one of them that is no longer listed is Closed.
    """
    init()
    prev = {r["job_id"]: r for r in all_rows()}
    changes: dict[str, list[dict]] = {k: [] for k in
                                      ("new", "still_open", "closed", "changed", "new_sponsorship", "applied")}
    now = _now()
    with ledger._conn() as con:
        for job in jobs:
            row = _row(job)
            old = prev.get(job.job_key)
            if old is None:
                row.update(first_seen_at=now, application_status="Not Applied", closed_at="")
                changes["new"].append(row)
            else:
                row.update(first_seen_at=old["first_seen_at"], application_status=old["application_status"],
                           closed_at="")
                changes["still_open"].append(row)
                if old["desc_hash"] and old["desc_hash"] != row["desc_hash"]:
                    changes["changed"].append(row)
                if old["sponsorship_status"] in ("C", "") and row["sponsorship_status"] in ("A", "B"):
                    changes["new_sponsorship"].append(row)
            con.execute(f"INSERT OR REPLACE INTO tracker ({', '.join(FIELDS)}) VALUES ({', '.join('?' * len(FIELDS))})",
                        [row.get(f, "") for f in FIELDS])

        current = {j.job_key for j in jobs}
        for jid, old in prev.items():
            if jid in current or old["closed_at"]:
                continue
            if jid in seen_keys:   # rediscovered (deduped, not rescored) → still open
                changes["still_open"].append(old)
                continue
            if (old["source"], old["company"]) in crawled_boards:
                con.execute("UPDATE tracker SET closed_at = ?, application_status = CASE WHEN application_status = "
                            "'Not Applied' THEN 'Closed' ELSE application_status END WHERE job_id = ?", (now, jid))
                changes["closed"].append(old)

        for r in con.execute("SELECT job_key FROM jobs WHERE status = 'applied'").fetchall():
            t = con.execute("SELECT * FROM tracker WHERE job_id = ?", (r["job_key"],)).fetchone()
            if t and t["application_status"] == "Not Applied":
                con.execute("UPDATE tracker SET application_status = 'Applied' WHERE job_id = ?", (r["job_key"],))
                changes["applied"].append(dict(t))
    return changes


def set_status(job_id: str, status: str) -> bool:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    init()
    with ledger._conn() as con:
        return con.execute("UPDATE tracker SET application_status = ? WHERE job_id = ?", (status, job_id)).rowcount > 0


def mark_applied(job_keys: list[str]) -> None:
    init()
    with ledger._conn() as con:
        for k in job_keys:
            con.execute("UPDATE tracker SET application_status = 'Applied' WHERE job_id = ? "
                        "AND application_status = 'Not Applied'", (k,))


_EXPORT = [f for f in FIELDS if f not in ("desc_hash",)]


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_EXPORT, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def to_json(rows: list[dict]) -> str:
    return json.dumps([{k: r.get(k, "") for k in _EXPORT} for r in rows], indent=2, ensure_ascii=False)
