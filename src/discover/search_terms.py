"""
Shared role-family × market search matrix for the keyword-search sources
(JSearch, Adzuna, LinkedIn, SEEK).

The search profile lives in config.yaml `search:`:
  families            role families A/B/C (gate + reporting use every title)
  paid_roles          the short list queried on paid/metered sources
  sponsorship_queries extra per-market queries for sponsorship-first discovery
  markets             ISO2 -> {name, tier, cities}
"""
from __future__ import annotations

from src.config import load

# Adzuna only covers a fixed set of countries (no IE, no TR).
_ADZUNA_CC: dict[str, str] = {
    "DE": "de", "NL": "nl", "GB": "gb", "US": "us", "CH": "ch", "CA": "ca",
    "AT": "at", "BE": "be", "IT": "it", "AU": "au", "REMOTE": "us",
}


def _cfg() -> dict:
    return load("config").get("search", {})


def _market_name(spec) -> str:
    return spec["name"] if isinstance(spec, dict) else str(spec)


def market_specs(allowed: list[str] | None = None) -> dict[str, dict]:
    """{ISO2|REMOTE: {name, tier, cities}} narrowed to `allowed`."""
    out: dict[str, dict] = {}
    for iso, spec in (_cfg().get("markets") or {}).items():
        if allowed is not None and iso not in {c.upper() for c in allowed}:
            continue
        out[iso] = spec if isinstance(spec, dict) else {"name": spec, "tier": 3}
    return out


def markets(allowed: list[str] | None = None) -> dict[str, str]:
    """Configured search markets {ISO2|REMOTE: search name}, narrowed to `allowed`."""
    return {iso: _market_name(spec) for iso, spec in market_specs(allowed).items()}


def market_tier(iso: str) -> int:
    spec = (_cfg().get("markets") or {}).get(iso)
    return int(spec.get("tier", 3)) if isinstance(spec, dict) else 3


def families() -> dict[str, dict]:
    return dict(_cfg().get("families") or {})


def family_roles() -> list[str]:
    """Every role title across all families (deduped, ordered A→C)."""
    fams = sorted(families().values(), key=lambda f: f.get("priority", "Z"))
    return list(dict.fromkeys(r for f in fams for r in f.get("roles", [])))


def roles() -> list[str]:
    """Roles queried on paid/metered keyword sources."""
    return list(_cfg().get("paid_roles") or _cfg().get("roles") or [])


def sponsorship_queries(iso: str) -> list[str]:
    return list((_cfg().get("sponsorship_queries") or {}).get(iso, []))


def _queries_for(iso: str) -> list[str]:
    return list(dict.fromkeys(roles() + sponsorship_queries(iso)))


def jsearch_queries(allowed: list[str] | None = None) -> list[tuple[str, str]]:
    """[(query, country_hint)]"""
    return [
        (f"{q} {name}", iso)
        for iso, name in markets(allowed).items()
        for q in _queries_for(iso)
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
        for q in _queries_for(iso):
            if iso == "REMOTE":
                out.append((f"{q} remote", ""))
            else:
                out.append((q, name))
    return out


def seek_terms() -> list[str]:
    """SEEK (Australia) search terms: paid roles + AU sponsorship queries."""
    return _queries_for("AU")
