from __future__ import annotations

import re

from src.models import RemoteType

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_COUNTRY_HINTS: dict[str, str] = {
    "germany": "DE", "berlin": "DE", "munich": "DE", "münchen": "DE",
    "hamburg": "DE", "frankfurt": "DE", "cologne": "DE", "köln": "DE",
    "netherlands": "NL", "amsterdam": "NL", "rotterdam": "NL", "eindhoven": "NL",
    "ireland": "IE", "dublin": "IE",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "london": "GB",
    "manchester": "GB", "edinburgh": "GB",
    "turkey": "TR", "türkiye": "TR", "istanbul": "TR", "ankara": "TR",
    "united states": "US", "usa": "US", "new york": "US", "san francisco": "US",
}

_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)


def slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def build_job_key(company: str, title: str, country: str) -> str:
    return f"{slug(company)}__{slug(title)}__{slug(country)}"


def country_from_location(location: str) -> str:
    lower = location.lower()
    for hint, iso2 in _COUNTRY_HINTS.items():
        if hint in lower:
            return iso2
    return "REMOTE"


def infer_remote(*texts: str) -> RemoteType:
    combined = " ".join(texts)
    if _REMOTE_RE.search(combined):
        return RemoteType.remote
    if _HYBRID_RE.search(combined):
        return RemoteType.hybrid
    return RemoteType.unknown
