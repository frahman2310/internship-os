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


def test_corpus_never_whitelists_em_dash_from_ai_assisted_sample():
    """The formal essay declares Claude editorial assistance and runs 12.6 em dashes per
    1k words; the unassisted creative sample has zero. Treating the formal sample as
    evidence of his voice would license the loudest AI tell using AI-written text."""
    from internship_os.writing.corpus import NEVER_OVERRIDE, author_habits

    habits = author_habits()
    assert not any("em-dash" in h.lower() or "em dash" in h.lower() for h in habits)
    for loud in ("delve", "tapestry", "leverage", "robust", "seamless"):
        assert loud in NEVER_OVERRIDE
        assert not any(loud == h.lower().strip() for h in habits)


def test_voice_respects_his_own_habits_but_not_loud_tells():
    from internship_os.writing.voice import report

    his = "I actually just really wanted to explain what happened."
    assert report(his)["strong"] == [] and report(his)["medium"] == []
    assert len(report(his, respect_habits=False)["medium"]) > 0
    loud = "We delve into a rich tapestry of seamless synergies."
    assert len(report(loud)["strong"]) >= 2


def test_marketplace_copy_is_tier_five():
    from internship_os.research.sources.fetch import assign_tier

    assert assign_tier("https://www.fiverr.com/x")[0] == 5
    assert assign_tier("https://www.upwork.com/y")[0] == 5
    assert assign_tier("https://www.reddit.com/r/z")[0] == 4


def test_hard_requirements_are_detected_when_the_model_calls_them_soft():
    """The model marked "Candidates are required to be pursuing a degree completing
    between June 2027 and July 2028" as soft. A missed hard gate lets an ineligible
    application consume a slot, so wording is checked deterministically as a backstop."""
    from internship_os.research.application_intel import (
        Requirement, Requirements, check_gates, harden,
    )

    reqs = harden(Requirements(requirements=[
        Requirement(text="Candidates are required to be pursuing a degree with a completion "
                         "time frame between June 2027 and July 2028", kind="eligibility", hard=False),
        Requirement(text="On track for a minimum 2:1 degree classification", kind="eligibility", hard=False),
        Requirement(text="EMEA applicants are limited to 1 application per recruitment season",
                    kind="eligibility", hard=False),
        Requirement(text="Desired: A second European language.", kind="competency", hard=False),
    ]))
    assert [r.hard for r in reqs.requirements] == [True, True, True, False]

    gates = {g.verdict for g in check_gates(reqs, graduation_year=2028)}
    assert "fail" not in gates
    # Graduating 2028 sits inside the stated 2027-2028 window.
    assert any(g.verdict == "pass" and "2028" in g.reason
               for g in check_gates(reqs, graduation_year=2028))
    # Graduating 2029 does not.
    assert any(g.verdict != "pass"
               for g in check_gates(reqs, graduation_year=2029) if "completion" in g.requirement)
    # The application cap is surfaced as a strategic constraint, not a pass.
    assert any("ONE-SHOT" in g.reason for g in check_gates(reqs, graduation_year=2028))


def test_excluded_personal_topics_never_enter_the_evidence_store():
    """the user excluded bereavement and political ambition. Enforced as a check rather than
    a note, because the failure mode is a stale row surfacing in a drafted answer months
    later, long after anyone remembers the instruction."""
    import asyncio

    from sqlalchemy import select

    from internship_os.db import session_scope
    from internship_os.models.candidate import EvidenceItem

    FORBIDDEN = ("died", "death", "passed away", "bereave", "politician",
                 "public service", "public servant", "bullied", "bullying")

    async def check():
        async with session_scope() as s:
            rows = (await s.execute(select(EvidenceItem.text))).scalars().all()
            return [t for t in rows if any(w in t.lower() for w in FORBIDDEN)]

    assert asyncio.run(check()) == []


def test_unrestricted_right_to_work_wording_fails_the_visa_gate():
    """SMBC's "Unrestricted and unlimited right to work in the UK" excludes a Student visa
    holder from the internship itself, not merely the graduate role. The earlier matcher
    only caught explicit "we do not sponsor" phrasing and returned unknown here, which
    would have sent the user into an application he cannot win."""
    from internship_os.research.application_intel import (
        Requirement, Requirements, check_gates, harden,
    )

    reqs = harden(Requirements(requirements=[
        Requirement(text="Unrestricted and unlimited right to work in the UK",
                    kind="eligibility", hard=True),
    ]))
    assert [g.verdict for g in check_gates(reqs, needs_sponsorship=True)] == ["fail"]
    assert [g.verdict for g in check_gates(reqs, needs_sponsorship=False)] == ["pass"]


def test_namespaced_topics_keep_their_half_life():
    """A research run covering several firms namespaces topics ("rothschild:eligibility").
    An exact dict lookup dropped every one of those to the 18-month default, which would
    have left quarterly market data reading as fresh for a year and a half."""
    from internship_os.research.confidence import half_life

    assert half_life("blackrock:recent_news") == half_life("recent_news") == 6
    assert half_life("rothschild:eligibility") == 12
    # Market figures are the shortest-lived: they are what he would assert out loud.
    assert half_life("market_backdrop") == 3
    assert half_life("something_unmapped") == 18


def test_learning_loop_learns_style_words_not_grammar():
    """the user's edits are the training signal. The loop must learn that he deletes
    "passionate" while ignoring "about", which merely rides along in every deleted clause."""
    import os

    from internship_os.writing import feedback

    if feedback.LEARNED_PATH.exists():
        os.remove(feedback.LEARNED_PATH)
    try:
        pairs = [
            ("I am very passionate about finance and would really like to add value.",
             "I want to work on live financing decisions."),
            ("I am passionate about markets and would really like to contribute.",
             "I want to build models that inform recommendations."),
            ("I am passionate about this role and really believe I can add value.",
             "I want to learn the work properly."),
        ]
        for before, after in pairs:
            feedback.record(before, after)
        learned = feedback.load()
        assert learned.edits_seen == 3
        assert learned.dislikes.get("passionate", 0) >= 3
        assert learned.dislikes.get("really", 0) >= 3
        # Grammar words must never become rules.
        assert "about" not in learned.dislikes
        assert "would" not in learned.dislikes
        rules = " ".join(learned.rules)
        assert "passionate" in rules
        # These edits average about five words shorter, below the eight-word threshold, so
        # no length rule should fire yet — a small trim is not a stated preference.
        assert "draft shorter" not in rules
        assert -8 < learned.mean_length_delta < 0
    finally:
        if feedback.LEARNED_PATH.exists():
            os.remove(feedback.LEARNED_PATH)


def test_length_rule_fires_only_on_a_sustained_trim():
    import os

    from internship_os.writing import feedback

    if feedback.LEARNED_PATH.exists():
        os.remove(feedback.LEARNED_PATH)
    try:
        long_draft = " ".join(["word"] * 60) + " passionate."
        for _ in range(3):
            feedback.record(long_draft, "Short and direct.")
        assert "draft shorter" in " ".join(feedback.load().rules)
    finally:
        if feedback.LEARNED_PATH.exists():
            os.remove(feedback.LEARNED_PATH)


def test_identical_save_is_not_recorded_as_an_edit():
    """Re-saving unchanged text would teach the loop nothing and dilute real signal."""
    from internship_os.writing.feedback import diff

    d = diff("Same text here.", "Same text here.")
    assert d.removed == [] and d.added == [] and d.length_delta == 0


def test_guidance_precedence_puts_his_words_first():
    """Order encodes precedence: what he said, then what his edits show, then house style.
    A note must appear above the register, or the drafter can be talked out of an explicit
    instruction by generic advice."""
    import os

    from internship_os.writing import feedback, notes

    for path in (notes.NOTES_PATH, feedback.LEARNED_PATH):
        if path.exists():
            os.remove(path)
    try:
        notes.add("Never end on a summary line.", scope="global")
        for _ in range(3):
            feedback.record("I am passionate about this role and want to add value here.",
                            "I built the model.")
        combined = notes.combined_guidance()
        i_note = combined.index("Never end on a summary line")
        i_learned = combined.index("passionate")
        i_register = combined.index("Write like a competent person")
        assert i_note < i_learned < i_register
    finally:
        for path in (notes.NOTES_PATH, feedback.LEARNED_PATH):
            if path.exists():
                os.remove(path)


def test_piece_scoped_note_does_not_leak_to_other_pieces():
    import os

    from internship_os.writing import notes

    if notes.NOTES_PATH.exists():
        os.remove(notes.NOTES_PATH)
    try:
        notes.add("Cut the closer here.", scope="piece", document_id=42)
        notes.add("Never use exclamation marks.", scope="global")
        assert "Cut the closer here." in notes.guidance(42)
        assert "Cut the closer here." not in notes.guidance(99)
        assert "exclamation" in notes.guidance(99)
    finally:
        if notes.NOTES_PATH.exists():
            os.remove(notes.NOTES_PATH)
