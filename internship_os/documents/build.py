"""Build a tailored CV for one opportunity, end to end, and record it as data.

Every bullet is an `evidence_items` row copied verbatim, so each `document_claims` row is
`grounded` and carries the evidence id it came from. The grounding audit is therefore not
a check performed after the fact — it is a property of how the document was assembled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.document import Document, DocumentClaim, DocumentVersion
from .plan import ContentPlan, build_plan
from .render import compile_pdf, overfull_count, render
from .verify_pdf import VerificationError, verify_pdf


@dataclass
class BuildResult:
    document_id: int
    version_id: int
    version: int
    pdf_path: Path
    page_count: int
    overfull: int
    bullets: int
    dropped: list[str]
    warnings: list[str]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "untitled"


async def build_cv(session: AsyncSession, opportunity_id: int) -> BuildResult:
    plan: ContentPlan = await build_plan(session, opportunity_id)

    slug = f"cv-{slugify(plan.company_name)}-{slugify(plan.opportunity.title)}"[:80]
    document = (await session.execute(
        select(Document).where(
            Document.opportunity_id == opportunity_id, Document.kind == "cv"
        )
    )).scalars().first()
    if document is None:
        document = Document(opportunity_id=opportunity_id, kind="cv", slug=slug)
        session.add(document)
        await session.flush()

    last = (await session.execute(
        select(DocumentVersion.version)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version.desc())
    )).scalars().first()
    version_number = (last or 0) + 1

    tex = render(plan)
    out_dir = Path(settings.artifacts_dir) / "cv" / slug
    pdf_path, log = compile_pdf(tex, out_dir, f"v{version_number}")

    warnings: list[str] = []
    page_count = 0
    report: dict = {}
    try:
        report = verify_pdf(pdf_path, expected_pages=1, min_chars=800) or {}
        page_count = report.get("pages", 0) if isinstance(report, dict) else 0
    except VerificationError as exc:
        # A two-page CV is a real defect, but the artefact still needs recording so the
        # next build can diff against it. Surface loudly, do not silently drop.
        warnings.append(str(exc))
        report = {"error": str(exc)}

    if not page_count:
        page_count = _page_count(pdf_path)
    if page_count != 1:
        warnings.append(f"CV is {page_count} pages — uk-1page must be exactly 1")

    overfull = overfull_count(log)
    if overfull:
        warnings.append(f"{overfull} overfull hbox(es) — text runs past the margin")

    version = DocumentVersion(
        document_id=document.id,
        version=version_number,
        status="draft",
        source_format="latex",
        source_text=tex,
        rendered_path=str(pdf_path),
        page_count=page_count,
        verify_report={**report, "overfull": overfull, "warnings": warnings},
        base_cv="uk-1page",
        created_by="agent",
        model=None,  # selection is deterministic; no model produced this
    )
    session.add(version)
    await session.flush()

    bullets = 0
    for entry in [*plan.education, *plan.work, *plan.projects]:
        for bullet in entry.bullets:
            session.add(
                DocumentClaim(
                    document_version_id=version.id,
                    text=bullet.text,
                    evidence_item_id=bullet.evidence_item_id,
                    grounding="grounded",
                    audit_note="verbatim evidence_item, selected deterministically",
                )
            )
            bullets += 1

    document.current_version_id = version.id
    await session.commit()

    return BuildResult(
        document_id=document.id,
        version_id=version.id,
        version=version_number,
        pdf_path=pdf_path,
        page_count=page_count,
        overfull=overfull,
        bullets=bullets,
        dropped=plan.dropped,
        warnings=warnings,
    )


def _page_count(pdf_path: Path) -> int:
    import subprocess
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf_path)], capture_output=True, text=True, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(match.group(1)) if match else 0
