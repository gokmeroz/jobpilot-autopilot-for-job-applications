"""Resume text → Markdown conversion and the scoring-profile section filter."""
from __future__ import annotations

from src import score
from src.resume import to_markdown

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
