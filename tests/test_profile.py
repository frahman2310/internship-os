"""Profile ingestion: parsing, metrics, idempotency, traceability, consistency reporting.

The traceability test is the important one — it is the mechanism that lets the system claim
nothing about the user was invented.
"""

from __future__ import annotations

import textwrap

import pytest
from sqlalchemy import func, select

from internship_os.ingest.metrics import extract_metric
from internship_os.ingest.profile import (
    cross_check_master_cv,
    import_profile,
    normalize_for_match,
    parse_profile_markdown,
)
from internship_os.models.candidate import Candidate, EvidenceItem, Experience

SAMPLE = textwrap.dedent(
    """\
    ---
    framework_version: 1.1.1
    ---

    # Candidate Profile

    ## Identity
    - **Name:** the user
    - **Email:** user@example.com
    - **Constraints:** Non-EEA national on a **UK Student visa** — prefers sponsors.

    ### Languages

    | Language | Level | Notes |
    |----------|-------|-------|
    | English | Native | |
    | Urdu | Native | |

    ## Education

    | Degree | Period | Institution | Key Topics |
    |--------|--------|-------------|------------|
    | BSc Finance | Sep 2025 – Jun 2028 | Example University | Quant Methods |

    ## Professional Experience

    ### Advisory Intern - Grant Thornton Pakistan (Jul 2026 – Aug 2026)
    Overseas (Advisory / Valuations)
    - Built DCF and present-value models for UAE companies
    - Performed impairment assessments for Zehrat Labnan

    ### Founder & Director - Event Management (Self-Founded) (Sep 2023 – Apr 2025)
    - Oversaw an $8,000 operating budget and delivered $1,000 net profit

    ## Technical Skills

    ### Building / Technology
    - **Languages:** Python, TypeScript
    """
)


@pytest.fixture
def profile_md(tmp_path):
    path = tmp_path / "01-candidate-profile.md"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


class TestMetricExtraction:
    @pytest.mark.parametrize(
        "text,value,unit,approx",
        [
            ("oversaw an $8,000 operating budget", 8000.0, "USD", False),
            ("automated ~85% of the reporting cycle", 85.0, "%", True),
            ("77% each: Financial Economics", 77.0, "%", False),
            ("800+ attendees each", 800.0, "count", True),
            ("330+ tests (pytest)", 330.0, "count", True),
            ("leverage sizing (~6x–7x EBITDA)", 6.0, "x", True),
            ("out of 1,200+ delegates", 1200.0, "count", True),
        ],
    )
    def test_golden_set(self, text, value, unit, approx):
        metric = extract_metric(text)
        assert metric is not None, f"no metric found in {text!r}"
        assert (metric.value, metric.unit) == (value, unit)
        assert metric.approx is approx

    def test_prose_without_numbers_yields_nothing(self):
        assert extract_metric("Conducted FinTech research into a new project") is None


class TestParsing:
    def test_identity_and_languages(self, profile_md):
        parsed = parse_profile_markdown(profile_md)
        assert parsed.identity["name"] == "the user"
        assert parsed.identity["email"] == "user@example.com"
        assert [lang["language"] for lang in parsed.languages] == ["English", "Urdu"]

    def test_experience_heading_split(self, profile_md):
        """Titles contain em-dashes and commas, so the org is after the LAST ' - '."""
        parsed = parse_profile_markdown(profile_md)
        gt = next(e for e in parsed.experiences if e["organisation"] == "Grant Thornton Pakistan")
        assert gt["title"] == "Advisory Intern"
        assert (gt["start_date"], gt["end_date"]) == ("2026-07", "2026-08")
        assert len(gt["bullets"]) == 2
        assert gt["location"].startswith("Overseas")

    def test_org_containing_parentheses_survives(self, profile_md):
        parsed = parse_profile_markdown(profile_md)
        orgs = {e["organisation"] for e in parsed.experiences}
        assert "Event Management (Self-Founded)" in orgs

    def test_education_table_becomes_an_experience(self, profile_md):
        parsed = parse_profile_markdown(profile_md)
        edu = next(e for e in parsed.experiences if e["kind"] == "education")
        assert edu["organisation"] == "Example University"
        assert (edu["start_date"], edu["end_date"]) == ("2025-09", "2028-06")


class TestImport:
    async def test_import_is_idempotent(self, session, profile_md):
        parsed = parse_profile_markdown(profile_md)

        first = await import_profile(session, parsed)
        await session.commit()
        ids_before = sorted(
            (await session.execute(select(EvidenceItem.id))).scalars().all()
        )

        second = await import_profile(session, parsed)
        await session.commit()
        ids_after = sorted(
            (await session.execute(select(EvidenceItem.id))).scalars().all()
        )

        assert first.created_evidence > 0
        assert second.created_evidence == 0, "re-import must not duplicate evidence"
        assert ids_before == ids_after, "evidence ids must be stable across re-imports"

    async def test_every_evidence_item_traces_to_its_source_line(self, session, profile_md):
        parsed = parse_profile_markdown(profile_md)
        await import_profile(session, parsed)
        await session.commit()

        lines = profile_md.read_text(encoding="utf-8").splitlines()
        items = (await session.execute(select(EvidenceItem))).scalars().all()
        assert items
        for item in items:
            assert item.source_file == str(profile_md)
            n = int(item.source_locator[1:])
            assert normalize_for_match(item.text) in normalize_for_match(lines[n - 1]), (
                f"{item.source_locator} does not contain {item.text[:50]!r}"
            )

    async def test_llm_did_not_invent_evidence(self, session, profile_md):
        """Evidence count must track source bullets, not exceed them."""
        parsed = parse_profile_markdown(profile_md)
        report = await import_profile(session, parsed)
        await session.commit()
        n = await session.scalar(select(func.count()).select_from(EvidenceItem))
        assert n == report.source_bullets, (
            f"{n} evidence items from {report.source_bullets} source bullets"
        )

    async def test_metrics_land_on_the_row(self, session, profile_md):
        parsed = parse_profile_markdown(profile_md)
        await import_profile(session, parsed)
        await session.commit()
        budget = (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.text.like("%operating budget%"))
            )
        ).scalars().one()
        assert budget.metric_value == 8000.0
        assert budget.metric_unit == "USD"
        assert budget.category == "metric"

    async def test_dry_run_writes_nothing(self, session, profile_md):
        parsed = parse_profile_markdown(profile_md)
        await import_profile(session, parsed, dry_run=True)
        n = await session.scalar(select(func.count()).select_from(Experience))
        assert n == 0

    async def test_visa_status_is_captured(self, session, profile_md):
        parsed = parse_profile_markdown(profile_md)
        await import_profile(session, parsed)
        await session.commit()
        candidate = (await session.execute(select(Candidate))).scalars().one()
        assert candidate.visa_status == "UK Student visa"


class TestConsistency:
    def test_mismatch_is_reported_not_merged(self, profile_md, tmp_path):
        """A source disagreement must surface. Picking a winner silently is the failure mode."""
        tex = tmp_path / "master.tex"
        tex.write_text(
            r"\entry{Example University}{— BSc Finance}{Sep 2025 -- Jun 2028}"
            "\n"
            r"\entry{Jane Street}{— Intern}{2027}",
            encoding="utf-8",
        )
        parsed = parse_profile_markdown(profile_md)
        issues = cross_check_master_cv(parsed, tex)

        joined = " ".join(issues)
        assert "janestreet" in joined, "CV-only entry must be reported"
        assert "grantthorntonpakistan" in joined, "markdown-only entry must be reported"
        # and the parse itself is untouched by the comparison
        assert any(e["organisation"] == "Grant Thornton Pakistan" for e in parsed.experiences)

    def test_missing_cv_is_reported_not_crashed(self, profile_md, tmp_path):
        issues = cross_check_master_cv(
            parse_profile_markdown(profile_md), tmp_path / "nope.tex"
        )
        assert issues and "not found" in issues[0]
