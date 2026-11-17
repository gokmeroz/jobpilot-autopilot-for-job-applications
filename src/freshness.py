"""
Search freshness window.

First run (empty tracker): look back `freshness.initial_hours` (30 days) so
still-open roles are found; results are ranked newest-first and grouped into
24h / 72h / 7d / older buckets in the report.

Later runs: look back to the previous run, clamped to
[subsequent_min_hours, subsequent_max_hours] (72h..7d) to catch delayed
indexing. `--window` overrides both.

Sources read `gate.max_age_hours`; apply() writes the chosen window there so
every adapter and the gate agree on one number for the run.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from src.config import load

_WINDOW_RE = re.compile(r"^(\d+)\s*([hd])$", re.IGNORECASE)


def parse_window(text: str) -> float:
    """'24h' -> 24, '7d' -> 168."""
    m = _WINDOW_RE.match(text.strip())
    if not m:
        raise ValueError(f"bad --window {text!r}; use e.g. 24h, 72h, 7d, 30d")
    n, unit = int(m.group(1)), m.group(2).lower()
    return float(n * (24 if unit == "d" else 1))


def choose_hours(last_run_at: datetime | None, override: str | None = None,
                 now: datetime | None = None) -> float:
    if override:
        return parse_window(override)
    cfg = load("config").get("freshness", {})
    if last_run_at is None:
        return float(cfg.get("initial_hours", 720))
    now = now or datetime.now(timezone.utc)
    since = (now - last_run_at).total_seconds() / 3600
    lo = float(cfg.get("subsequent_min_hours", 72))
    hi = float(cfg.get("subsequent_max_hours", 168))
    return max(lo, min(hi, since))


def apply(hours: float) -> None:
    load("config")["gate"]["max_age_hours"] = hours


def bucket(age_hours: float | None) -> str:
    if age_hours is None:
        return "original publication date unavailable"
    for limit in load("config").get("freshness", {}).get("buckets", [24, 72, 168]):
        if age_hours <= limit:
            return f"last {limit}h" if limit < 168 else "last 7 days"
    return "older but still open"
