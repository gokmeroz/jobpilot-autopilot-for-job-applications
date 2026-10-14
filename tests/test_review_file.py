"""Review file write → --resume-review parse round trip."""
from __future__ import annotations

from main import _parse_review_file
from src import pipeline
from src.models import Score
from tests.conftest import make_job


def _score(total: float) -> Score:
    return Score(role=total, stack=total, seniority=total, visa=total, culture=total,
                 total=total, rationale="")


def test_pipe_in_title_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    job = make_job(title="Fullstack Developer/in | AI Engineer/in (m/w/d)", company="SIGN8",
                   score=_score(7.2))
    path = pipeline._write_review_file([job], "r1", {"paths": {"manual_queue": "mq"}})

    _, jobs = _parse_review_file(str(path))

    assert len(jobs) == 1
    assert jobs[0].title == job.title
    assert jobs[0].company == "SIGN8"
    assert jobs[0].country == "DE"
    assert jobs[0].apply_url == job.apply_url


def test_legacy_unescaped_pipe_row_recovers_columns(tmp_path):
    path = tmp_path / "old_review.md"
    path.write_text(
        "| # | Role | Company | Country | Type | Salary | Score | Apply Link |\n"
        "|---|------|---------|---------|------|--------|-------|------------|\n"
        "| 1 | Backend Engineer | Sweden | Remote | Grafana Labs | SE | Remote | — | 6.6 "
        "| https://job-boards.greenhouse.io/grafanalabs/jobs/1 |\n"
    )

    _, jobs = _parse_review_file(str(path))

    assert jobs[0].title == "Backend Engineer | Sweden | Remote"
    assert jobs[0].company == "Grafana Labs"
    assert jobs[0].country == "SE"
    assert jobs[0].score.total == 6.6
