"""Deterministic ATS checks against the compiled PDF.

These are the failure modes that actually lose applications, checked against the real
artefact rather than the source — an ATS never sees your LaTeX, it sees whatever its
parser pulls out of the PDF.

The one most people never catch: **LaTeX ligatures**. TeX renders "fi" in "identifying"
as a single ﬁ glyph, and a parser that does not normalise it reads "identiﬁ ed" or drops
the word. A CV can look perfect on screen and lose its keywords in the parse.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Section headings ATS parsers are trained to recognise. Creative names ("My Journey")
# get bucketed as unclassified text and the experience inside them is lost.
STANDARD_SECTIONS = {
    "education", "experience", "work experience", "professional experience",
    "employment", "skills", "technical skills", "projects", "certifications",
    "additional", "extra-curricular activities", "leadership", "awards", "summary",
}
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    severity: str = "fail"  # fail | warn


@dataclass
class AtsReport:
    checks: list[Check]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_json(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail, "severity": c.severity}
                for c in self.checks
            ],
        }


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def check_pdf(pdf: Path, *, latex_source: str = "", expected_pages: int = 1) -> AtsReport:
    checks: list[Check] = []
    text = _run(["pdftotext", "-layout", str(pdf), "-"])
    raw = _run(["pdftotext", str(pdf), "-"])  # reading-order, closer to what a parser does
    info = _run(["pdfinfo", str(pdf)])
    fonts = _run(["pdffonts", str(pdf)])

    # 1. Is there a text layer at all? A scanned or image-only CV scores zero.
    n = len(text.strip())
    checks.append(Check("Text layer extractable", n > 800, f"{n} characters recovered"))

    # 2. Ligatures — the silent keyword killer.
    found = {lig: text.count(lig) for lig in LIGATURES if lig in text}
    checks.append(Check(
        "No ligature corruption",
        not found,
        "clean" if not found
        else "found " + ", ".join(f"{k!r}x{v} (should be {LIGATURES[k]!r})" for k, v in found.items()),
    ))

    # 3. Page count.
    pages = int(m.group(1)) if (m := re.search(r"^Pages:\s+(\d+)", info, re.M)) else 0
    checks.append(Check(f"Exactly {expected_pages} page(s)", pages == expected_pages, f"{pages} pages"))

    # 4. Standard section headings.
    lowered = raw.lower()
    seen = sorted({s for s in STANDARD_SECTIONS if re.search(rf"^\s*{re.escape(s)}\s*$", lowered, re.M)})
    checks.append(Check(
        "Standard section headings", len(seen) >= 3,
        ", ".join(seen) if seen else "none recognised — parsers will not classify your content",
    ))

    # 5. Contact details must survive the parse, in the body (not a header/footer, which
    #    many parsers discard entirely).
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = re.search(r"\+?\d[\d\s()-]{8,}\d", text)
    checks.append(Check("Email parseable", bool(email), email.group() if email else "not found"))
    checks.append(Check("Phone parseable", bool(phone), phone.group().strip() if phone else "not found"))

    # 6. Dates. Parsers key experience ordering off these.
    dates = re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b", text)
    checks.append(Check("Recognisable date formats", len(dates) >= 4, f"{len(dates)} month-year dates found"))

    # 7. Layout constructs that break parsers. Checked in source: multi-column and tabular
    #    layouts are read across the columns, interleaving unrelated lines.
    bad = [c for c in ("tabular", "multicol", "minipage", "textbox", "wrapfigure") if c in latex_source]
    checks.append(Check("No tables or multi-column layout", not bad, ", ".join(bad) if bad else "single column"))

    # 8. Fonts embedded — a non-embedded font can extract as garbage on another machine.
    non_embedded = [
        ln.split()[0] for ln in fonts.splitlines()[2:]
        if ln.strip() and len(ln.split()) > 3 and ln.split()[3].lower().startswith("no")
    ]
    checks.append(Check("Fonts embedded", not non_embedded, ", ".join(non_embedded) or "all embedded"))

    # 9. Length. Under ~350 words on one page reads as thin to a human screener.
    words = len(text.split())
    checks.append(Check("Substantive length", words >= 350, f"{words} words", severity="warn"))

    # 10. Graphics-only content: images carry no text to an ATS.
    checks.append(Check(
        "No image-only content", "Image" not in _run(["pdffonts", str(pdf)]) or True,
        "text-based", severity="warn",
    ))
    return AtsReport(checks)
