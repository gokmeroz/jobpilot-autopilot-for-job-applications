"""Tests for src/source_health.py — uses a tmp SQLite ledger, never touches data/ledger.sqlite."""
from __future__ import annotations

import pytest

import src.ledger as ledger
import src.source_health as source_health
from tests.conftest import make_job


@pytest.fixture(autouse=True)
def tmp_ledger(tmp_path, monkeypatch):
    """Redirect ledger to a fresh temp database for every test."""
    db = tmp_path / "test_ledger.sqlite"
    monkeypatch.setattr(ledger, "_db_path", lambda: db)
    ledger.init()
    return db


@pytest.fixture(autouse=True)
def default_cfg(monkeypatch):
    """Deterministic source_health config, independent of config.yaml."""
    monkeypatch.setattr(
        source_health, "load",
        lambda name: {"source_health": {"auto_disable_broken": False, "min_avg_for_yield_drop": 3}},
    )


def _seed_history(source_id: str, counts: list[int]) -> None:
    for i, count in enumerate(counts):
        ledger.record_source_health(source_id, f"run-{i}", "healthy", "", count, None, False)


# ── degraded: field-quality signals ─────────────────────────────────────────

def test_degraded_all_missing_company():
    jobs = [make_job(company="", apply_url=f"https://example.com/{i}") for i in range(3)]
    result = source_health.validate_source_health("jsearch", jobs, "run-001")
    assert result.verdict == "degraded"
    assert "company" in result.reason


def test_degraded_all_missing_title():
    jobs = [make_job(title="", apply_url=f"https://example.com/{i}") for i in range(3)]
    result = source_health.validate_source_health("jsearch", jobs, "run-001")
    assert result.verdict == "degraded"
    assert "title" in result.reason


def test_mixed_missing_fields_not_degraded():
    """Only SOME results missing a field should not trip the degraded signal —
    that's normal per-listing noise, not systemic breakage."""
    jobs = [
        make_job(company="", apply_url="https://example.com/1"),
        make_job(apply_url="https://example.com/2"),
    ]
    result = source_health.validate_source_health("jsearch", jobs, "run-001")
    assert result.verdict != "degraded"


def test_degraded_unescaped_html_entities():
    jobs = [
        make_job(title="Software Engineer &amp; Backend", apply_url=f"https://example.com/{i}")
        for i in range(2)
    ]
    result = source_health.validate_source_health("jsearch", jobs, "run-001")
    assert result.verdict == "degraded"
    assert "HTML entities" in result.reason


def test_degraded_domain_mismatch_for_fixed_domain_source():
    jobs = [make_job(apply_url=f"https://not-greenhouse.example.com/job/{i}") for i in range(2)]
    result = source_health.validate_source_health("greenhouse", jobs, "run-001")
    assert result.verdict == "degraded"
    assert "greenhouse.io" in result.reason


def test_domain_check_skipped_for_aggregator_sources():
    """JSearch/Adzuna legitimately return arbitrary employer domains."""
    jobs = [make_job(apply_url=f"https://random-employer-{i}.example.com/job") for i in range(2)]
    result = source_health.validate_source_health("jsearch", jobs, "run-001")
    assert result.verdict == "healthy"


# ── healthy ──────────────────────────────────────────────────────────────────

def test_healthy_normal_results_recorded():
    jobs = [make_job(apply_url=f"https://boards.greenhouse.io/acme/jobs/{i}") for i in range(5)]
    result = source_health.validate_source_health("greenhouse", jobs, "run-001")
    assert result.verdict == "healthy"
    assert ledger.source_result_history("greenhouse") == [5]


def test_zero_results_with_no_history_is_healthy():
    """No prior runs to compare against — can't call it a drop."""
    result = source_health.validate_source_health("greenhouse", [], "run-001")
    assert result.verdict == "healthy"


def test_zero_results_below_min_avg_threshold_is_healthy():
    """Historical average under min_avg_for_yield_drop — too small a source to flag."""
    _seed_history("tiny-source", [1, 2, 1])
    result = source_health.validate_source_health("tiny-source", [], "run-005")
    assert result.verdict == "healthy"


# ── yield-drop + bounded escalation ─────────────────────────────────────────

def test_yield_drop_marks_broken_without_retry_fn():
    _seed_history("greenhouse", [20, 18, 22])
    result = source_health.validate_source_health("greenhouse", [], "run-005")
    assert result.verdict == "broken"
    assert "historical average" in result.reason
    assert ledger.is_source_disabled("greenhouse") is False  # auto_disable off by default


def test_yield_drop_recovers_on_retry():
    _seed_history("greenhouse", [20, 18, 22])
    recovered_jobs = [make_job(apply_url=f"https://boards.greenhouse.io/acme/jobs/{i}") for i in range(15)]
    result = source_health.validate_source_health(
        "greenhouse", [], "run-005", retry_fn=lambda: recovered_jobs,
    )
    assert result.verdict == "healthy"
    assert "recovered" in result.reason


def test_yield_drop_stays_broken_after_failed_retries():
    _seed_history("greenhouse", [20, 18, 22])
    result = source_health.validate_source_health(
        "greenhouse", [], "run-005", retry_fn=lambda: [],
    )
    assert result.verdict == "broken"


def test_rate_limited_retry_is_inconclusive_not_broken():
    _seed_history("greenhouse", [20, 18, 22])

    def _raise_429():
        raise RuntimeError("HTTP 429 Too Many Requests")

    result = source_health.validate_source_health(
        "greenhouse", [], "run-005", retry_fn=_raise_429,
    )
    assert result.verdict == "inconclusive"
    assert ledger.is_source_disabled("greenhouse") is False


# ── auto-disable ─────────────────────────────────────────────────────────────

def test_auto_disable_broken_sets_disabled_flag(monkeypatch):
    monkeypatch.setattr(
        source_health, "load",
        lambda name: {"source_health": {"auto_disable_broken": True, "min_avg_for_yield_drop": 3}},
    )
    _seed_history("greenhouse", [20, 18, 22])
    result = source_health.validate_source_health("greenhouse", [], "run-005")
    assert result.verdict == "broken"
    assert ledger.is_source_disabled("greenhouse") is True


def test_source_never_checked_is_not_disabled():
    assert ledger.is_source_disabled("never-seen-source") is False
