"""Tests for src/gate.py — each gate rule tested in isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.gate import (
    _gate_age,
    _gate_domain,
    _gate_language,
    _gate_role,
    _gate_seniority,
    _gate_yoe,
    run,
)
from src.models import Status
from tests.conftest import make_job


# ── Age gate ─────────────────────────────────────────────────────────────────

class TestGateAge:
    def _job(self, hours_ago: float, trusted: bool = True):
        posted = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return make_job(posted_at=posted, timestamp_trusted=trusted)

    def test_fresh_job_passes(self):
        assert _gate_age(self._job(10), max_age_hours=48) is None

    def test_stale_job_rejected(self):
        assert _gate_age(self._job(72), max_age_hours=48) is not None

    def test_well_under_limit_passes(self):
        assert _gate_age(self._job(47), max_age_hours=48) is None

    def test_untrusted_timestamp_always_passes(self):
        assert _gate_age(self._job(999, trusted=False), max_age_hours=48) is None

    def test_no_posted_at_passes(self):
        job = make_job(posted_at=None, timestamp_trusted=False)
        assert _gate_age(job, max_age_hours=48) is None


# ── Role gate ─────────────────────────────────────────────────────────────────

class TestGateRole:
    def _passes(self, title: str) -> bool:
        return _gate_role(make_job(title=title)) is None

    def test_software_engineer_passes(self):
        assert self._passes("Software Engineer")

    def test_backend_engineer_passes(self):
        assert self._passes("Backend Engineer")

    def test_frontend_developer_passes(self):
        assert self._passes("Frontend Developer")

    def test_fullstack_engineer_passes(self):
        assert self._passes("Full Stack Engineer")

    def test_nodejs_developer_passes(self):
        assert self._passes("Node.js Developer")

    def test_python_engineer_passes(self):
        assert self._passes("Python Engineer")

    def test_ai_engineer_passes(self):
        assert self._passes("AI Engineer")

    def test_product_engineer_passes(self):
        assert self._passes("Product Engineer")

    def test_data_engineer_passes(self):
        assert self._passes("Data Engineer")

    def test_non_tech_rejected(self):
        assert not self._passes("Marketing Manager")
        assert not self._passes("Sales Account Executive")
        assert not self._passes("Customer Success Manager")
        assert not self._passes("HR Business Partner")


# ── Seniority gate ────────────────────────────────────────────────────────────

class TestGateSeniority:
    def _passes(self, title: str) -> bool:
        return _gate_seniority(make_job(title=title)) is None

    def test_plain_swe_passes(self):
        assert self._passes("Software Engineer")

    def test_senior_rejected(self):
        assert not self._passes("Senior Software Engineer")
        assert not self._passes("Sr. Backend Developer")

    def test_staff_rejected(self):
        assert not self._passes("Staff Engineer")

    def test_lead_rejected(self):
        assert not self._passes("Lead Software Engineer")

    def test_principal_rejected(self):
        assert not self._passes("Principal Engineer")

    def test_junior_overrides_senior_word(self):
        assert self._passes("Junior Software Engineer")
        assert self._passes("Graduate Software Engineer")
        assert self._passes("Associate Engineer")

    def test_new_grad_passes(self):
        assert self._passes("New Grad Software Engineer")

    def test_entry_level_passes(self):
        assert self._passes("Entry Level Backend Developer")


# ── YOE gate ──────────────────────────────────────────────────────────────────

class TestGateYoe:
    def test_no_yoe_passes(self):
        assert _gate_yoe(make_job(yoe_max=None), max_yoe=1) is None

    def test_within_limit_passes(self):
        assert _gate_yoe(make_job(yoe_max=1), max_yoe=1) is None

    def test_over_limit_rejected(self):
        assert _gate_yoe(make_job(yoe_max=3), max_yoe=1) is not None

    def test_zero_yoe_passes(self):
        assert _gate_yoe(make_job(yoe_max=0), max_yoe=1) is None


# ── Language gate ─────────────────────────────────────────────────────────────

class TestGateLanguage:
    def test_english_passes(self):
        assert _gate_language(make_job(language="EN"), ["EN"], german_ok=False) is None

    def test_french_rejected(self):
        assert _gate_language(make_job(language="FR"), ["EN"], german_ok=False) is not None

    def test_german_rejected_when_not_ok(self):
        job = make_job(language="DE", country="DE")
        assert _gate_language(job, ["EN"], german_ok=False) is not None

    def test_german_passes_when_german_ok_and_de_country(self):
        job = make_job(language="DE", country="DE")
        assert _gate_language(job, ["EN"], german_ok=True) is None

    def test_german_rejected_if_not_de_country(self):
        job = make_job(language="DE", country="NL")
        assert _gate_language(job, ["EN"], german_ok=True) is not None

    def test_case_insensitive(self):
        assert _gate_language(make_job(language="en"), ["EN"], german_ok=False) is None


# ── Domain denylist gate ──────────────────────────────────────────────────────

class TestGateDomain:
    def _passes(self, title: str) -> bool:
        return _gate_domain(make_job(title=title)) is None

    def test_normal_swe_passes(self):
        assert self._passes("Software Engineer")
        assert self._passes("Backend Developer")

    def test_scada_rejected(self):
        assert not self._passes("SCADA Software Engineer")

    def test_ms_dynamics_rejected(self):
        assert not self._passes("MS Dynamics Developer")
        assert not self._passes("Dynamics BC Developer")
        assert not self._passes("Dynamics NAV Developer")

    def test_odoo_rejected(self):
        assert not self._passes("Odoo Developer")

    def test_servicenow_rejected(self):
        assert not self._passes("ServiceNow Developer")


# ── Full run() ────────────────────────────────────────────────────────────────

class TestRun:
    def test_passes_good_jobs(self):
        jobs = [make_job(title="Software Engineer", language="EN", yoe_max=None)]
        passed, rejected = run(jobs)
        assert len(passed) == 1
        assert len(rejected) == 0

    def test_rejects_bad_jobs(self):
        jobs = [make_job(title="Senior Software Engineer")]
        passed, rejected = run(jobs)
        assert len(passed) == 0
        assert len(rejected) == 1
        assert rejected[0].status == Status.gated_out

    def test_mixed_list_split_correctly(self):
        jobs = [
            make_job(title="Software Engineer"),
            make_job(title="Senior Software Engineer"),
            make_job(title="Junior Backend Developer"),
            make_job(title="Marketing Manager"),
        ]
        passed, rejected = run(jobs)
        assert len(passed) == 2
        assert len(rejected) == 2

    def test_gate_reason_set_on_rejected(self):
        jobs = [make_job(title="Senior Engineer", yoe_max=5)]
        _, rejected = run(jobs)
        assert rejected[0].gate_reason is not None
