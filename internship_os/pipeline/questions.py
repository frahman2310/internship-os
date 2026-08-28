"""The application questions each employer actually asks, and drafting against them.

Questions are stored on the opportunity rather than inferred, because a drafted answer to
a question the employer did not ask is worse than no answer: it reads as a recycled essay,
which is precisely what the swap test exists to catch.

Where the questions sit behind a login and cannot be researched, that is recorded as an
explicit gap rather than filled with a plausible guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.opportunity import Opportunity

QUESTIONS_KEY = "_questions"


@dataclass
class Question:
    text: str
    word_limit: int | None = None
    source: str = "researched"      # researched | user_supplied | assumed
    note: str = ""

    def to_json(self) -> dict:
        return {"text": self.text, "word_limit": self.word_limit,
                "source": self.source, "note": self.note}


@dataclass
class QuestionSet:
    opportunity_id: int
    questions: list[Question] = field(default_factory=list)
    needs_cover_letter: bool = False
    gap_note: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.questions) or self.needs_cover_letter


def load(opp: Opportunity) -> QuestionSet:
    payload = opp.raw_payload or {}
    stored = payload.get(QUESTIONS_KEY) or {}
    return QuestionSet(
        opportunity_id=opp.id,
        questions=[Question(**{k: v for k, v in q.items() if k in Question.__annotations__})
                   for q in stored.get("questions", [])],
        needs_cover_letter=stored.get("needs_cover_letter",
                                      str(payload.get("coverLetter", "")).lower() == "yes"),
        gap_note=stored.get("gap_note", ""),
    )


async def store(session: AsyncSession, opportunity_id: int, qs: QuestionSet) -> QuestionSet:
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"opportunity {opportunity_id} not found")
    payload = dict(opp.raw_payload or {})
    payload[QUESTIONS_KEY] = {
        "questions": [q.to_json() for q in qs.questions],
        "needs_cover_letter": qs.needs_cover_letter,
        "gap_note": qs.gap_note,
    }
    opp.raw_payload = payload
    await session.commit()
    return qs


async def draft_all(session: AsyncSession, opportunity_id: int) -> list[dict]:
    """Draft every required piece for one role, through the full guidance chain.

    Answers only. A cover letter is drafted separately because it follows Morgan Stanley's
    letter form, which the answer builder does not apply.
    """
    from ..writing.answers import build_answer
    from ..writing.pieces import save_piece, slugify

    opp = await session.get(Opportunity, opportunity_id)
    qs = load(opp)
    out: list[dict] = []
    for q in qs.questions:
        answer = await build_answer(session, opportunity_id, q.text,
                                    word_limit=q.word_limit or 250)
        piece = await save_piece(
            session, opportunity_id=opportunity_id, kind="form_answers",
            slug=slugify(q.text)[:44], title=q.text[:120], text=answer.text)
        out.append({
            "question": q.text, "words": answer.words, "limit": answer.limit,
            "grounding": answer.grounding, "clean": answer.tells.get("clean", False),
            "version": piece.version, "ok": answer.ok,
        })
    return out
