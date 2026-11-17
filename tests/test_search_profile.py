"""Tests for the search-brief layer: freshness, sponsorship, fit scoring, tracker, search terms."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.ledger as ledger
import src.sponsors as sponsors
from src import freshness, sponsorship, tracker
from src.discover import search_terms
from src.models import Sponsorship
from src.normalize import country_from_location
from src.score import _apply_result, fit_class
from tests.conftest import make_job


@pytest.fixture(autouse=True)
def no_network_sponsors(monkeypatch):
    monkeypatch.setattr(sponsors, "_LOADED", {"GB": {sponsors.norm("Acme Ltd"): {"name": "Acme Ltd", "url": "https://gov.uk/r", "detail": "UK licensed sponsor"}}, "AU": {}})


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    monkeypatch.setattr(ledger, "_db_path", lambda: db)
    ledger.init()
    tracker.init()


# ── freshness ────────────────────────────────────────────────────────────────

def test_first_run_uses_initial_window():
    assert freshness.choose_hours(None) == 720


def test_subsequent_run_clamped_to_72h_minimum():
    now = datetime.now(timezone.utc)
    assert freshness.choose_hours(now - timedelta(hours=10), now=now) == 72


def test_subsequent_run_clamped_to_7d_maximum():
    now = datetime.now(timezone.utc)
    assert freshness.choose_hours(now - timedelta(days=30), now=now) == 168


def test_window_override():
    assert freshness.choose_hours(None, "24h") == 24
    assert freshness.choose_hours(None, "7d") == 168
    with pytest.raises(ValueError):
        freshness.parse_window("week")


def test_buckets():
    assert freshness.bucket(None) == "original publication date unavailable"
    assert freshness.bucket(5) == "last 24h"
    assert freshness.bucket(50) == "last 72h"
    assert freshness.bucket(100) == "last 7 days"
    assert freshness.bucket(400) == "older but still open"


# ── sponsorship ──────────────────────────────────────────────────────────────

def test_explicit_sponsorship_is_A():
    sp = sponsorship.classify(make_job(country="GB", company="Other", description="Visa sponsorship is available for this role."))
    assert sp.category == "A" and "sponsorship" in sp.evidence.lower()


def test_restriction_beats_positive():
    sp = sponsorship.classify(make_job(country="GB", description="We offer visa sponsorship. Unable to sponsor graduates."))
    assert sp.category == "D"


def test_work_rights_requirement_is_D():
    sp = sponsorship.classify(make_job(country="AU", description="You must have full working rights in Australia."))
    assert sp.category == "D"


def test_register_membership_is_only_B():
    sp = sponsorship.classify(make_job(country="GB", company="ACME LIMITED", description="Great team."))
    assert sp.category == "B" and sp.evidence_type == "sponsor_register"


def test_unknown_is_C():
    assert sponsorship.classify(make_job(country="DE", company="Nobody", description="Great team.")).category == "C"


def test_home_country_not_needed():
    assert sponsorship.classify(make_job(country="TR")).category == "-"


def test_offshore_restricted():
    sp = sponsorship.classify(make_job(country="AU", description="Visa sponsorship available. Candidates must be based in Australia."))
    assert sp.category == "A" and sp.offshore == "Restricted"
    assert sponsorship.hard_restricted(sp, make_job(country="AU")) is not None


def test_au_salary_eligibility():
    assert "meets CSIT" in sponsorship.classify(make_job(country="AU", salary="$95,000 - $110,000")).salary_eligibility
    assert "below CSIT" in sponsorship.classify(make_job(country="AU", salary="$60k")).salary_eligibility
    assert "unverified" in sponsorship.classify(make_job(country="AU", salary=None)).salary_eligibility


def test_llm_claim_needs_verbatim_quote():
    job = make_job(country="GB", company="Nobody", description="We are happy to sponsor Skilled Worker visas for this position.")
    base = Sponsorship(category="C")
    assert sponsorship.apply_llm_claim(job, base, "A", "we sponsor everyone always").category == "C"
    got = sponsorship.apply_llm_claim(job, base, "A", "happy to sponsor Skilled Worker visas")
    assert got.category == "A" and got.evidence_type == "llm_quote"


def test_llm_cannot_upgrade_D():
    job = make_job(description="No visa sponsorship. We love sponsoring talent growth here.")
    sp = Sponsorship(category="D")
    assert sponsorship.apply_llm_claim(job, sp, "A", "We love sponsoring talent growth here").category == "D"


# ── fit scoring ──────────────────────────────────────────────────────────────

def test_fit_classes():
    assert fit_class(85) == "Strong fit"
    assert fit_class(70) == "Good fit"
    assert fit_class(55) == "Stretch"
    assert fit_class(40) == "Not recommended"
    assert fit_class(95, "needs 5 years") == "Not recommended"


def test_total_computed_from_weights_not_llm():
    data = {"tech": 100, "experience": 50, "seniority": 100, "alignment": 100, "location": 100,
            "total": 3, "hard_blocker": None, "career_path": "ai_ml"}
    job = _apply_result(make_job(country="DE", company="Nobody", description="x"), data)
    assert job.score.total == pytest.approx(87.5)
    assert job.fit.fit_class == "Strong fit"
    assert job.sponsorship.category == "C"


def test_ai_title_gets_ai_resume():
    job = _apply_result(make_job(title="LLM Engineer", description="x"), {k: 70 for k in ("tech", "experience", "seniority", "alignment", "location")})
    assert job.recommended_resume == "ai"


def test_restricted_job_gets_blocker():
    job = _apply_result(make_job(country="US", description="Must have unrestricted authorization to work in the US."),
                        {k: 90 for k in ("tech", "experience", "seniority", "alignment", "location")})
    assert job.fit.hard_blocker and job.fit.fit_class == "Not recommended"


# ── tracker ──────────────────────────────────────────────────────────────────

def _scored(**kw):
    return _apply_result(make_job(description="x", **kw), {k: 80 for k in ("tech", "experience", "seniority", "alignment", "location")})


def test_tracker_new_then_still_open(tmp_db):
    j = _scored()
    ch = tracker.reconcile([j], {j.job_key}, set())
    assert len(ch["new"]) == 1 and tracker.all_rows()[0]["application_status"] == "Not Applied"
    ch = tracker.reconcile([], {j.job_key}, set())
    assert len(ch["still_open"]) == 1 and not ch["closed"]


def test_tracker_closes_missing_job_only_on_crawled_board(tmp_db):
    j = _scored(source="greenhouse")
    tracker.reconcile([j], {j.job_key}, set())
    assert not tracker.reconcile([], set(), set())["closed"]               # board not fetched → unknown
    assert len(tracker.reconcile([], set(), {("greenhouse", "Acme")})["closed"]) == 1
    assert tracker.all_rows()[0]["application_status"] == "Closed"


def test_tracker_detects_description_change(tmp_db):
    j = _scored()
    tracker.reconcile([j], {j.job_key}, set())
    ch = tracker.reconcile([j.model_copy(update={"description": "new text"})], {j.job_key}, set())
    assert len(ch["changed"]) == 1


def test_tracker_status_and_export(tmp_db):
    j = _scored()
    tracker.reconcile([j], {j.job_key}, set())
    assert tracker.set_status(j.job_key, "Interview")
    with pytest.raises(ValueError):
        tracker.set_status(j.job_key, "Hired")
    rows = tracker.all_rows()
    assert "application_status" in tracker.to_csv(rows).splitlines()[0]
    assert '"Interview"' in tracker.to_json(rows)


def test_last_run_roundtrip(tmp_db):
    assert tracker.last_run_at() is None
    tracker.record_run("r1", 720)
    assert tracker.last_run_at() is not None


# ── search terms / geography ─────────────────────────────────────────────────

def test_family_roles_cover_all_priorities():
    roles = search_terms.family_roles()
    assert "AI Engineer" in roles and "Backend Engineer" in roles and "Forward Deployed Engineer" in roles


def test_sponsorship_queries_added_per_market():
    au = [q for q, loc in search_terms.linkedin_matrix(["AU"])]
    assert any("482" in q for q in au)
    assert search_terms.market_tier("US") == 1 and search_terms.market_tier("AU") == 3


def test_country_hints_longest_first():
    assert country_from_location("Cambridge, MA") == "US"
    assert country_from_location("Cambridge, UK") == "GB"
    assert country_from_location("Perth, WA") == "AU"
    assert country_from_location("Lausanne") == "CH"
