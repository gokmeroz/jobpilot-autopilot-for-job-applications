"""
Resume PDF → Markdown for the LLM scoring prompt.

The PDF at `apply.resume_file` stays the file we upload to ATS forms; this
module turns its text into Markdown so the scorer sees every skill and metric
on it, not just what was hand-copied into CANDIDATE.md.

- Text PDFs are read directly with PyMuPDF. Scanned (image-only) PDFs fall
  back to Tesseract OCR via PyMuPDF — requires `brew install tesseract`.
- Contact details are redacted: the header block above the first section
  (name, phone, email, profile links) is dropped, and any remaining email
  addresses, phone numbers, and CANDIDATE.md identity values are removed.
  This text leaves the machine in API calls.
- The result is cached next to the PDF (resume.pdf → resume.md) and rebuilt
  only when the PDF's hash changes.

Regenerate and inspect by hand:  make resume
"""
from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache
from pathlib import Path

from src.config import ROOT, load

log = logging.getLogger(__name__)

# Below this many characters of extracted text we assume the PDF is scanned.
_MIN_TEXT_CHARS = 200

_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿]")
_BULLET     = re.compile(r"^[•●▪◦■\-*]\s*")
_EMAIL      = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Single separators only, so "122E085      11/2023" (code + right-aligned date) isn't a phone.
_PHONE      = re.compile(r"\+?\d(?:[ ().-]?\d){8,}")
_DATE_RANGE = re.compile(r"\d{2}/\d{4}\s*[–-]\s*(?:\d{2}/\d{4}|Present)", re.IGNORECASE)
_LABEL      = re.compile(r"^[A-Z][\w &/+-]{1,30}:\s")  # "Languages: Python, ..."
_HASH_LINE  =re.compile(r"^<!-- source-sha256: ([0-9a-f]{64})")


def _is_heading(line: str) -> bool:
    """Section headings on resumes are short all-caps lines: 'PROFESSIONAL EXPERIENCE'."""
    letters = [c for c in line if c.isalpha()]
    return 4 <= len(line) <= 40 and len(letters) >= 4 and line == line.upper()


def _redact(line: str, terms: list[str]) -> str:
    line = _EMAIL.sub("", line)
    line = _PHONE.sub("", line)
    for term in terms:
        line = re.sub(re.escape(term), "", line, flags=re.IGNORECASE)
    return line


def to_markdown(text: str, redact_terms: list[str] | None = None) -> str:
    """Convert extracted resume text to Markdown. Pure — no I/O."""
    terms = [t for t in (redact_terms or []) if t and len(t) >= 4]
    out: list[str] = []
    seen_heading = False

    for raw in text.splitlines():
        line = _ZERO_WIDTH.sub("", raw).strip()
        if not line:
            continue

        is_bullet = bool(_BULLET.match(line))
        line = _redact(_BULLET.sub("", line), terms).strip(" •|·")
        if not re.search(r"\w", line):
            continue  # nothing left but separators after redaction

        if _is_heading(line):
            seen_heading = True
            out.append(f"\n## {line.title()}\n")
            continue
        if not seen_heading:
            continue  # header block: name, headline, contact links

        # Right-aligned dates/locations show up as long runs of spaces.
        line = re.sub(r"\s{3,}", " · ", line)
        prev = out[-1] if out else ""

        if is_bullet:
            out.append("- " + line)
        elif _DATE_RANGE.search(line):
            out.append(f"\n### {line}")  # entry header: "Company — tagline · 04/2025 – 07/2025"
        elif (
            prev
            and not prev.startswith(("\n", "#"))
            and " · " not in prev  # role/location line under an entry header
            and not prev.endswith((".", "!", "?"))
            and not _LABEL.match(line)
        ):
            out[-1] += " " + line  # wrapped continuation of the previous line
        else:
            out.append(line)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def _extract_text(pdf_path: Path) -> str:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        text = "\n".join(page.get_text(sort=True) for page in doc)
        if len(text.strip()) >= _MIN_TEXT_CHARS:
            return text

        log.info("resume: %s has no text layer — running OCR", pdf_path.name)
        try:
            return "\n".join(
                page.get_text(sort=True, textpage=page.get_textpage_ocr(full=True))
                for page in doc
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"{pdf_path.name} looks scanned and OCR failed ({exc}). "
                "Install Tesseract (`brew install tesseract`) or export the resume as a text PDF."
            ) from exc


def _identity_terms() -> list[str]:
    """Identity values from CANDIDATE.md, plus the handle part of profile URLs."""
    from src.apply.candidate import load_candidate

    c = load_candidate()
    terms = [c.full_name, c.email, c.phone]
    for url in (c.linkedin_url, c.github_url, c.portfolio_url, c.twitter_url):
        if url:
            bare = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
            terms += [url, bare]
    return terms


def resume_md_path() -> Path:
    return (ROOT / load("config")["apply"]["resume_file"]).with_suffix(".md")


def build_resume_markdown(force: bool = False) -> str:
    """Return the redacted resume Markdown, regenerating the cache if the PDF changed.

    Returns "" when there is no resume PDF — scoring then runs on CANDIDATE.md alone.
    """
    pdf_path = ROOT / load("config")["apply"]["resume_file"]
    md_path  = pdf_path.with_suffix(".md")
    if not pdf_path.exists():
        log.warning("resume: %s not found — scoring without resume", pdf_path)
        return ""

    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if not force and md_path.exists():
        cached = md_path.read_text()
        m = _HASH_LINE.match(cached)
        if m and m.group(1) == digest:
            return cached.split("\n", 1)[1]

    body = to_markdown(_extract_text(pdf_path), _identity_terms())
    md_path.write_text(
        f"<!-- source-sha256: {digest} — generated from {pdf_path.name}; "
        "rebuilt automatically when the PDF changes -->\n" + body
    )
    log.info("resume: wrote %s (%d chars)", md_path.relative_to(ROOT), len(body))
    return body


@lru_cache(maxsize=1)
def resume_markdown() -> str:
    """Cached per process — the scorer calls this once per job."""
    return build_resume_markdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(build_resume_markdown(force=True))
    print(f"--- written to {resume_md_path().relative_to(ROOT)} ---")
