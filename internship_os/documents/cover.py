"""Cover letters: the first documents that need real prose, and so the first that need
the grounding audit to actually work.

A CV is assembled by selecting evidence verbatim, which makes fabrication structurally
impossible. A cover letter cannot be built that way — it has to paraphrase and connect.
So the guarantee is enforced instead of assumed:

  * the drafter is given a numbered menu of evidence and research claims, and must cite
    the ids it used for every paragraph;
  * a paragraph citing nothing is `ungrounded`;
  * a paragraph citing an id that was never offered is `ungrounded`;
  * **every number in a paragraph must appear in something it cited** — this is the check
    that catches quiet metric escalation, where "~85%" becomes "over 90%".

`ungrounded` rows do not block drafting. They block `approved`, which is the invariant
already enforced in models/invariants.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm.schema_call import call_schema
from ..models.candidate import Candidate, EvidenceItem
from ..models.company import Company
from ..models.document import Document, DocumentClaim, DocumentVersion
from ..models.opportunity import Opportunity
from ..models.research import ResearchClaim
from .plan import build_plan, score_evidence, tokenize

SYSTEM = (
    "You draft UK internship cover letters for one candidate. "
    "You may ONLY state things supported by the numbered evidence and research given to "
    "you. You never invent an achievement, a motivation, a conversation, a named "
    "employee, or a number. If you want to say something you cannot support, leave it "
    "out. Understating is always preferred to overstating. Write plainly: no "
    "'I am thrilled', no 'passionate', no manufactured enthusiasm."
)

MAX_EVIDENCE = 20
MAX_CLAIMS = 12

# Ordinals and small counts are ordinary prose ("first year", "two internships") rather
# than metric claims, so requiring a citation for them produces noise, not safety.
_NUMBER = re.compile(r"(?<![\w.])(?:£|\$|€)?\d[\d,]*(?:\.\d+)?\s*(?:%|k|m|bn|x)?", re.I)
_INNOCUOUS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "one", "two", "three"}


class Paragraph(BaseModel):
    text: str = Field(description="One paragraph of the letter, 2-5 sentences")
    evidence_ids: list[int] = Field(
        default_factory=list, description="Ids from the EVIDENCE list this paragraph uses"
    )
    claim_ids: list[int] = Field(
        default_factory=list, description="Ids from the RESEARCH list this paragraph uses"
    )


class CoverLetter(BaseModel):
    paragraphs: list[Paragraph]


@dataclass
class AuditedParagraph:
    text: str
    grounding: str
    note: str
    evidence_id: int | None


@dataclass
class CoverResult:
    document_id: int
    version_id: int
    version: int
    text: str
    paragraphs: list[AuditedParagraph] = field(default_factory=list)
    ungrounded: int = 0

    @property
    def approvable(self) -> bool:
        return self.ungrounded == 0


def normalise_number(token: str) -> str:
    return token.lower().replace(",", "").replace(" ", "").lstrip("£$€")


def numbers_in(text: str) -> set[str]:
    out = set()
    for match in _NUMBER.finditer(text):
        value = normalise_number(match.group())
        if value and value.rstrip("%kmbnx.") not in _INNOCUOUS:
            out.add(value)
    return out


def audit(
    paragraph: Paragraph,
    evidence: dict[int, str],
    claims: dict[int, str],
    context: str = "",
) -> AuditedParagraph:
    """Decide how a drafted paragraph is grounded. Conservative by construction: anything
    it cannot positively verify is downgraded, never waved through."""
    cited_ev = [i for i in paragraph.evidence_ids if i in evidence]
    cited_cl = [i for i in paragraph.claim_ids if i in claims]
    invented = [
        i for i in (*paragraph.evidence_ids, *paragraph.claim_ids)
        if i not in evidence and i not in claims
    ]

    if invented:
        return AuditedParagraph(
            paragraph.text, "ungrounded",
            f"cites id(s) that were never provided: {invented}", None,
        )
    if not cited_ev and not cited_cl:
        return AuditedParagraph(
            paragraph.text, "ungrounded", "cites no evidence or research", None
        )

    source_text = " ".join([evidence[i] for i in cited_ev] + [claims[i] for i in cited_cl])
    # The role being applied for is a legitimate source for its own numbers: naming the
    # "2027" in "Summer Internship 2027" is not a metric claim.
    source_numbers = numbers_in(source_text) | numbers_in(context)
    unsupported = {n for n in numbers_in(paragraph.text) if n not in source_numbers}
    if unsupported:
        return AuditedParagraph(
            paragraph.text, "ungrounded",
            f"number(s) not present in cited sources: {sorted(unsupported)}",
            cited_ev[0] if cited_ev else None,
        )

    # Verbatim reuse is `grounded`; genuine paraphrase is `reframed`. Both are honest —
    # the distinction exists so a reviewer knows which sentences to read closely.
    verbatim = any(evidence[i].strip().lower() in paragraph.text.lower() for i in cited_ev)
    return AuditedParagraph(
        paragraph.text,
        "grounded" if verbatim else "reframed",
        f"cites evidence {cited_ev} claims {cited_cl}",
        cited_ev[0] if cited_ev else None,
    )


async def build_cover_letter(session: AsyncSession, opportunity_id: int) -> CoverResult:
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"opportunity {opportunity_id} not found")
    company = await session.get(Company, opp.company_id)
    candidate = (await session.execute(select(Candidate))).scalars().first()
    if candidate is None:
        raise ValueError("no candidate imported — run `profile import` first")

    plan = await build_plan(session, opportunity_id)
    terms = tokenize(f"{opp.title} {opp.industry or ''}")

    items = (await session.execute(
        select(EvidenceItem).where(
            EvidenceItem.candidate_id == candidate.id, EvidenceItem.is_active.is_(True)
        )
    )).scalars().all()
    items = sorted(items, key=lambda i: -score_evidence(i, set(), terms))[:MAX_EVIDENCE]
    evidence = {i.id: i.text for i in items}

    claim_rows = (await session.execute(
        select(ResearchClaim).where(
            ResearchClaim.subject_id == opportunity_id,
            ResearchClaim.superseded_by_id.is_(None),
            ResearchClaim.confidence >= 0.5,
        ).order_by(ResearchClaim.confidence.desc())
    )).scalars().all()[:MAX_CLAIMS]
    claims = {c.id: c.statement for c in claim_rows}

    evidence_block = "\n".join(f"[E{i}] {t}" for i, t in evidence.items()) or "(none)"
    claims_block = "\n".join(
        f"[C{c.id}] {c.statement} (confidence {c.confidence:.2f})" for c in claim_rows
    ) or "(no verified research yet — do not speculate about this employer)"

    prompt = (
        f"Candidate: {candidate.full_name}, {plan.education[0].organisation if plan.education else ''}\n"
        f"Applying for: {opp.title} at {company.name if company else ''}\n\n"
        f"EVIDENCE (use the numeric id, e.g. 12, in evidence_ids):\n{evidence_block}\n\n"
        f"RESEARCH about this employer (use the numeric id in claim_ids):\n{claims_block}\n\n"
        "Write 4 paragraphs: (1) what is being applied for and the single strongest "
        "reason to take the candidate seriously; (2) directly relevant experience; "
        "(3) why this employer specifically — using ONLY the research above, and if the "
        "research is thin, write a short honest paragraph about the kind of work and "
        "why the candidate wants it — NEVER mention the absence of research, and never "
        "refer to your own sources or process; (4) a short close.\n\n"
        "For each paragraph list every id you used. A paragraph citing nothing will be "
        "rejected. Do not state any number that does not appear in what you cite."
    )

    letter = await call_schema(
        prompt, CoverLetter, system=SYSTEM, purpose="documents:cover", max_tokens=1800
    )

    context = f"{opp.title} {company.name if company else ''} {opp.season or ''}"
    audited = [audit(p, evidence, claims, context) for p in letter.paragraphs]
    body = "\n\n".join(p.text.strip() for p in audited)

    slug = f"cover-{(company.name if company else 'unknown').lower().replace(' ', '-')}"[:80]
    document = (await session.execute(
        select(Document).where(
            Document.opportunity_id == opportunity_id, Document.kind == "cover_letter"
        )
    )).scalars().first()
    if document is None:
        document = Document(opportunity_id=opportunity_id, kind="cover_letter", slug=slug)
        session.add(document)
        await session.flush()

    last = (await session.execute(
        select(DocumentVersion.version)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version.desc())
    )).scalars().first()

    version = DocumentVersion(
        document_id=document.id,
        version=(last or 0) + 1,
        status="draft",
        source_format="text",
        source_text=body,
        created_by="agent",
    )
    session.add(version)
    await session.flush()

    for para in audited:
        session.add(
            DocumentClaim(
                document_version_id=version.id,
                text=para.text,
                evidence_item_id=para.evidence_id,
                grounding=para.grounding,
                audit_note=para.note,
            )
        )

    document.current_version_id = version.id
    await session.commit()

    return CoverResult(
        document_id=document.id,
        version_id=version.id,
        version=version.version,
        text=body,
        paragraphs=audited,
        ungrounded=sum(1 for p in audited if p.grounding == "ungrounded"),
    )
