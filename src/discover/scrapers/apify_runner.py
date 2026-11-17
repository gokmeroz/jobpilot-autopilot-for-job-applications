"""
Generic Apify HTTP client.

Starts an actor run asynchronously, polls until complete, then returns
all dataset items as a list of dicts. Auth via APIFY_TOKEN env var.
"""
from __future__ import annotations

import logging
import time

import requests

from src.config import env

log = logging.getLogger(__name__)

_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL = 10   # seconds between status checks
_TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


# ---------------------------------------------------------------------------
# Session spend guard. Every run is capped server-side with maxTotalChargeUsd
# at whatever budget is left, and a run that would start with nothing left
# asks for approval first (never silently exceeds config `apify_budget_usd`).
# ---------------------------------------------------------------------------

class Budget:
    def __init__(self) -> None:
        self.limit_usd: float | None = None
        self.spent_usd: float = 0.0
        self.runs: list[dict] = []   # {actor, run_id, status, cost_usd, items}

    def limit(self) -> float:
        if self.limit_usd is None:
            from src.config import load
            self.limit_usd = float(load("config").get("apify_budget_usd", 5.0))
        return self.limit_usd

    def remaining(self) -> float:
        return max(0.0, self.limit() - self.spent_usd)

    def approve_more(self, actor_id: str) -> bool:
        import sys
        if not sys.stdin.isatty():
            log.warning("apify: budget $%.2f used up — skipping %s (non-interactive)", self.limit(), actor_id)
            return False
        try:
            raw = input(
                f"\n  Apify spend ${self.spent_usd:.2f} has reached the ${self.limit():.2f} session budget.\n"
                f"  Raise the budget to run {actor_id}? Enter new total USD (blank = skip): "
            ).strip()
            new = float(raw) if raw else 0.0
        except (EOFError, KeyboardInterrupt, ValueError):
            return False
        if new > self.spent_usd:
            self.limit_usd = new
            return True
        return False


BUDGET = Budget()


def affordable_results(actor_id: str, source: str, wanted: int) -> int:
    """Shrink a planned result count to this source's share of the remaining budget.

    Logs the estimate so the run shows what it expects to spend before starting.
    """
    from src.config import load
    apify = load("config").get("apify", {})
    price = float((apify.get("price_per_result_usd") or {}).get(actor_id, 0) or 0)
    if price <= 0:
        return wanted
    share = float((apify.get("budget_share") or {}).get(source, 1.0))
    allowance = min(BUDGET.remaining(), share * BUDGET.limit())
    n = max(0, min(wanted, int(allowance / price)))
    log.info("apify: %s estimate %d results × $%.4f = $%.2f (wanted %d, allowance $%.2f)",
             actor_id, n, price, n * price, wanted, allowance)
    if n < wanted:
        print(f"  Apify cost guard: {source} capped at {n} of {wanted} results (~${n * price:.2f}); "
              f"raise with --apify-budget")
    return n


def _actor_url_slug(actor_id: str) -> str:
    """Convert 'user/actor-name' → 'user~actor-name' for URL usage."""
    return actor_id.replace("/", "~")


def run_actor(
    actor_id: str,
    input_data: dict,
    *,
    timeout_secs: int = 300,
    max_items: int | None = None,
) -> list[dict]:
    """
    Run an Apify actor and return all output dataset items.

    Args:
        actor_id:     Actor identifier, e.g. "curious_coder/linkedin-jobs-scraper".
        input_data:   Actor input payload (will be JSON-encoded).
        timeout_secs: How long to wait for the run to complete before giving up.
        max_items:    Optional cap on returned items (pagination offset not used —
                      the actor itself should be told to limit output via input_data).

    Returns:
        List of item dicts from the actor's default dataset.

    Raises:
        RuntimeError: if APIFY_TOKEN is missing or the run fails.
    """
    token = env("APIFY_TOKEN", required=True)
    slug  = _actor_url_slug(actor_id)

    if BUDGET.remaining() < 0.01 and not BUDGET.approve_more(actor_id):
        raise RuntimeError(f"Apify session budget ${BUDGET.limit():.2f} reached — {actor_id} not run")

    # ── 1. Start run ────────────────────────────────────────────────────────
    start_url = f"{_BASE}/acts/{slug}/runs"
    try:
        resp = requests.post(
            start_url,
            json=input_data,
            params={"token": token, "maxTotalChargeUsd": f"{BUDGET.remaining():.2f}"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to start Apify actor '{actor_id}': {exc}") from exc

    run_data   = resp.json()["data"]
    run_id     = run_data["id"]
    dataset_id = run_data["defaultDatasetId"]
    log.info("apify: started run %s for actor '%s'", run_id, actor_id)

    # ── 2. Poll until terminal ───────────────────────────────────────────────
    status_url = f"{_BASE}/actor-runs/{run_id}"
    deadline   = time.monotonic() + timeout_secs
    status     = run_data.get("status", "RUNNING")

    while status not in _TERMINAL:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.error("apify: run %s timed out after %ds", run_id, timeout_secs)
            return []

        time.sleep(min(_POLL_INTERVAL, remaining))

        try:
            poll_resp = requests.get(
                status_url,
                params={"token": token},
                timeout=15,
            )
            poll_resp.raise_for_status()
            status = poll_resp.json()["data"]["status"]
            log.debug("apify: run %s status → %s", run_id, status)
        except requests.RequestException as exc:
            log.warning("apify: status poll failed (will retry): %s", exc)

    cost = _run_cost(run_id, token)
    BUDGET.spent_usd += cost
    BUDGET.runs.append({"actor": actor_id, "run_id": run_id, "status": status, "cost_usd": cost, "items": 0})
    log.info("apify: run %s cost ~$%.3f (session $%.2f / $%.2f)", run_id, cost, BUDGET.spent_usd, BUDGET.limit())

    if status != "SUCCEEDED":
        log.error("apify: run %s ended with status '%s'", run_id, status)
        return []

    elapsed = timeout_secs - (deadline - time.monotonic())
    log.info("apify: run %s succeeded in ~%.0fs", run_id, elapsed)

    # ── 3. Fetch dataset items ───────────────────────────────────────────────
    items_url = f"{_BASE}/datasets/{dataset_id}/items"
    params: dict = {"token": token, "format": "json", "clean": "true"}
    if max_items:
        params["limit"] = max_items

    try:
        items_resp = requests.get(items_url, params=params, timeout=60)
        items_resp.raise_for_status()
        items: list[dict] = items_resp.json()
    except requests.RequestException as exc:
        log.error("apify: failed to fetch dataset %s: %s", dataset_id, exc)
        return []

    log.info("apify: retrieved %d items from dataset %s", len(items), dataset_id)
    BUDGET.runs[-1]["items"] = len(items)
    return items


def _run_cost(run_id: str, token: str) -> float:
    """Actual charged USD for a finished run (0.0 if Apify doesn't report it)."""
    try:
        r = requests.get(f"{_BASE}/actor-runs/{run_id}", params={"token": token}, timeout=15)
        r.raise_for_status()
        return float(r.json()["data"].get("usageTotalUsd") or 0.0)
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return 0.0
