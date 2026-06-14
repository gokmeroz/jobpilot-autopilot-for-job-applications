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

    # ── 1. Start run ────────────────────────────────────────────────────────
    start_url = f"{_BASE}/acts/{slug}/runs"
    try:
        resp = requests.post(
            start_url,
            json=input_data,
            params={"token": token},
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
    return items
