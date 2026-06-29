"""
Render a cover letter text string to a temporary PDF file for ATS file-upload fields.

Uses fpdf2 (pure Python, no system dependencies). Turkish/non-Latin characters are
normalised to their closest ASCII equivalents so the built-in Helvetica font renders
them correctly (Goktug Mert Ozdogan, etc.).

Usage:
    path = generate_pdf(cover_letter_text)
    # ... upload path to the form ...
    delete_pdf(path)   # called automatically by BaseFormFiller.cleanup_cover_letter_pdf()
"""
from __future__ import annotations

import logging
import tempfile
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)


_CHAR_MAP = str.maketrans({
    "‘": "'",   # left single quotation mark
    "’": "'",   # right single quotation mark (apostrophe)
    "“": '"',   # left double quotation mark
    "”": '"',   # right double quotation mark
    "–": "-",   # en dash
    "—": "--",  # em dash
    "…": "...", # horizontal ellipsis
    " ": " ",   # non-breaking space
})


def _normalise(text: str) -> str:
    """Replace non-Latin-1 characters so Helvetica renders all text cleanly.

    1. Replace common typographic characters with ASCII equivalents.
    2. NFD-decompose and drop combining diacritics (ö→o, ğ→g, etc.).
    3. Drop any remaining characters outside Latin-1 (rare edge cases).
    """
    text = text.translate(_CHAR_MAP)
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return text.encode("latin-1", errors="replace").decode("latin-1")


def generate_pdf(cover_letter_text: str) -> Path:
    """Convert cover letter text to a PDF and return the path to the temp file.

    The caller is responsible for deleting the file after upload (or use
    BaseFormFiller.cleanup_cover_letter_pdf() which handles this automatically).
    """
    from fpdf import FPDF  # imported lazily — only needed when cover letter PDF is required

    safe_text = _normalise(cover_letter_text)

    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_margins(left=25, top=25, right=25)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_font("Helvetica", size=11)
    pdf.set_text_color(30, 30, 30)

    w = pdf.epw  # effective page width respects margins
    for line in safe_text.splitlines():
        if line.strip():
            pdf.multi_cell(w, 6, line.strip())
        else:
            pdf.ln(4)

    tmp = tempfile.NamedTemporaryFile(prefix="cl_", suffix=".pdf", delete=False)
    tmp.close()
    out = Path(tmp.name)
    pdf.output(str(out))
    log.debug("cover letter PDF → %s (%d B)", out.name, out.stat().st_size)
    return out


def delete_pdf(path: Path | None) -> None:
    """Delete a temporary cover letter PDF. Silent on any error."""
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
        log.debug("deleted temp cover letter PDF: %s", path.name)
    except Exception as exc:
        log.warning("could not delete temp cover letter PDF %s: %s", path, exc)
