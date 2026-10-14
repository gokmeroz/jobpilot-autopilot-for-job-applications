"""
Per-source health checking for the Discover stage.

Job-board scrapers rot silently: a markup change can make a parser return
null/empty fields or zero results while still exiting 0, so the pipeline
keeps running and just collects junk (or nothing) without anyone noticing.
This module gives each source a verdict after every discover run —
healthy / degraded / broken / inconclusive — persisted to the ledger so
yield trends are visible across runs, not just within one.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from src import ledger
from src.config import load
from src.models import Job

log = logging.getLogger(__name__)

# Sources whose adapter is expected to only ever return URLs on a fixed
# domain — used for the "result URLs off the source's own domain" degraded
# signal. Aggregator/API sources (JSearch, Adzuna, Remotive, Arbeitnow, ...)
# legitimately return arbitrary employer domains, so they're absent here.
_EXPECTED_DOMAIN: dict[str, str] = {
    "greenhouse": "greenhouse.io",
    "ashby": "ashbyhq.com",
    "lever": "lever.co",
    "linkedin": "linkedin.com",
    "wellfound": "wellfound.com",
}

_HTML_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|#39|nbsp|#\d+);")

_MAX_ESCALATION_ATTEMPTS = 2  # canonical-query retry, then one more before giving up
_RATE_LIMIT_RE = re.compile(r"\b429\b|rate.?limit|too many requests", re.IGNORECASE)


@dataclass
class HealthResult:
    source_id: str
    verdict: str  # "healthy" | "degraded" | "broken" | "inconclusive"
    reason: str = ""
    result_count: int = 0
    historical_avg: float | None = None


def _degraded_reason(source_id: str, jobs: list[Job]) -> str | None:
    """Field-quality signal: company/title null or empty across ALL results,
    unescaped HTML entities across all results, or (for sources with a known
    fixed domain) every result URL off that domain."""
    if not jobs:
        return None  # zero results is a yield-drop signal, not a field signal

    empty_company = sum(1 for j in jobs if not (j.company or "").strip())
    empty_title = sum(1 for j in jobs if not (j.title or "").strip())
    if empty_company == len(jobs):
        return f"all {len(jobs)} result(s) missing company"
    if empty_title == len(jobs):
        return f"all {len(jobs)} result(s) missing title"

    entity_hits = sum(
        1 for j in jobs
        if _HTML_ENTITY_RE.search(j.title) or _HTML_ENTITY_RE.search(j.company)
    )
    if entity_hits == len(jobs):
        return f"unescaped HTML entities in title/company across all {len(jobs)} result(s)"

    expected = _EXPECTED_DOMAIN.get(source_id)
    if expected:
        off_domain = sum(1 for j in jobs if expected not in urlparse(j.apply_url).netloc.lower())
        if off_domain == len(jobs):
            return f"all {len(jobs)} result URL(s) off expected domain '{expected}'"

    return None


def _is_rate_limited(exc: Exception) -> bool:
    return bool(_RATE_LIMIT_RE.search(str(exc)))


def validate_source_health(
    source_id: str,
    jobs: list[Job],
    run_id: str,
    *,
    retry_fn=None,
) -> HealthResult:
    """
    Check one source's results from this run and return a verdict, persisting
    it to the ledger.

    - Degraded: field-quality problems in this run's results (see
      `_degraded_reason`). Never escalated/retried — the data we have is
      already conclusive.
    - Yield-drop: zero results this run vs. a non-zero historical average.
      On suspicion, bounded escalation kicks in if `retry_fn` is given
      (normally the adapter's own `fetch` — re-running it stands in for
      "re-run the canonical test query", since these adapters encode their
      own query set and don't take one as an argument): retry up to
      `_MAX_ESCALATION_ATTEMPTS` times before marking the source broken.
      A 429/rate-limit response is inconclusive, never broken — we don't
      want to auto-disable a source just because it got throttled.
    """
    cfg = load("config").get("source_health", {})
    min_avg = cfg.get("min_avg_for_yield_drop", 3)
    auto_disable = cfg.get("auto_disable_broken", False)

    reason = _degraded_reason(source_id, jobs)
    if reason:
        result = HealthResult(source_id, "degraded", reason, len(jobs))
        ledger.record_source_health(source_id, run_id, result.verdict, reason, len(jobs), None, False)
        log.warning("source health: %s DEGRADED — %s", source_id, reason)
        return result

    history = ledger.source_result_history(source_id)
    avg = sum(history) / len(history) if history else None
    result_count = len(jobs)

    if result_count == 0 and avg is not None and avg >= min_avg:
        verdict = "broken"
        reason = f"0 results vs historical average {avg:.1f} (last {len(history)} run(s))"

        if retry_fn is not None:
            for attempt in range(1, _MAX_ESCALATION_ATTEMPTS + 1):
                try:
                    retry_jobs = retry_fn()
                except Exception as exc:
                    if _is_rate_limited(exc):
                        verdict = "inconclusive"
                        reason = f"rate-limited on retry {attempt}: {exc}"
                        break
                    reason = f"{reason}; retry {attempt} raised {exc}"
                    continue
                if retry_jobs:
                    verdict = "healthy"
                    reason = f"recovered on retry {attempt} ({len(retry_jobs)} results)"
                    jobs = retry_jobs
                    result_count = len(jobs)
                    break

        disabled = auto_disable and verdict == "broken"
        ledger.record_source_health(source_id, run_id, verdict, reason, result_count, avg, disabled)
        log_fn = log.warning if verdict != "healthy" else log.info
        log_fn("source health: %s %s — %s", source_id, verdict.upper(), reason)
        return HealthResult(source_id, verdict, reason, result_count, avg)

    result = HealthResult(source_id, "healthy", "", result_count, avg)
    ledger.record_source_health(source_id, run_id, "healthy", "", result_count, avg, False)
    return result
