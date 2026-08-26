"""Local CV editor: a small stdlib HTTP server plus one page.

Deliberately not FastAPI and not a frontend framework. This runs on one machine for one
person; adding a build step would mean the editor breaks whenever the toolchain moves,
which is precisely the failure you do not want between you and a deadline.

Nothing here is exposed beyond localhost, and no route touches a secret.
"""

from __future__ import annotations

import asyncio
import json
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import settings
from ..documents.render import compile_pdf, esc, overfull_count, preamble
from . import assist, ats, store
from ..writing import feedback, notes, pieces

PAGE = Path(__file__).parent / "app.html"
WRITING_PAGE = Path(__file__).parent / "writing.html"


CLASSIC_PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[top=0.85cm,bottom=0.85cm,left=1.2cm,right=1.2cm]{geometry}
\usepackage{tgtermes}          % TeX Gyre Termes — metric-compatible with Times New Roman
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage{needspace}
\usepackage{microtype}
\usepackage[colorlinks=true,urlcolor=blue,linkcolor=blue]{hyperref}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\renewcommand{\normalsize}{\fontsize{9.5}{10.6}\selectfont}
\normalsize
\linespread{1.0}
\titleformat{\section}{\bfseries\normalsize}{}{0em}{\MakeUppercase}%
  [\vspace{-7pt}\rule{\linewidth}{0.6pt}]
\titlespacing{\section}{0pt}{3pt}{1pt}
% Organisation bold left, dates bold hard right; the role sits on its own line beneath.
\newcommand{\jobentry}[3]{\needspace{3\baselineskip}\textbf{#1}\hfill\textbf{#3}\par\textit{#2}\par}
\newcommand{\eduentry}[3]{\needspace{3\baselineskip}\textbf{#1}\hfill\textbf{#3}\par#2\par}
\setlist[itemize]{label=\textbullet, leftmargin=1.4em, topsep=0pt, itemsep=0pt, parsep=0pt, partopsep=0pt}
"""


def classic_latex(draft: store.Draft) -> str:
    """Reproduce the Word/Times layout of the user's own CV, down to which words are bold.

    Education sub-lines are upright and work sub-lines italic — that is how the source
    document does it, and "match completely" means matching that too.
    """
    parts = [
        CLASSIC_PREAMBLE,
        r"\begin{document}",
        r"\begin{center}",
        f"  {{\\LARGE\\bfseries {esc(draft.name)}}}\\\\[2pt]",
        f"  {contact_line(draft.contact)}",
        r"\end{center}",
        r"\vspace{-8pt}",
    ]
    for section in draft.sections:
        entries = [e for e in section.entries if e.org or any(b.strip() for b in e.bullets)]
        if not entries:
            continue
        parts.append(f"\\section{{{esc(section.title)}}}")
        education = section.title.strip().lower().startswith("education")
        for entry in entries:
            if entry.org:
                cmd = "eduentry" if education else "jobentry"
                parts.append(
                    f"\\{cmd}{{{esc(entry.org)}}}{{{esc(entry.role)}}}{{{esc(entry.dates)}}}"
                )
            bullets = [b for b in entry.bullets if b.strip()]
            if not bullets:
                continue
            if entry.org:
                parts.append(r"\begin{itemize}")
                parts.extend(f"  \\item {esc(b.strip())}" for b in bullets)
                parts.append(r"\end{itemize}")
            else:
                # Trailing blocks (OTHER ACTIVITIES) are bold label + upright text, no bullets.
                for b in bullets:
                    label, sep, rest = b.partition(":")
                    parts.append(
                        f"\\textbf{{{esc(label)}:}} {esc(rest.strip())}\\par" if sep
                        else f"{esc(b)}\\par"
                    )
    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


def contact_line(contact: str) -> str:
    """Linkify the email and LinkedIn the way the source CV does, leaving the phone plain."""
    out = []
    for piece in [p.strip() for p in contact.split("|") if p.strip()]:
        if "@" in piece:
            out.append(f"\\href{{mailto:{piece}}}{{{esc(piece)}}}")
        elif "linkedin" in piece.lower() or piece.startswith("www.") or "github" in piece.lower():
            url = piece if piece.startswith("http") else f"https://{piece}"
            out.append(f"\\href{{{url}}}{{{esc(piece)}}}")
        else:
            out.append(esc(piece))
    return " \\textbar\\ ".join(out)


def draft_to_latex(draft: store.Draft) -> str:
    """Render an edited draft with the same preamble the built CVs use, so what you see
    here is what a compiled application document looks like."""
    if getattr(draft, "style", "modern") == "classic":
        return classic_latex(draft)
    # The reference CVs (LSE, Columbia) run denser than the shared uk-1page template,
    # which is tuned for a lighter CV. Overriding here rather than in the template keeps
    # `doc build` output unchanged while letting a full page of real content fit.
    dense = "\n".join([
        r"\geometry{top=1.0cm,bottom=1.0cm,left=1.15cm,right=1.15cm}",
        # The reference CVs set section headings in full caps with a rule beneath, not
        # small caps: "EDUCATION", "WORK EXPERIENCE". Match them exactly.
        r"\titleformat{\section}{\bfseries\large}{}{0em}{\MakeUppercase}"
        r"[\vspace{-6pt}\rule{\linewidth}{0.9pt}]",
        r"\titlespacing{\section}{0pt}{6pt}{2.5pt}",
        r"\setlist[itemize]{leftmargin=1.0em, topsep=0pt, itemsep=0.3pt, parsep=0pt, before=\vspace{0.2pt}}",
        r"\linespread{0.95}",
        # Organisation bold, role regular after an en-dash, dates bold hard right — the
        # shape every one of the sample CVs uses.
        r"\renewcommand{\entry}[3]{\needspace{2.5\baselineskip}\textbf{#1}\,#2\hfill\textbf{#3}\par\vspace{0.5pt}}",
    ])
    parts = [
        preamble(),
        dense,
        r"\begin{document}",
        r"\begin{center}",
        f"  {{\\LARGE\\bfseries {esc(draft.name)}}}\\\\[3pt]",
        f"  {esc(draft.contact)}",
        r"\end{center}",
    ]
    for section in draft.sections:
        entries = [e for e in section.entries if e.org or any(b.strip() for b in e.bullets)]
        if not entries:
            continue
        parts.append(f"\\section{{{esc(section.title)}}}")
        for entry in entries:
            if entry.org:
                descriptor = ""
                if entry.role:
                    descriptor = f"— {esc(entry.role)}"
                    if entry.location:
                        descriptor += f" \\textbar\\ {esc(entry.location)}"
                parts.append(
                    f"\\entry{{{esc(entry.org)}}}{{{descriptor}}}{{{esc(entry.dates)}}}"
                )
            bullets = [b for b in entry.bullets if b.strip()]
            if bullets:
                parts.append(r"\begin{itemize}")
                parts.extend(f"  \\item {esc(b.strip())}" for b in bullets)
                parts.append(r"\end{itemize}")
    parts.append(r"\end{document}")
    return "\n\n".join(parts) + "\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet: the CLI already prints what matters
        return

    # --- helpers ---------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: object, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # --- routes ----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        query = parse_qs(route.query)
        try:
            if route.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif route.path in ("/writing", "/writing.html"):
                self._send(200, WRITING_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif route.path == "/api/writing":
                self._json(asyncio.run(_library()))
            elif route.path == "/api/writing/learned":
                learned = feedback.load()
                self._json({"rules": learned.rules, "edits_seen": learned.edits_seen,
                            "mean_length_delta": learned.mean_length_delta,
                            "dislikes": sorted(learned.dislikes.items(), key=lambda x: -x[1])[:25],
                            "prefers": sorted(learned.prefers.items(), key=lambda x: -x[1])[:25]})
            elif route.path == "/api/writing/notes":
                doc_id = (query.get("document_id") or [None])[0]
                doc_id = int(doc_id) if doc_id not in (None, "", "null") else None
                items = notes.all_notes()
                self._json([
                    {"index": i, **n.to_json()} for i, n in enumerate(items)
                    if not n.resolved and (n.scope == "global" or n.document_id == doc_id)
                ][::-1])
            elif route.path == "/api/writing/guidance":
                doc_id = (query.get("document_id") or [None])[0]
                doc_id = int(doc_id) if doc_id not in (None, "", "null") else None
                self._json({"guidance": notes.combined_guidance(doc_id)})
            elif route.path == "/api/writing/history":
                doc_id = int((query.get("document_id") or ["0"])[0])
                self._json(asyncio.run(_history(doc_id)))
            elif route.path == "/api/drafts":
                self._json(store.list_drafts())
            elif route.path == "/api/draft":
                slug = (query.get("slug") or ["finance"])[0]
                draft = store.load(slug)
                if draft is None:
                    draft = asyncio.run(store.seed_from_profile(slug, slug.title()))
                self._json(draft.to_json())
            elif route.path == "/api/pdf":
                slug = (query.get("slug") or ["finance"])[0]
                pdf = Path(settings.artifacts_dir) / "editor" / f"{store.slugify(slug)}.pdf"
                if not pdf.exists():
                    self._json({"error": "not compiled yet"}, 404)
                    return
                self._send(200, pdf.read_bytes(), "application/pdf")
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # a dead editor mid-deadline is the worst outcome
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        try:
            body = self._body()
            if route.path == "/api/writing/note":
                note = notes.add(
                    body.get("text", ""), scope=body.get("scope", "global"),
                    document_id=body.get("document_id"), quote=body.get("quote", ""),
                    company=body.get("company", ""), kind=body.get("kind", ""),
                )
                self._json({"ok": True, "note": note.to_json()})
            elif route.path == "/api/writing/note/resolve":
                self._json({"ok": notes.resolve(int(body.get("index", -1)))})
            elif route.path == "/api/writing/save":
                self._json(asyncio.run(_save_piece(body)))
            elif route.path == "/api/writing/scan":
                from ..writing.voice import report as voice_report
                self._json(voice_report(body.get("text", "")))
            elif route.path == "/api/draft":
                draft = store.Draft.from_json(body)
                store.save(draft)
                self._json({"ok": True})
            elif route.path == "/api/seed":
                slug = body.get("slug") or "finance"
                draft = asyncio.run(store.seed_from_profile(slug, body.get("label") or slug.title()))
                self._json(draft.to_json())
            elif route.path == "/api/rewrite":
                suggestions = asyncio.run(
                    assist.rewrite(
                        body.get("bullet", ""), role=body.get("role", ""), org=body.get("org", "")
                    )
                )
                self._json([
                    {"text": s.text, "rationale": s.rationale,
                     "invented": s.invented_numbers, "safe": s.safe}
                    for s in suggestions
                ])
            elif route.path == "/api/critique":
                result = asyncio.run(assist.critique(body.get("bullet", "")))
                self._json({"verdict": result.verdict, "problems": result.problems})
            elif route.path == "/api/compile":
                draft = store.Draft.from_json(body)
                store.save(draft)
                out_dir = Path(settings.artifacts_dir) / "editor"
                pdf, log = compile_pdf(draft_to_latex(draft), out_dir, store.slugify(draft.slug))
                pages = _page_count(pdf)
                tex = draft_to_latex(draft)
                report = ats.check_pdf(pdf, latex_source=tex, expected_pages=1)
                self._json({
                    "ok": True,
                    "pages": pages,
                    "overfull": overfull_count(log),
                    "url": f"/api/pdf?slug={draft.slug}",
                    "path": str(pdf),
                    "ats": report.to_json(),
                })
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


async def _library() -> list[dict]:
    from ..db import session_scope

    async with session_scope() as session:
        return [
            {"company": g.company, "role": g.role, "opportunity_id": g.opportunity_id,
             "deadline": g.deadline, "pieces": [p.to_json() for p in g.pieces]}
            for g in await pieces.library(session)
        ]


async def _history(document_id: int) -> list[dict]:
    from ..db import session_scope

    async with session_scope() as session:
        rows = await pieces.versions_of(session, document_id)
        return [{"version": v.version, "created_by": v.created_by,
                 "created_at": v.created_at, "words": pieces.words(v.source_text or "")}
                for v in rows]


async def _save_piece(body: dict) -> dict:
    """Save an edited piece AND fold the change into the learned voice.

    The previous version is read before writing, so the diff is exactly what the user changed.
    Recording happens only for user edits: learning from the machine's own output would
    reinforce whatever it already does.
    """
    from ..db import session_scope

    async with session_scope() as session:
        before = ""
        docs = await pieces.versions_of(session, int(body["document_id"]))
        if docs:
            before = docs[-1].source_text or ""
        piece = await pieces.save_piece(
            session,
            opportunity_id=body.get("opportunity_id"),
            kind=body["kind"], slug=body["slug"], title=body.get("title", ""),
            text=body["text"], by_user=True,
        )
    rules = []
    if before.strip() and before.strip() != body["text"].strip():
        learned = feedback.record(before, body["text"])
        rules = learned.rules[:6]
    return {"ok": True, "version": piece.version, "words": piece.word_count, "rules": rules}


def _page_count(pdf: Path) -> int:
    import re
    import subprocess

    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(match.group(1)) if match else 0


def serve(port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler))
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
