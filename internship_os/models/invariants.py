"""Cross-table invariants, enforced at commit time.

SQLite CHECK constraints cannot span tables, so these two rules — the ones the whole
system's trustworthiness rests on — are enforced by session hooks instead. Hooks, not
service-layer helpers, because a helper can be bypassed by the next code path someone adds.

  1. A `fact` claim needs at least one supporting excerpt.
  2. A document version cannot be `approved` while any of its spans is `ungrounded`.

Checked on `before_commit` rather than `before_flush` deliberately: the legitimate way to
build a claim is flush-to-get-an-id, then attach evidence, then commit. Enforcing at flush
would outlaw the correct pattern. Invariants are about committed state.
"""

from __future__ import annotations

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from .document import DocumentClaim, DocumentVersion
from .research import ClaimEvidence, ResearchClaim


class InvariantViolation(Exception):
    """Raised when a commit would break a factual-integrity rule."""


_TOUCHED = "_internship_os_touched"


def _touched(session: Session) -> dict[str, set]:
    return session.info.setdefault(_TOUCHED, {"claims": set(), "versions": set()})


def _record_touched(session: Session, _ctx=None, _instances=None) -> None:
    """Remember which objects this unit of work touched, so commit checks stay O(changed)."""
    bucket = _touched(session)
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, ResearchClaim) and obj.epistemic_class == "fact":
            bucket["claims"].add(obj)
        elif isinstance(obj, DocumentVersion) and obj.status == "approved":
            bucket["versions"].add(obj)


def _verify(session: Session) -> None:
    # Flush FIRST: before_commit fires ahead of commit's own flush, so anything added but
    # never explicitly flushed would otherwise be invisible to the checks below.
    session.flush()
    bucket = session.info.get(_TOUCHED)
    if not bucket:
        return

    for claim in bucket["claims"]:
        if claim.id is None or claim.epistemic_class != "fact":
            continue
        supporting = session.scalar(
            select(func.count())
            .select_from(ClaimEvidence)
            .where(ClaimEvidence.claim_id == claim.id, ClaimEvidence.stance == "supports")
        )
        if not supporting:
            raise InvariantViolation(
                f"fact claim {claim.statement[:60]!r} has no supporting excerpt; "
                "attach evidence or downgrade it to 'inference'"
            )

    for version in bucket["versions"]:
        if version.id is None or version.status != "approved":
            continue
        ungrounded = session.scalar(
            select(func.count())
            .select_from(DocumentClaim)
            .where(
                DocumentClaim.document_version_id == version.id,
                DocumentClaim.grounding == "ungrounded",
            )
        )
        if ungrounded:
            raise InvariantViolation(
                f"document version {version.id} has {ungrounded} ungrounded claim(s); "
                "ground them against evidence or cut them before approval"
            )

    bucket["claims"].clear()
    bucket["versions"].clear()


_INSTALLED = False


def install(_session_factory=None) -> None:
    """Attach the invariant checks.

    Listens on the sync `Session` class rather than a sessionmaker: an AsyncSession
    delegates to a sync Session underneath, where these events actually fire. Listening on
    the class also means sessions created anywhere are covered.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _record_touched)
    event.listen(Session, "before_commit", _verify)
    _INSTALLED = True
