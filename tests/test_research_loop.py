"""The orchestrator end to end, with the network and the model replaced by fakes.

Proves the loop actually wires together: plan → search → fetch → extract → discard
non-verbatim quotes → synthesise → score → persist with evidence links → recall next time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import func, select

from internship_os.models.research import (
    ClaimEvidence,
    ResearchClaim,
    ResearchExcerpt,
    ResearchSource,
)
from internship_os.research import agents, orchestrator

PAGE_TEXT = (
    "Jane Street Trading Internship. The internship runs for ten weeks in London. "
    "Our process begins with an online assessment, followed by two rounds of interviews "
    "focused on probability and mental arithmetic. We sponsor visas for interns who need them."
)

VERBATIM = "Our process begins with an online assessment"
PARAPHRASE = "The hiring process kicks off with an online test"  # not in the page


@dataclass
class FakePage:
    url: str = "https://www.janestreet.com/internships/"
    domain: str = "janestreet.com"
    title: str = "Trading Internship"
    text: str = PAGE_TEXT
    sha256: str = "a" * 64
    path: Path = Path("/tmp/fake.txt")
    tier: int = 1
    is_primary: bool = True


@pytest.fixture
def fake_world(monkeypatch):
    """One search hit, one page, one good quote and one invented one."""

    async def fake_search(query, limit=6):
        return [{"url": "https://www.janestreet.com/internships/", "title": "Internship"}]

    async def fake_fetch(url, company_domains=None, timeout=20.0):
        return FakePage()

    async def fake_plan(company, role, goal, n=6):
        return agents.ResearchPlan(
            questions=[
                agents.PlannedQuestion(
                    text="What are the interview stages?", topic="process", priority=9
                )
            ]
        )

    async def fake_extract(question, page_text, max_chars=12000):
        return agents.Extraction(
            excerpts=[
                agents.Excerpt(quote=VERBATIM, relevance="states the process"),
                agents.Excerpt(quote=PARAPHRASE, relevance="invented restatement"),
            ]
        )

    async def fake_synthesise(question, excerpts):
        return agents.Synthesis(
            claims=[
                agents.DraftClaim(
                    statement="The process starts with an online assessment.",
                    topic="process",
                    epistemic_class="fact",
                    method="direct_quote",
                    rationale="stated verbatim on the company site",
                    excerpt_indexes=[0],
                )
            ]
        )

    async def fake_red_team(claim):
        return agents.RedTeam(
            disconfirming_queries=["jane street internship no online assessment"],
            concerns=["company pages describe the intended process, not exceptions"],
        )

    monkeypatch.setattr(orchestrator, "web_search", fake_search)
    monkeypatch.setattr(orchestrator, "fetch_page", fake_fetch)
    monkeypatch.setattr(agents, "plan_research", fake_plan)
    monkeypatch.setattr(agents, "extract_excerpts", fake_extract)
    monkeypatch.setattr(agents, "synthesise_claims", fake_synthesise)
    monkeypatch.setattr(agents, "red_team", fake_red_team)


class TestLoop:
    async def test_invented_quote_is_discarded_verbatim_one_is_kept(self, session, fake_world):
        report = await orchestrator.run_research(
            session,
            company="Jane Street",
            role="Quant Trading Intern",
            subject_id=1,
            depth="quick",
            company_domains={"janestreet.com"},
        )
        await session.commit()

        assert report.discarded_quotes == 1, "the paraphrase must not become evidence"
        excerpts = (await session.execute(select(ResearchExcerpt))).scalars().all()
        assert len(excerpts) == 1
        assert excerpts[0].quote == VERBATIM

    async def test_claim_is_persisted_with_evidence_and_computed_confidence(
        self, session, fake_world
    ):
        await orchestrator.run_research(
            session,
            company="Jane Street",
            role="Quant Trading Intern",
            subject_id=1,
            depth="quick",
            company_domains={"janestreet.com"},
        )
        await session.commit()

        claim = (await session.execute(select(ResearchClaim))).scalars().one()
        assert claim.epistemic_class == "fact"
        # Computed, not taken from the model, and never certain.
        assert 0.05 <= claim.confidence <= 0.95
        assert "tier 1" in claim.confidence_rationale
        assert claim.stale_after > claim.as_of

        links = await session.scalar(select(func.count()).select_from(ClaimEvidence))
        assert links == 1, "a fact claim must be linked to its supporting excerpt"

    async def test_source_provenance_is_recorded(self, session, fake_world):
        await orchestrator.run_research(
            session, company="Jane Street", role="Intern", subject_id=1, depth="quick"
        )
        await session.commit()
        source = (await session.execute(select(ResearchSource))).scalars().one()
        assert source.url.startswith("https://www.janestreet.com")
        assert source.accessed_at
        assert source.content_sha256

    async def test_second_run_recalls_instead_of_refetching(self, session, fake_world):
        """Researching the same topic twice must not spend a second network call."""
        first = await orchestrator.run_research(
            session, company="Jane Street", role="Intern", subject_id=1, depth="quick"
        )
        await session.commit()
        assert first.pages_fetched == 1

        second = await orchestrator.run_research(
            session, company="Jane Street", role="Intern", subject_id=1, depth="quick"
        )
        await session.commit()
        assert second.recalled == 1
        assert second.pages_fetched == 0, "a known, fresh topic should not be re-fetched"

    async def test_no_usable_evidence_leaves_the_question_open(
        self, session, fake_world, monkeypatch
    ):
        """A dry search must produce an honest gap, not a confident guess."""

        async def nothing(query, limit=6):
            return []

        monkeypatch.setattr(orchestrator, "web_search", nothing)
        report = await orchestrator.run_research(
            session, company="Obscure Fund", role="Intern", subject_id=99, depth="quick"
        )
        await session.commit()
        assert report.claims == 0
        assert report.open_questions, "the report must say what it could not establish"
