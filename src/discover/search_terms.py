"""
Shared role × market search matrix for the keyword-search sources
(JSearch, Adzuna, LinkedIn).

Roles and markets live in config.yaml `search:` so they can be tuned
without code changes. Each source gets the matrix in its own shape.
"""
from __future__ import annotations

from src.config import load

# Adzuna only covers a fixed set of countries (no IE, no TR).
_ADZUNA_CC: dict[str, str] = {"DE": "de", "NL": "nl", "GB": "gb", "REMOTE": "us"}


def _cfg() -> dict:
    return load("config").get("search", {})


def markets(allowed: list[str] | None = None) -> dict[str, str]:
    """Configured search markets {ISO2|REMOTE: search name}, narrowed to `allowed`."""
    all_markets: dict[str, str] = _cfg().get("markets", {})
    if allowed is None:
        return dict(all_markets)
    allowed_set = {c.upper() for c in allowed}
    return {iso: name for iso, name in all_markets.items() if iso in allowed_set}


def roles() -> list[str]:
    return list(_cfg().get("roles", []))


def jsearch_queries(allowed: list[str] | None = None) -> list[tuple[str, str]]:
    """[(query, country_hint)]"""
    return [
        (f"{role} {name}", iso)
        for iso, name in markets(allowed).items()
        for role in roles()
    ]


def adzuna_targets(allowed: list[str] | None = None) -> list[tuple[str, str, str]]:
    """[(adzuna_cc, iso2, keywords)]"""
    out = []
    for iso in markets(allowed):
        cc = _ADZUNA_CC.get(iso)
        if not cc:
            continue
        for role in roles():
            if iso == "REMOTE":
                out.append((cc, "US", f"{role} remote"))
            else:
                out.append((cc, iso, role))
    return out


def linkedin_matrix(allowed: list[str] | None = None) -> list[tuple[str, str]]:
    """[(keywords, location)] — location "" searches globally (remote)."""
    out = []
    for iso, name in markets(allowed).items():
        for role in roles():
            if iso == "REMOTE":
                out.append((f"{role} remote", ""))
            else:
                out.append((role, name))
    return out
