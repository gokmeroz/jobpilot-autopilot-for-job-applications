from __future__ import annotations

from src.resolve import _clean_linkedin_url


def test_clean_linkedin_url_strips_query_params():
    cleaned = _clean_linkedin_url(
        "https://www.linkedin.com/jobs/view/12345/?trk=foo&position=1"
    )
    assert cleaned == "https://www.linkedin.com/jobs/view/12345/"
