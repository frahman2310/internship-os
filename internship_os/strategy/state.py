"""The opportunity state machine.

`transition()` is the ONLY writer of `Opportunity.state`. It writes the audit event in the
same transaction as the move, so state and history can never disagree.

Guards encode rules from the fork's 04-job-evaluation.md and apply.md — most importantly
that a gate failure routes to REJECTED_BY_GATE with the posting's verbatim wording rather
than being silently dropped, and that APPLYING is unreachable without an approval.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.application import Approval
from ..models.base import utcnow
from ..models.document import Document, DocumentClaim, DocumentVersion
from ..models.opportunity import FitAssessment, Opportunity, OpportunityStateEvent

TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"QUALIFIED", "REJECTED_BY_GATE", "EXPIRED", "WITHDRAWN"},
    "QUALIFIED": {"RESEARCHING", "READY_TO_APPLY", "REJECTED_BY_GATE", "EXPIRED", "WITHDRAWN"},
    "RESEARCHING": {"READY_TO_APPLY", "QUALIFIED", "EXPIRED", "WITHDRAWN"},
    "READY_TO_APPLY": {"DRAFTING", "EXPIRED", "WITHDRAWN"},
    "DRAFTING": {"AWAITING_REVIEW", "READY_TO_APPLY", "EXPIRED", "WITHDRAWN"},
    "AWAITING_REVIEW": {"APPROVED", "DRAFTING", "EXPIRED", "WITHDRAWN"},
    "APPROVED": {"APPLYING", "DRAFTING", "WITHDRAWN"},
    "APPLYING": {"SUBMITTED", "AWAITING_REVIEW", "WITHDRAWN"},
    "SUBMITTED": {"ASSESSMENT", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"},
    "ASSESSMENT": {"INTERVIEW", "REJECTED", "OFFER", "WITHDRAWN"},
    "INTERVIEW": {"INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"},
    # terminal
    "OFFER": set(),
    "REJECTED": set(),
    "REJECTED_BY_GATE": set(),
    "WITHDRAWN": set(),
    "EXPIRED": set(),
}


class IllegalTransition(Exception):
    pass


class GuardFailed(Exception):
    pass


async def _guard_qualified(opp: Opportunity, s: AsyncSession) -> None:
    fit = (
        await s.execute(
            select(FitAssessment)
            .where(FitAssessment.opportunity_id == opp.id)
            .order_by(FitAssessment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if fit is None:
        raise GuardFailed("QUALIFIED requires a fit assessment (run the gates first)")
    if fit.eligibility_gate == "fail" or fit.language_gate == "fail":
        raise GuardFailed(
            "gate failure must route to REJECTED_BY_GATE, not QUALIFIED "
            f"(eligibility={fit.eligibility_gate}, language={fit.language_gate})"
        )


async def _guard_awaiting_review(opp: Opportunity, s: AsyncSession) -> None:
    ungrounded = (
        await s.execute(
            select(DocumentClaim.id)
            .join(DocumentVersion, DocumentClaim.document_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(
                Document.opportunity_id == opp.id,
                DocumentClaim.grounding == "ungrounded",
            )
            .limit(1)
        )
    ).first()
    if ungrounded:
        raise GuardFailed(
            "cannot request review while a generated span is ungrounded — "
            "ground it against an evidence item or cut it"
        )


async def _guard_applying(opp: Opportunity, s: AsyncSession) -> None:
    approval = (
        await s.execute(
            select(Approval).where(
                Approval.subject_type == "application_submission",
                Approval.subject_id == opp.id,
                Approval.consumed_at.is_(None),
                Approval.revoked_at.is_(None),
            )
        )
    ).scalars().first()
    if approval is None:
        raise GuardFailed(
            "APPLYING requires an unconsumed approval token for this opportunity"
        )
    if approval.expires_at <= utcnow():
        raise GuardFailed("approval token has expired; re-approve to continue")


GUARDS: dict[str, Callable] = {
    "QUALIFIED": _guard_qualified,
    "AWAITING_REVIEW": _guard_awaiting_review,
    "APPLYING": _guard_applying,
}


async def transition(
    session: AsyncSession,
    opp: Opportunity,
    to_state: str,
    *,
    reason: str | None = None,
    actor: str = "system",
    metadata: dict | None = None,
    skip_guards: bool = False,
) -> Opportunity:
    """Move an opportunity, or raise. The only legal writer of `Opportunity.state`."""
    frm = opp.state
    if to_state not in TRANSITIONS:
        raise IllegalTransition(f"unknown state {to_state!r}")
    if to_state not in TRANSITIONS.get(frm, set()):
        raise IllegalTransition(
            f"{frm} -> {to_state} is not a legal transition "
            f"(legal: {sorted(TRANSITIONS.get(frm, set())) or 'none — terminal'})"
        )

    guard = GUARDS.get(to_state)
    if guard and not skip_guards:
        await guard(opp, session)

    opp.state = to_state
    opp.state_changed_at = utcnow()
    session.add(
        OpportunityStateEvent(
            opportunity_id=opp.id,
            from_state=frm,
            to_state=to_state,
            reason=reason,
            actor=actor,
            event_metadata=metadata,
        )
    )
    return opp
