"""Which opportunities are actually open right now.

This is easy to get wrong and expensive when you do. Trackr's fields do not mean what
their names suggest:

    date      -> the date the application OPENED (absent = not open yet)
    deadline  -> the closing date (usually absent)
    rolling   -> True for most rows, including ones that have not opened

Treating `rolling=True` as "open" counts 383 UK finance roles. Counting only rows that
have actually opened gives 49. The difference is the entire 2027 pipeline that has not
launched yet, and planning application batches around the wrong number wastes weeks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.company import Company
from ..models.opportunity import Opportunity


@dataclass
class LiveRole:
    opportunity: Opportunity
    company: str
    sponsors_visa: str | None
    opened_on: str | None
    deadline: str | None
    rolling: bool

    @property
    def days_left(self) -> int | None:
        if not self.deadline:
            return None
        try:
            return (date.fromisoformat(self.deadline) - date.today()).days
        except ValueError:
            return None

    @property
    def urgency(self) -> str:
        """Rolling deadlines are not relaxed ones: rolling means seats fill as people
        apply, so late is worse than a fixed date you can plan for."""
        d = self.days_left
        if d is None:
            return "rolling"
        if d <= 14:
            return "critical"
        return "soon" if d <= 35 else "normal"


def _payload(opp: Opportunity) -> dict:
    return opp.raw_payload or {}


def is_open(opp: Opportunity, today: str | None = None) -> bool:
    """Opened on or before today, and not past its deadline."""
    today = today or date.today().isoformat()
    p = _payload(opp)
    opened = p.get("date") or opp.opening_date
    if not opened or opened > today:
        return False
    closes = p.get("deadline") or opp.closing_date
    return not closes or closes >= today


async def live_roles(
    session: AsyncSession,
    *,
    industry: str | None = None,
    region: str = "UK",
    quant_only: bool = False,
    today: str | None = None,
) -> list[LiveRole]:
    """Open roles, ordered by how soon you must act: fixed deadlines first (soonest
    first), then rolling ones with the longest-open first — those have been consuming
    seats the longest."""
    stmt = select(Opportunity, Company.name, Company.sponsors_visa).join(
        Company, Company.id == Opportunity.company_id
    )
    if region:
        stmt = stmt.where(Opportunity.region == region)
    if industry:
        stmt = stmt.where(Opportunity.industry == industry)
    if quant_only:
        stmt = stmt.where(Opportunity.is_quant.is_(True))

    out: list[LiveRole] = []
    for opp, company, visa in (await session.execute(stmt)).all():
        if not is_open(opp, today):
            continue
        p = _payload(opp)
        out.append(
            LiveRole(
                opportunity=opp,
                company=company,
                sponsors_visa=visa,
                opened_on=p.get("date") or opp.opening_date,
                deadline=p.get("deadline") or opp.closing_date,
                rolling=bool(p.get("rolling")),
            )
        )
    far = (date.today() + timedelta(days=3650)).isoformat()
    out.sort(key=lambda r: (r.deadline or far, r.opened_on or ""))
    return out


def batches(roles: list[LiveRole], size: int = 10) -> list[list[LiveRole]]:
    return [roles[i : i + size] for i in range(0, len(roles), size)]
