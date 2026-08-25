"""ContentPlan -> LaTeX -> PDF.

The preamble is taken verbatim from `templates/cv/uk-1page/template.tex` rather than
duplicated here: font and spacing choices were tuned once against TinyTeX (notably
tgpagella, because mathpazo needs URW fonts TinyTeX does not ship) and must not drift.
Only the body is generated.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..config import REPO_ROOT, settings
from .plan import ContentPlan, Entry

TEMPLATE = REPO_ROOT / "templates" / "cv" / "uk-1page" / "template.tex"

_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def esc(text: str | None) -> str:
    """Escape for LaTeX. Runs on every candidate-supplied string without exception —
    an unescaped '&' in a company name is a compile failure, and '$8,000' is a maths mode."""
    if not text:
        return ""
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def preamble() -> str:
    body = TEMPLATE.read_text()
    head, sep, _ = body.partition(r"\begin{document}")
    if not sep:
        raise ValueError(f"template has no \\begin{{document}}: {TEMPLATE}")
    return head


def _dates(entry: Entry) -> str:
    """Undated entries (most projects) render blank rather than claiming "Present",
    which would assert an end date the profile never stated."""
    start, end = entry.start_date, entry.end_date
    if start and end:
        return f"{esc(start)} -- {esc(end)}"
    if start:
        return f"{esc(start)} -- Present"
    return esc(end) if end else ""


def _section(title: str, entries: list[Entry]) -> str:
    if not entries:
        return ""
    out = [f"\\section{{{title}}}"]
    for entry in entries:
        descriptor = ""
        if entry.title:
            descriptor = f"— {esc(entry.title)}"
            if entry.location:
                descriptor += f" \\textbar\\ {esc(entry.location)}"
        out.append(f"\\entry{{{esc(entry.organisation)}}}{{{descriptor}}}{{{_dates(entry)}}}")
        if entry.bullets:
            out.append("\\begin{itemize}")
            out.extend(f"  \\item {esc(b.text)}" for b in entry.bullets)
            out.append("\\end{itemize}")
    return "\n".join(out)


def render(plan: ContentPlan) -> str:
    c = plan.candidate
    contact = [x for x in (c.email, c.phone) if x]
    header = " \\quad\\textbar\\quad ".join(esc(x) for x in contact)
    if c.linkedin_url:
        handle = c.linkedin_url.rstrip("/").split("/")[-1]
        header = f"\\href{{{c.linkedin_url}}}{{{esc(handle)}}} \\quad\\textbar\\quad " + header
    if c.github_url:
        gh = c.github_url.rstrip("/").split("/")[-1]
        header += f" \\quad\\textbar\\quad \\href{{{c.github_url}}}{{{esc(gh)}}}"

    additional = []
    if plan.skills_line:
        label = "Technical" if plan.track == "tech" else "Finance"
        additional.append(f"  \\item \\textbf{{{label}:}} {esc(plan.skills_line)}")
    if plan.languages_line:
        additional.append(f"  \\item \\textbf{{Languages:}} {esc(plan.languages_line)}")
    # `work_rights_note` is deliberately NOT rendered: it is an internal routing note
    # that cites skill filenames and gate names. A CV goes to an employer.

    parts = [
        preamble(),
        r"\begin{document}",
        r"\begin{center}",
        f"  {{\\LARGE\\bfseries {esc(c.full_name)}}}\\\\[3pt]",
        f"  {header}",
        r"\end{center}",
        _section("Education", plan.education),
        _section("Work Experience", plan.work),
        _section("Projects", plan.projects),
    ]
    if additional:
        parts += [r"\section{Additional}", r"\begin{itemize}", *additional, r"\end{itemize}"]
    parts.append(r"\end{document}")
    return "\n\n".join(p for p in parts if p) + "\n"


def compile_pdf(tex_source: str, out_dir: Path, stem: str) -> tuple[Path, str]:
    """Run pdflatex twice (hyperref needs the second pass for its refs) and return the PDF.

    Raises with the actual TeX error line rather than the whole log: a 2000-line log with
    the real cause buried at line 1400 is how a font problem stays unfixed for an hour.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / f"{stem}.tex"
    tex_path.write_text(tex_source)

    log = ""
    for _ in range(2):
        result = subprocess.run(
            [str(settings.pdflatex_path), "-interaction=nonstopmode",
             "-halt-on-error", "-output-directory", str(out_dir), str(tex_path)],
            capture_output=True, text=True, check=False,
        )
        log = result.stdout + result.stderr

    pdf_path = out_dir / f"{stem}.pdf"
    if not pdf_path.exists():
        errors = [ln for ln in log.splitlines() if ln.startswith("!")]
        detail = "\n".join(errors[:5]) or log[-800:]
        raise RuntimeError(f"pdflatex produced no PDF:\n{detail}")
    return pdf_path, log


def overfull_count(log: str) -> int:
    """Overfull hboxes mean text is running past the margin — invisible in the page count
    but very visible on paper."""
    return len(re.findall(r"^Overfull \\hbox", log, re.MULTILINE))
