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
