"""Resume text → Markdown conversion and the scoring-profile section filter."""
from __future__ import annotations

import pytest

from src import score
from src.resume import resume_track, to_markdown

_RAW = """\
            JANE EXAMPLE DOE
      Software Engineer | Backend Systems
   Berlin, Germany • +49 151 2345 6789 • jane.doe@example.com
   LinkedIn: linkedin.com/in/janedoe • GitHub: github.com/janedoe

PROFESSIONAL EXPERIENCE

Acme Bank — Large Retail Bank​                       04/2025 – 07/2025
Backend Intern​                                          Berlin, Germany
  •​ Built REST APIs in Node.js serving 10,000+ employees across 75+
   relational tables, holidays,
  and approval workflows.
  •​ Shipped features.
Project 122E085                                             11/2023 – Present

TECHNICAL SKILLS
Languages: Python, TypeScript,
SQL
Data: PostgreSQL, MongoDB
"""

_TERMS = ["Jane Example Doe", "jane.doe@example.com", "linkedin.com/in/janedoe", "github.com/janedoe"]


def test_header_block_and_contact_details_are_dropped():
    md = to_markdown(_RAW, _TERMS)
    for leaked in ("Jane", "JANE", "+49", "example.com", "janedoe", "Backend Systems"):
        assert leaked not in md


def test_contact_details_redacted_inside_body():
    md = to_markdown("SUMMARY\nReach me at x.y@mail.com or +90 506 181 3675 anytime.\n")
    assert "@" not in md and "506" not in md
    assert "Reach me at" in md


def test_sections_entries_and_bullets():
    md = to_markdown(_RAW, _TERMS)
    assert "## Professional Experience" in md
    assert "### Acme Bank — Large Retail Bank · 04/2025 – 07/2025" in md
    assert "Backend Intern · Berlin, Germany" in md
    # wrapped bullet lines are rejoined, trailing commas kept
    assert (
        "- Built REST APIs in Node.js serving 10,000+ employees across 75+ "
        "relational tables, holidays, and approval workflows."
    ) in md
    assert "- Shipped features." in md
    assert "​" not in md


def test_project_codes_next_to_dates_are_not_phone_numbers():
    md = to_markdown(_RAW, _TERMS)
    assert "### Project 122E085 · 11/2023 – Present" in md


def test_skill_label_lines_stay_separate():
    md = to_markdown(_RAW, _TERMS)
    assert "Languages: Python, TypeScript, SQL" in md
    assert "\nData: PostgreSQL, MongoDB" in md


def test_scoring_profile_excludes_identity_logistics_eeo():
    candidate = """\
# CANDIDATE.md

## Identity
- Full name: Jane Doe
- Phone: +49 151 0000

## Profile summary
Backend engineer.

## Logistics
- Notice period: 0
### Salary expectation
| DE | EUR | 40,000 |

## Work authorization (per country)
| DE | no | yes |

## EEO / demographics defaults
- Gender: Prefer not to say

## Hard rule
Do not answer.
"""
    out = score._strip_sections(candidate, score._PROFILE_EXCLUDED_SECTIONS)
    assert "Backend engineer." in out
    assert "Work authorization" in out
    for gone in ("Jane Doe", "+49", "Notice period", "Salary", "Gender", "Do not answer"):
        assert gone not in out


# ── Track selection: which resume a job gets ─────────────────────────────────


@pytest.mark.parametrize("title", [
    "Junior AI Engineer", "AI Software Engineer", "Applied AI Engineer", "LLM Engineer",
    "Machine Learning Engineer, New Grad", "Junior ML Engineer", "GenAI Developer",
    "Product Engineer (AI)", "Computer Vision Engineer", "NLP Engineer",
])
def test_ai_titles_get_ai_resume(title):
    assert resume_track(title) == "ai"


@pytest.mark.parametrize("title", [
    "Junior Software Engineer", "Backend Developer", "Full Stack Engineer",
    "Product Engineer", "Solutions Engineer", "HTML Email Developer", "Email Platform Engineer",
])
def test_other_titles_get_swe_resume(title):
    assert resume_track(title) == "swe"


def test_caps_company_lines_and_month_dates():
    raw = """\
WORK EXPERIENCE
HALKBANK                                    Istanbul, Turkey
Full-Stack Intern                          Apr 2025 - Jul 2025
 • Built things.
PROJECTS
REPROBOT — Multi-Agent LLM System          github.com/x/ReproBot
"""
    md = to_markdown(raw)
    assert "## Work Experience" in md
    assert "### HALKBANK · Istanbul, Turkey" in md
    assert "### Full-Stack Intern · Apr 2025 - Jul 2025" in md
    assert "### REPROBOT — Multi-Agent LLM System" in md
    assert "## Halkbank" not in md  # company names are not sections
