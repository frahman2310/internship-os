"""The research agent roster: prompt + output schema pairs, not a class hierarchy.

Each is one job with one shape. The orchestrator owns the loop; these own nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm.schema_call import call_schema

SYSTEM = (
    "You are a research analyst supporting one candidate's UK internship applications. "
    "You are rigorous about evidence and comfortable saying you do not know. "
    "You never invent quotes, statistics, names or dates. "
    "You distinguish what a source states from what you infer from it."
)


# --- planner --------------------------------------------------------------------------


class PlannedQuestion(BaseModel):
    text: str = Field(description="A specific, answerable research question")
    topic: str = Field(
        description=(
            "One of: culture, process, comp, tech_stack, recent_news, interview_style, "
            "hiring_bar, sponsorship_policy, conversion, red_flags"
        )
    )
    priority: int = Field(default=5, ge=1, le=10)


class ResearchPlan(BaseModel):
    questions: list[PlannedQuestion]


async def plan_research(company: str, role: str, goal: str, n: int = 6) -> ResearchPlan:
    prompt = (
        f"Company: {company}\nRole: {role}\nGoal: {goal}\n\n"
        f"List {n} specific research questions that would materially change how this "
        "candidate applies. Prefer questions with checkable answers (process stages, "
        "stated eligibility, sponsorship policy, recent business developments) over "
        "questions that invite speculation. Avoid anything answerable only by guessing."
    )
    return await call_schema(
        prompt, ResearchPlan, system=SYSTEM, purpose="research:plan"
    )


# --- extractor ------------------------------------------------------------------------


class Excerpt(BaseModel):
    quote: str = Field(description="Text copied EXACTLY from the page, 10-320 characters")
    relevance: str = Field(description="Which question this answers, in one clause")


class Extraction(BaseModel):
    excerpts: list[Excerpt] = Field(default_factory=list)


async def extract_excerpts(question: str, page_text: str, max_chars: int = 12000) -> Extraction:
    """Pull verbatim spans. Anything not literally present is discarded by the caller."""
    prompt = (
        f"Question: {question}\n\n"
        f"PAGE TEXT (verbatim):\n---\n{page_text[:max_chars]}\n---\n\n"
        "Copy at most 4 spans from the page text that bear on the question. "
        "Each quote must be copied character-for-character from the page above. "
        "Do not paraphrase, do not fix typos, do not join separated sentences. "
        "If the page does not address the question, return an empty list."
    )
    return await call_schema(
        prompt, Extraction, system=SYSTEM, purpose="research:extract", temperature=0.0
    )


# --- synthesiser ----------------------------------------------------------------------


class DraftClaim(BaseModel):
    statement: str = Field(description="One sentence a reader could act on")
    topic: str
    epistemic_class: str = Field(description="fact | inference | speculation")
    method: str = Field(description="direct_quote | synthesis | llm_inference")
    rationale: str = Field(description="Why this class, referencing the evidence")
    excerpt_indexes: list[int] = Field(
        default_factory=list, description="0-based indexes of the excerpts supporting this"
    )


class Synthesis(BaseModel):
    claims: list[DraftClaim] = Field(default_factory=list)


async def synthesise_claims(question: str, excerpts: list[str]) -> Synthesis:
    numbered = "\n".join(f"[{i}] {quote}" for i, quote in enumerate(excerpts))
    prompt = (
        f"Question: {question}\n\nEVIDENCE:\n{numbered}\n\n"
        "Write at most 3 claims supported by this evidence.\n"
        "- 'fact': the evidence states it directly. Cite the excerpt index.\n"
        "- 'inference': you are reasoning beyond what is stated.\n"
        "- 'speculation': plausible but thinly supported.\n"
        "A claim asserting what causes hiring success is at best an inference unless a "
        "primary source states the mechanism. Do not assign confidence numbers."
    )
    return await call_schema(
        prompt, Synthesis, system=SYSTEM, purpose="research:synthesise"
    )


# --- red team -------------------------------------------------------------------------


class RedTeam(BaseModel):
    disconfirming_queries: list[str] = Field(
        default_factory=list, description="Searches that would show the claim is wrong"
    )
    concerns: list[str] = Field(
        default_factory=list, description="Reasons this evidence might mislead"
    )


async def red_team(claim: str) -> RedTeam:
    prompt = (
        f"Claim: {claim}\n\n"
        "Give up to 2 web searches whose results would DISCONFIRM this claim if it is "
        "wrong, and note any reason the underlying evidence might mislead (who is likely "
        "to publish about this, and who is not)."
    )
    return await call_schema(prompt, RedTeam, system=SYSTEM, purpose="research:redteam")


# --- gap finder -----------------------------------------------------------------------


class Gaps(BaseModel):
    follow_up_questions: list[PlannedQuestion] = Field(default_factory=list)
    unanswerable: list[str] = Field(default_factory=list)


async def find_gaps(goal: str, answered: list[str], still_open: list[str]) -> Gaps:
    prompt = (
        f"Goal: {goal}\n\nAnswered so far:\n"
        + "\n".join(f"- {a}" for a in answered[:20])
        + "\n\nStill open:\n"
        + "\n".join(f"- {q}" for q in still_open[:20])
        + "\n\nWhich open questions are worth one more search, and which are simply not "
        "answerable from public sources? Return follow-ups only where a search would "
        "plausibly help."
    )
    return await call_schema(prompt, Gaps, system=SYSTEM, purpose="research:gaps")
