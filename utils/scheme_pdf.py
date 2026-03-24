"""Generate a simple PDF summary for a scheme when no source PDF is available."""
from __future__ import annotations

from fpdf import FPDF


def _safe_ascii(text: str, max_len: int = 8000) -> str:
    if not text:
        return ""
    s = str(text).strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s.encode("latin-1", "replace").decode("latin-1")


def build_scheme_summary_pdf_bytes(scheme: dict) -> bytes:
    """
    Build a minimal multi-section PDF from Firestore scheme fields.
    Uses core fonts only (ASCII-safe); non-Latin characters may appear as placeholders.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_left_margin(14)
    pdf.set_right_margin(14)
    pdf.add_page()
    w = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 16)
    title = _safe_ascii(scheme.get("name") or scheme.get("title") or "Government Scheme", 200)
    pdf.multi_cell(w, 9, title)
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(w, 5, _safe_ascii("Generated summary from SchemeScan / PolicyPilot database."))
    pdf.ln(6)
    pdf.set_text_color(0, 0, 0)

    sections: list[tuple[str, str]] = []
    for label, key in (
        ("Ministry", "ministry"),
        ("Level", "level"),
        ("Category", "category"),
        ("State", "state"),
        ("Benefits", "benefits"),
        ("Benefits (detail)", "benefit_text"),
        ("Eligibility", "eligibility"),
        ("Scheme ID", "scheme_id"),
    ):
        val = scheme.get(key)
        if val and str(val).strip():
            sections.append((label, str(val)))

    for label, value in sections:
        pdf.set_font("Helvetica", "B", 11)
        pdf.multi_cell(w, 6, _safe_ascii(label))
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(w, 5, _safe_ascii(value))
        pdf.ln(3)

    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.ln(6)
    pdf.multi_cell(
        w,
        4,
        _safe_ascii(
            "Disclaimer: Verify all details on the official government portal or notification before applying."
        ),
    )

    out = pdf.output(dest="S")
    return out if isinstance(out, bytes) else bytes(out)
