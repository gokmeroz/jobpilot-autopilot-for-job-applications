from __future__ import annotations

import functools
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


@functools.lru_cache(maxsize=None)
def load(name: str) -> dict:
    path = ROOT / "config" / f"{name}.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


def env(key: str, required: bool = False) -> str | None:
    value = os.getenv(key)
    if required and not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value
