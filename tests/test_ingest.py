"""Ingestion: dedup, mapping, ATS classification, change detection, expiry.

All offline. The ATS test runs against the 58 real listing URLs salvaged from the old
dashboard, so the classification percentages are measured, not assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from internship_os.ingest.base import (
    OpportunityDraft,
    canonical_company,
    make_dedup_key,
    normalize_title,
)
from internship_os.ingest.service import (
    SyncReport,
    classify_ats,
    expire_past_deadline,
    seed_ats_platforms,
    upsert_draft,
)
from internship_os.ingest.trackr import TrackrAdapter
from internship_os.models.application import AtsPlatform
from internship_os.models.company import Company
from internship_os.models.opportunity import Opportunity, OpportunityStateEvent

FIXTURE = Path(__file__).parent / "fixtures" / "dashboard_listings.json"

# One record in the CLI's documented `Result` shape.
TRACKR_ROW = {
    "id": "vz48bwh47m",
    "title": "2027 Academy Investment Summer Internship",
    "company": "Point72",
    "location": "London",
    "date": "2026-03-02",
    "url": "https://job-boards.greenhouse.io/point72/jobs/8435134002",
    "deadline": "2026-06-14",
    "category": "Buy-Side",
    "eligibility": None,
    "currentStage": "Offers Out",
    "rolling": True,
    "cv": True,
    "coverLetter": "No",
    "writtenAnswers": "Yes",
    "acceptanceRate": None,
    "conversionRate": None,
    "sponsorsVisa": True,
    "notes": "2-5 telephone interviews across HR and traders",
}


class TestNormalisation:
    def test_company_folding(self):
        assert canonical_company("Grant Thornton (UK) LLP") == "grant thornton uk"
        assert canonical_company("Jane Street") == canonical_company("jane street")

    def test_title_variants_collapse(self):
        """Sources word the same programme differently; the year and spelling vary."""
        a = normalize_title("2027 Summer Internship Program")
        b = normalize_title("Summer Internship Programme 2027")
        assert a == b

    def test_distinct_streams_stay_distinct(self):
        """BlackRock lists finance and tech streams separately and you apply to one."""
        assert normalize_title("2027 Summer Internship Program - EMEA") != normalize_title(
            "2027 Summer Internship Program"
        )

    def test_dedup_key_is_scoped_by_season(self):
        base = ("Jane Street", "Quant Trading Intern")
        assert make_dedup_key(*base, "2027", "UK") != make_dedup_key(*base, "2028", "UK")


class TestMapping:
    def test_trackr_row_maps_to_draft(self):
        draft = TrackrAdapter().to_draft(TRACKR_ROW, industry="Finance", season="2027")
        assert draft.company_name == "Point72"
        assert draft.sponsors_visa == "yes"
        assert draft.is_quant is True, "Point72 is a quant/buy-side firm"
        assert draft.closing_date == "2026-06-14"
        assert draft.requires_cover_letter == "no"
        assert draft.requires_written_answers == "yes"
        assert draft.categories == ["Buy-Side"]
        assert draft.raw_payload == TRACKR_ROW, "raw payload is the hedge against schema drift"

    def test_visa_tristate(self):
        adapter = TrackrAdapter()
        assert adapter.to_draft({**TRACKR_ROW, "sponsorsVisa": True}).sponsors_visa == "yes"
        assert adapter.to_draft({**TRACKR_ROW, "sponsorsVisa": False}).sponsors_visa == "no"
        assert adapter.to_draft({**TRACKR_ROW, "sponsorsVisa": None}).sponsors_visa == "unknown"


class TestPersistence:
    async def test_upsert_creates_then_dedups(self, session):
        await seed_ats_platforms(session)
        adapter = TrackrAdapter()
        draft = adapter.to_draft(TRACKR_ROW, industry="Finance", season="2027")

        report = SyncReport()
        await upsert_draft(session, draft, report)
        await session.commit()
        assert report.created == 1

        # Same programme seen again in a later sync.
        report2 = SyncReport()
        await upsert_draft(session, draft, report2)
        await session.commit()
        assert report2.created == 0 and report2.unchanged == 1

        n = await session.scalar(select(func.count()).select_from(Opportunity))
        assert n == 1, "the same programme must never become two rows"

    async def test_deadline_change_is_reported(self, session):
        await seed_ats_platforms(session)
        adapter = TrackrAdapter()
        await upsert_draft(
            session, adapter.to_draft(TRACKR_ROW), SyncReport()
        )
        await session.commit()

        moved = adapter.to_draft({**TRACKR_ROW, "deadline": "2026-05-01"})
        report = SyncReport()
        await upsert_draft(session, moved, report)
        await session.commit()

        assert report.updated == 1
        assert any("closing_date" in c for c in report.changes), report.changes

    async def test_discovery_writes_a_state_event(self, session):
        await seed_ats_platforms(session)
        await upsert_draft(
            session, TrackrAdapter().to_draft(TRACKR_ROW), SyncReport()
        )
        await session.commit()
        n = await session.scalar(
            select(func.count())
            .select_from(OpportunityStateEvent)
            .where(OpportunityStateEvent.to_state == "DISCOVERED")
        )
        assert n == 1

    async def test_known_visa_answer_is_not_overwritten_by_unknown(self, session):
        await seed_ats_platforms(session)
        adapter = TrackrAdapter()
        await upsert_draft(session, adapter.to_draft(TRACKR_ROW), SyncReport())
        await session.commit()

        # A later sync where the field came back empty must not erase what we knew.
        await upsert_draft(
            session,
            adapter.to_draft({**TRACKR_ROW, "sponsorsVisa": None}),
            SyncReport(),
        )
        await session.commit()
        company = (
            await session.execute(select(Company).where(Company.name == "Point72"))
        ).scalar_one()
        assert company.sponsors_visa == "yes"

    async def test_past_deadline_expires(self, session):
        await seed_ats_platforms(session)
        stale = TrackrAdapter().to_draft({**TRACKR_ROW, "deadline": "2020-01-01"})
        await upsert_draft(session, stale, SyncReport())
        await session.commit()

        report = SyncReport()
        await expire_past_deadline(session, report)
        await session.commit()
        assert report.expired == 1
        opp = (await session.execute(select(Opportunity))).scalar_one()
        assert opp.state == "EXPIRED"


class TestAtsClassification:
    @pytest.mark.skipif(not FIXTURE.exists(), reason="listing fixture missing")
    async def test_real_urls_classify(self, session):
        """Measured against the 58 real listings, not assumed."""
        await seed_ats_platforms(session)
        listings = json.loads(FIXTURE.read_text())["listings"]

        by_key: dict[str, int] = {}
        for listing in listings:
            ats_id = await classify_ats(session, listing["url"])
            platform = await session.get(AtsPlatform, ats_id)
            by_key[platform.key] = by_key.get(platform.key, 0) + 1

        assert by_key.get("greenhouse", 0) >= 15, by_key
        assert by_key.get("workday", 0) >= 8, by_key
        assert by_key.get("talnet", 0) >= 2, by_key
        assert sum(by_key.values()) == len(listings), "every URL must land somewhere"

    async def test_workday_is_never_marked_assisted(self, session):
        """Workday needs an account with a password, which this system refuses to create."""
        await seed_ats_platforms(session)
        workday = (
            await session.execute(select(AtsPlatform).where(AtsPlatform.key == "workday"))
        ).scalar_one()
        assert workday.automation_support == "manual_only"

    async def test_unknown_host_falls_back_to_bespoke(self, session):
        await seed_ats_platforms(session)
        ats_id = await classify_ats(session, "https://careers.somebank.com/apply/123")
        platform = await session.get(AtsPlatform, ats_id)
        assert platform.key == "bespoke"
        assert platform.automation_support == "manual_only"
