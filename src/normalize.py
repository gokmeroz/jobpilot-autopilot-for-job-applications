from __future__ import annotations

import re
from urllib.parse import urlparse

from src.models import RemoteType

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_COUNTRY_HINTS: dict[str, str] = {
    # Germany
    "germany": "DE", "berlin": "DE", "munich": "DE", "münchen": "DE",
    "hamburg": "DE", "frankfurt": "DE", "cologne": "DE", "köln": "DE",
    "düsseldorf": "DE", "stuttgart": "DE", "dusseldorf": "DE",
    # Netherlands
    "netherlands": "NL", "amsterdam": "NL", "rotterdam": "NL", "eindhoven": "NL",
    "utrecht": "NL", "the hague": "NL", "hague": "NL", "delft": "NL",
    # Ireland
    "ireland": "IE", "dublin": "IE", "cork": "IE", "galway": "IE",
    # United Kingdom
    "united kingdom": "GB", "uk": "GB", "england": "GB", "london": "GB",
    "manchester": "GB", "edinburgh": "GB", "birmingham": "GB", "leeds": "GB",
    "glasgow": "GB", "bristol": "GB", "cambridge": "GB", "oxford": "GB",
    # Turkey
    "turkey": "TR", "türkiye": "TR", "istanbul": "TR", "ankara": "TR",
    "izmir": "TR",
    # United States
    "united states": "US", "usa": "US", "new york": "US", "san francisco": "US",
    "seattle": "US", "boston": "US", "austin": "US", "chicago": "US",
    "los angeles": "US", "denver": "US", "atlanta": "US", "miami": "US",
    # Portugal
    "portugal": "PT", "lisbon": "PT", "porto": "PT", "braga": "PT",
    # Spain
    "spain": "ES", "barcelona": "ES", "madrid": "ES", "valencia": "ES",
    "seville": "ES", "bilbao": "ES", "san sebastian": "ES", "málaga": "ES",
    "malaga": "ES",
    # France
    "france": "FR", "paris": "FR", "lyon": "FR", "marseille": "FR",
    "bordeaux": "FR", "toulouse": "FR", "nantes": "FR",
    # Sweden
    "sweden": "SE", "stockholm": "SE", "gothenburg": "SE", "malmö": "SE",
    "malmo": "SE",
    # Norway
    "norway": "NO", "oslo": "NO", "bergen": "NO",
    # Denmark
    "denmark": "DK", "copenhagen": "DK",
    # Finland
    "finland": "FI", "helsinki": "FI",
    # Switzerland
    "switzerland": "CH", "zurich": "CH", "zürich": "CH", "geneva": "CH",
    # Austria
    "austria": "AT", "vienna": "AT", "wien": "AT",
    # Belgium
    "belgium": "BE", "brussels": "BE", "bruxelles": "BE", "antwerp": "BE",
    "ghent": "BE",
    # Poland
    "poland": "PL", "warsaw": "PL", "kraków": "PL", "krakow": "PL",
    "wrocław": "PL", "wroclaw": "PL",
    # Czech Republic
    "czech": "CZ", "czechia": "CZ", "prague": "CZ", "brno": "CZ",
    # Romania
    "romania": "RO", "bucharest": "RO", "cluj": "RO",
    # Hungary
    "hungary": "HU", "budapest": "HU",
    # Cyprus
    "cyprus": "CY", "nicosia": "CY", "limassol": "CY",
    # Estonia
    "estonia": "EE", "tallinn": "EE",
    # Japan
    "japan": "JP", "tokyo": "JP", "osaka": "JP", "kyoto": "JP",
    # Singapore
    "singapore": "SG",
    # Australia
    "australia": "AU", "sydney": "AU", "melbourne": "AU", "brisbane": "AU",
    # Canada
    "canada": "CA", "toronto": "CA", "vancouver": "CA", "montreal": "CA",
    # India
    "india": "IN", "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN",
    "hyderabad": "IN", "pune": "IN",
    # UAE
    "uae": "AE", "dubai": "AE", "abu dhabi": "AE",
}

_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)

# ATS domain → canonical name. Order matters: more specific first.
_ATS_DOMAINS: dict[str, str] = {
    "greenhouse.io":       "greenhouse",
    "lever.co":            "lever",
    "ashbyhq.com":         "ashby",
    "workable.com":        "workable",
    "smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com":   "workday",
    "jobvite.com":         "jobvite",
    "icims.com":           "icims",
    "taleo.net":           "taleo",
    "successfactors.com":  "successfactors",
    "breezy.hr":           "breezy",
    "recruitee.com":       "recruitee",
    "personio.de":         "personio",
    "personio.com":        "personio",
    "teamtailor.com":      "teamtailor",
    "recruitee.com":       "recruitee",
    "bamboohr.com":        "bamboohr",
    "wellfound.com":       "wellfound",
    "angel.co":            "wellfound",
    "applytojob.com":      "jobadder",
    "dover.com":           "dover",
}


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


def fingerprint_ats(url: str) -> str | None:
    """Return the canonical ATS name for a URL, or None if unrecognised."""
    try:
        host = urlparse(url).netloc.lower()
        for domain, name in _ATS_DOMAINS.items():
            if domain in host:
                return name
    except Exception:
        pass
    return None


def infer_remote(*texts: str) -> RemoteType:
    combined = " ".join(texts)
    if _REMOTE_RE.search(combined):
        return RemoteType.remote
    if _HYBRID_RE.search(combined):
        return RemoteType.hybrid
    return RemoteType.unknown
