"""
Company-level visa sponsorship evidence (sponsor-first discovery, approach B).

  GB  gov.uk Register of Licensed Sponsors: Workers (official CSV)
  AU  SponsorMap (Home Affairs FOI list, Technology & IT sector, every state)
      Applywave accredited-sponsor list (FOI)

Each list is cached under data/sponsors/ for `sponsorship.cache_days`.
Being on a list is evidence the employer CAN sponsor — never that it will
sponsor this role or an entry-level, offshore candidate. It can only lift a
job to category B, never to A.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from pathlib import Path

import requests

from src.config import ROOT, load

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (JobPilot job search)"}
_SUFFIX_RE = re.compile(
    r"\b(pty|ltd|limited|inc|llc|plc|gmbh|bv|b\.v\.|ag|sa|corp|corporation|company|co|group|holdings|"
    r"australia|uk|europe|technologies|technology|the)\b\.?", re.IGNORECASE)
_AU_STATES = ["new-south-wales", "victoria", "queensland", "western-australia", "south-australia",
              "tasmania", "australian-capital-territory", "northern-territory"]

STATUS: dict[str, str] = {}   # source -> "ok (N)" / error, for the coverage log


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _SUFFIX_RE.sub(" ", name.lower()))


def _cache(name: str) -> Path:
    p = ROOT / "data" / "sponsors" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _cached(name: str, builder) -> dict[str, dict]:
    path = _cache(name)
    days = load("config").get("sponsorship", {}).get("cache_days", 7)
    if path.exists() and time.time() - path.stat().st_mtime < days * 86400:
        data = json.loads(path.read_text())
        STATUS[name] = f"ok (cached, {len(data)} employers)"
        return data
    try:
        data = builder()
    except (requests.RequestException, ValueError, KeyError) as exc:
        STATUS[name] = f"failed: {exc}"
        log.warning("sponsors: %s failed: %s", name, exc)
        return json.loads(path.read_text()) if path.exists() else {}
    if data:
        path.write_text(json.dumps(data))
    STATUS[name] = f"ok ({len(data)} employers)"
    return data


def _uk_register() -> dict[str, dict]:
    page = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
    html = requests.get(page, headers=_UA, timeout=30).text
    m = re.search(r"https://assets\.publishing\.service\.gov\.uk[^\"]+\.csv", html)
    if not m:
        raise ValueError("register CSV link not found")
    text = requests.get(m.group(0), headers=_UA, timeout=120).content.decode("utf-8-sig", "replace")
    out: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        name = row.get("Organisation Name") or ""
        route = row.get("Route") or ""
        if name and "Skilled Worker" in route:
            out[norm(name)] = {"name": name, "url": page, "detail": f"UK licensed sponsor — {route} ({row.get('Type & Rating', '').strip()})"}
    return out


def _sponsormap() -> dict[str, dict]:
    # Embedded Next.js payload: \"name\":\"Atlassian\",\"name_original\":\"ATLASSIAN PTY LTD\"
    # (every company on a technology-and-it page is in that sector).
    rec = re.compile(r'\\"name\\":\\"([^\\]+)\\",\\"name_original\\":\\"([^\\]*)\\"')
    out: dict[str, dict] = {}
    for state in _AU_STATES:
        url = f"https://sponsormap.au/sponsors/{state}/technology-and-it"
        html = requests.get(url, headers=_UA, timeout=30).text
        for name, original in rec.findall(html):
            name = name.strip("‘’'\" ")
            out[norm(name)] = {"name": name, "url": url, "detail": f"SponsorMap accredited sponsor ({original.strip('‘’')}, Technology & IT)"}
            out.setdefault(norm(original), out[norm(name)])
        time.sleep(0.3)
    return out


def _applywave(max_pages: int = 20) -> dict[str, dict]:
    ld = re.compile(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', re.S)
    out: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        url = "https://applywave.app/visa-sponsorship/au" + (f"?page={page}" if page > 1 else "")
        html = requests.get(url, headers=_UA, timeout=30).text
        before = len(out)
        for block in ld.findall(html):
            d = json.loads(block)
            if isinstance(d, dict) and d.get("@type") == "ItemList":
                for it in d.get("itemListElement", []):
                    if it.get("name"):
                        out[norm(it["name"])] = {"name": it["name"], "url": it.get("url", url),
                                                 "detail": "Applywave accredited 482 sponsor (FOI list)"}
        if len(out) == before:
            break   # no new names → pagination ended (or not supported)
        time.sleep(0.3)
    return out


_LOADED: dict[str, dict[str, dict]] | None = None


def lists() -> dict[str, dict[str, dict]]:
    """{country: {normalized company: evidence}} — loaded once per run."""
    global _LOADED
    if _LOADED is None:
        cfg = load("config").get("sponsorship", {})
        _LOADED = {"GB": {}, "AU": {}}
        if cfg.get("uk_register", True):
            _LOADED["GB"] = _cached("uk_register", _uk_register)
        if cfg.get("au_directories", True):
            _LOADED["AU"] = {**_cached("au_applywave", _applywave), **_cached("au_sponsormap", _sponsormap)}
    return _LOADED


def lookup(company: str, country: str) -> dict | None:
    table = lists().get(country.upper())
    if not table:
        return None
    key = norm(company)
    return table.get(key) if key else None
