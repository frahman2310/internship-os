"""The state machine is the only writer of Opportunity.state, and it must refuse
shortcuts — most importantly, reaching APPLYING without an approval token."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from internship_os.models.application import Approval
from internship_os.models.base import utcnow
from internship_os.models.company import Company
from internship_os.models.opportunity import (
    FitAssessment,
    Opportunity,
    OpportunityStateEvent,
)
from internship_os.strategy.state import (
    GuardFailed,
    IllegalTransition,
    transition,
)


async def _opp(session, state="DISCOVERED") -> Opportunity:
    company = Company(name="Jane Street", canonical_name="jane street")
    session.add(company)
    await session.flush()
    opp = Opportunity(
        company_id=company.id,
        source_adapter="trackr",
        dedup_key=f"key-{state}",
        title="Quantitative Trading Intern",
        state=state,
    )
    session.add(opp)
    await session.flush()
    return opp


async def _pass_gates(session, opp) -> None:
    session.add(
        FitAssessment(
            opportunity_id=opp.id,
            framework_version="1.2.2",
            eligibility_gate="pass",
            language_gate="pass",
            overall=78.0,
            verdict_band="Strong Fit",
        )
    )
    await session.flush()


class TestLegality:
    async def test_illegal_jump_is_refused(self, session):
        opp = await _opp(session)
        with pytest.raises(IllegalTransition, match="not a legal transition"):
            await transition(session, opp, "SUBMITTED")

    async def test_unknown_state_is_refused(self, session):
        opp = await _opp(session)
        with pytest.raises(IllegalTransition, match="unknown state"):
            await transition(session, opp, "PROBABLY_FINE")

    async def test_terminal_states_are_terminal(self, session):
        opp = await _opp(session, state="REJECTED")
        with pytest.raises(IllegalTransition, match="terminal"):
            await transition(session, opp, "QUALIFIED")

    async def test_every_move_writes_exactly_one_event(self, session):
        opp = await _opp(session)
        await _pass_gates(session, opp)
        await transition(session, opp, "QUALIFIED", reason="gates passed")
        await session.commit()

        n = await session.scalar(
            select(func.count())
            .select_from(OpportunityStateEvent)
            .where(OpportunityStateEvent.opportunity_id == opp.id)
        )
        assert n == 1
        assert opp.state == "QUALIFIED"


class TestGates:
    async def test_qualified_requires_a_fit_assessment(self, session):
        opp = await _opp(session)
        with pytest.raises(GuardFailed, match="requires a fit assessment"):
            await transition(session, opp, "QUALIFIED")

    async def test_gate_failure_cannot_be_qualified(self, session):
        """A posting demanding citizenship must route to REJECTED_BY_GATE, not be scored past."""
        opp = await _opp(session)
        session.add(
            FitAssessment(
                opportunity_id=opp.id,
                framework_version="1.2.2",
                eligibility_gate="fail",
                eligibility_quote="Applicants must hold UK citizenship.",
                language_gate="pass",
            )
        )
        await session.flush()
        with pytest.raises(GuardFailed, match="REJECTED_BY_GATE"):
            await transition(session, opp, "QUALIFIED")

        # ...but the honest route stays open, and the quote is preserved.
        await transition(session, opp, "REJECTED_BY_GATE", reason="citizenship required")
        await session.commit()
        assert opp.state == "REJECTED_BY_GATE"


class TestApprovalBoundary:
    async def test_applying_without_approval_is_refused(self, session):
        opp = await _opp(session, state="APPROVED")
        with pytest.raises(GuardFailed, match="unconsumed approval token"):
            await transition(session, opp, "APPLYING")

    async def test_expired_approval_is_refused(self, session):
        opp = await _opp(session, state="APPROVED")
        session.add(
            Approval(
                subject_type="application_submission",
                subject_id=opp.id,
                scope_hash="abc123",
                expires_at="2020-01-01T00:00:00Z",
            )
        )
        await session.flush()
        with pytest.raises(GuardFailed, match="expired"):
            await transition(session, opp, "APPLYING")

    async def test_valid_approval_permits_applying(self, session):
        opp = await _opp(session, state="APPROVED")
        session.add(
            Approval(
                subject_type="application_submission",
                subject_id=opp.id,
                scope_hash="abc123",
                expires_at="2099-01-01T00:00:00Z",
            )
        )
        await session.flush()
        await transition(session, opp, "APPLYING")
        await session.commit()
        assert opp.state == "APPLYING"
        assert opp.state_changed_at <= utcnow()


def test_step_towards_finds_a_path_to_submitted():
    """Recording a human submission walked eight hardcoded states. The path is derived
    from TRANSITIONS instead, and never routes through a terminal state."""
    from internship_os.cli import _step_towards
    from internship_os.strategy.state import TRANSITIONS

    assert _step_towards("APPROVED", TRANSITIONS) == "APPLYING"
    assert _step_towards("APPLYING", TRANSITIONS) == "SUBMITTED"
    first = _step_towards("DISCOVERED", TRANSITIONS)
    assert first == "QUALIFIED"
    assert _step_towards("SUBMITTED", TRANSITIONS) != "WITHDRAWN"
    assert _step_towards("OFFER", TRANSITIONS) is None      # terminal, no path
