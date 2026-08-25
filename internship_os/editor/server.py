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
from . import assist, store

PAGE = Path(__file__).parent / "app.html"


def draft_to_latex(draft: store.Draft) -> str:
    """Render an edited draft with the same preamble the built CVs use, so what you see
    here is what a compiled application document looks like."""
    parts = [
        preamble(),
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
            if route.path == "/api/draft":
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
                self._json({
                    "ok": True,
                    "pages": pages,
                    "overfull": overfull_count(log),
                    "url": f"/api/pdf?slug={draft.slug}",
                    "path": str(pdf),
                })
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


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
