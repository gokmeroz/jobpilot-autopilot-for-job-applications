"""Tests for src/regions.py and src/discover/search_terms.py."""
from __future__ import annotations

import pytest

from src.discover import search_terms
from src.regions import EU, resolve


# ── Region presets ───────────────────────────────────────────────────────────

class TestResolve:
    def test_nothing_given_is_unrestricted(self):
        assert resolve(None, None) is None

    def test_all_overrides_everything(self):
        assert resolve(["turkey", "all"], ["PT"]) is None

    def test_turkey(self):
        assert resolve(["turkey"], None) == ["TR"]

    def test_eu_uk_contains_both(self):
        codes = resolve(["eu-uk"], None)
        assert "GB" in codes and "DE" in codes and "TR" not in codes

    def test_presets_union_with_countries_without_duplicates(self):
        codes = resolve(["eu", "remote"], ["DE", "TR"])
        assert codes == EU + ["REMOTE", "TR"]

    def test_countries_only(self):
        assert resolve(None, ["TR", "REMOTE"]) == ["TR", "REMOTE"]


# ── Search matrix ────────────────────────────────────────────────────────────

@pytest.fixture
def search_cfg(monkeypatch):
    cfg = {"search": {
        "roles": ["junior software engineer", "product engineer"],
        "markets": {"DE": "Germany", "IE": "Ireland", "TR": "Turkey", "REMOTE": "remote"},
    }}
    monkeypatch.setattr(search_terms, "load", lambda name: cfg)


class TestSearchTerms:
    def test_all_markets_when_unrestricted(self, search_cfg):
        assert len(search_terms.jsearch_queries()) == 2 * 4

    def test_markets_narrowed_by_region(self, search_cfg):
        queries = search_terms.jsearch_queries(EU)
        assert {hint for _, hint in queries} == {"DE", "IE"}
        assert ("product engineer Germany", "DE") in queries

    def test_no_matching_market_gives_empty_matrix(self, search_cfg):
        assert search_terms.jsearch_queries(["PT"]) == []
        assert search_terms.linkedin_matrix(["PT"]) == []

    def test_linkedin_remote_uses_global_location(self, search_cfg):
        assert search_terms.linkedin_matrix(["REMOTE"]) == [
            ("junior software engineer remote", ""),
            ("product engineer remote", ""),
        ]

    def test_adzuna_skips_unsupported_countries(self, search_cfg):
        targets = search_terms.adzuna_targets(["IE", "TR", "DE", "REMOTE"])
        assert {cc for cc, _, _ in targets} == {"de", "us"}
        assert ("us", "US", "product engineer remote") in targets
