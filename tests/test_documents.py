"""Phase 5: deterministic CV assembly."""

from internship_os.documents.plan import is_clean_skill, score_evidence, tokenize
from internship_os.documents.render import esc


def test_latex_escaping_of_real_profile_strings():
    assert esc("$8,000") == r"\$8,000"
    assert esc("R&D") == r"R\&D"
    assert esc("100% of cases") == r"100\% of cases"


def test_skill_fragments_are_rejected():
    # halves produced by splitting a skill list on a comma inside brackets
    assert not is_clean_skill("FX)")
    assert not is_clean_skill("macro analysis (inflation")
    assert not is_clean_skill("— registered for August 2026")
    assert is_clean_skill("EV/EBITDA")
    assert is_clean_skill("Python (async)")


def test_relevance_outranks_a_bare_metric():
    """A budgeted side project must not outrank on-topic investment work on a PE CV."""

    class Item:
        def __init__(self, text, metric=None, category="achievement"):
            self.text, self.metric_value, self.category = text, metric, category

    terms = tokenize("private equity investment research valuation")
    on_topic = score_evidence(Item("Valued companies using EV/EBITDA for investment research"), set(), terms)
    metric_only = score_evidence(Item("Oversaw an $8,000 budget", metric=8000.0), set(), terms)
    assert on_topic > metric_only


def test_cover_audit_rejects_uncited_and_escalated_numbers():
    from internship_os.documents.cover import Paragraph, audit

    evidence = {7: "automated ~85% of the LP report-generation process"}
    claims: dict[int, str] = {}

    # cites nothing
    assert audit(Paragraph(text="I am a great fit."), evidence, claims).grounding == "ungrounded"
    # cites an id it was never given
    assert audit(
        Paragraph(text="I did the thing.", evidence_ids=[999]), evidence, claims
    ).grounding == "ungrounded"
    # quiet metric escalation: 85% -> 90%
    escalated = audit(
        Paragraph(text="I automated over 90% of reporting.", evidence_ids=[7]), evidence, claims
    )
    assert escalated.grounding == "ungrounded"
    assert "90" in escalated.note
    # faithful paraphrase keeping the real number
    assert audit(
        Paragraph(text="I automated ~85% of report generation.", evidence_ids=[7]),
        evidence, claims,
    ).grounding in {"grounded", "reframed"}


def test_editor_blocks_invented_metrics_but_allows_rephrasing():
    from internship_os.editor.assist import Suggestion, invented_numbers

    original = "Conducted research into a new project within the Dolmen Mall REIT mandate"
    # pure language sharpening: nothing invented
    assert invented_numbers(original, "Researched FinTech for the Dolmen Mall REIT mandate") == []
    # the failure mode this exists to stop: a metric appearing from nowhere
    assert invented_numbers(original, "Research that lifted returns by 25%") == ["25%"]
    # a number already in the source may be kept, moved, or surfaced
    sourced = "oversaw an $8,000 budget and delivered $1,000 net profit"
    assert invented_numbers(sourced, "Delivered $1,000 profit on an $8,000 budget") == []

    assert Suggestion("x", "y", []).safe
    assert not Suggestion("x", "y", ["25%"]).safe


def test_live_role_filter_requires_an_actual_opening_date():
    """Trackr's `rolling` flag is set on most rows including unopened ones; treating it
    as 'open' counted 383 UK finance roles when 49 had actually opened."""
    from internship_os.models.opportunity import Opportunity
    from internship_os.pipeline.live import is_open

    def opp(**payload):
        o = Opportunity(company_id=1, source_adapter="t", dedup_key="k", title="x")
        o.raw_payload = payload
        return o

    today = "2026-08-25"
    assert not is_open(opp(rolling=True), today)                      # rolling, never opened
    assert is_open(opp(date="2026-08-01", rolling=True), today)        # opened, rolling
    assert not is_open(opp(date="2026-12-01"), today)                  # opens in future
    assert not is_open(opp(date="2026-01-01", deadline="2026-06-01"), today)  # closed
    assert is_open(opp(date="2026-01-01", deadline="2026-09-30"), today)


def test_autofill_never_returns_legally_sensitive_fields():
    from internship_os.ats.autofill import is_reserved

    for label in ("Date of Birth", "National Insurance Number", "Passport number",
                  "Ethnic origin", "Gender identity", "Do you have a disability?",
                  "Salary expectations", "Were you eligible for free school meals?"):
        assert is_reserved(label), label
    for label in ("First name", "Email address", "Which university?", "Expected graduation"):
        assert not is_reserved(label), label


def test_voice_scanner_spares_finance_vocabulary():
    from internship_os.writing.voice import report

    assert report("Benchmarked leverage multiples in leveraged finance")["strong"] == []
    assert any("delve" in h for h in report("Let us delve into the tapestry")["strong"])
