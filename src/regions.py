"""
Region presets for the CLI (--turkey, --eu, --eu-uk, ...).

A preset expands to a country allow-list (ISO2 or REMOTE) used by the gate,
and to the subset of search markets that discovery should query.
"""
from __future__ import annotations

EU: list[str] = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
]

# None = no restriction
REGIONS: dict[str, list[str] | None] = {
    "turkey": ["TR"],
    "eu":     EU,
    "uk":     ["GB"],
    "eu-uk":  EU + ["GB"],
    "remote": ["REMOTE"],
    "all":    None,
}


def resolve(regions: list[str] | None, countries: list[str] | None) -> list[str] | None:
    """
    Union region presets and explicit --countries codes into one allow-list.
    Returns None (no restriction) if nothing was given or 'all' was selected.
    """
    regions = regions or []
    if "all" in regions:
        return None
    codes: list[str] = []
    for r in regions:
        codes += REGIONS[r] or []
    codes += countries or []
    return list(dict.fromkeys(codes)) or None
