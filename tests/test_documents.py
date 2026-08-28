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


def test_standards_swap_test_and_banned_opener():
    """The swap test is the sharpest benchmark rule: a letter that never names the employer
    would suit any of them. Checked mechanically because a rule that lives only in a prompt
    is one that gets ignored under pressure."""
    from internship_os.writing.standards import check

    generic = ("I am writing to apply for the Summer Analyst position. I would bring a "
               "well-rounded skill set and thrive in a fast-paced environment. I look "
               "forward to your response.")
    rules = {f.rule for f in check(generic, company="Rothschild & Co")}
    assert {"opener", "swap_test", "close", "generic_motive"} <= rules

    grounded = ("Rothschild & Co places summer analysts into either M&A or Debt & "
                "Restructuring. Last April, on a live buy-and-build, senior leverage was "
                "sized at 6x EBITDA. Rothschild & Co's independence is why the advice is "
                "the product.")
    assert [f for f in check(grounded, company="Rothschild & Co") if f.severity == "fail"] == []


def test_benchmark_never_outranks_his_own_instruction():
    """Standards raise the floor; they must not overrule the person whose name is on the
    letter, so they sit last in the assembled guidance."""
    import os

    from internship_os.writing import notes

    if notes.NOTES_PATH.exists():
        os.remove(notes.NOTES_PATH)
    try:
        notes.add("Ignore the 300-word guidance, I want the long version.", scope="global")
        combined = notes.combined_guidance()
        assert combined.index("Ignore the 300-word guidance") < combined.index("swap test")
    finally:
        if notes.NOTES_PATH.exists():
            os.remove(notes.NOTES_PATH)


def test_swap_test_does_not_fire_on_competency_answers():
    """A "describe a time you worked in a team" answer is about the candidate, not the
    employer; naming the firm inside it reads as forced. Applying the swap test everywhere
    produced four false failures on the Bank of America answers."""
    from internship_os.writing.answers import classify
    from internship_os.writing.standards import check

    generic = "I coordinated a team of 100 people and delivered the event."
    for question in ("Describe a time you worked with others to achieve something.",
                     "What are your strengths?", "Describe a significant challenge."):
        rules = {f.rule for f in check(generic, company="Bank of America",
                                       competency=classify(question))}
        assert "swap_test" not in rules, question
    # It must still fire where it belongs.
    rules = {f.rule for f in check(generic, company="Bank of America",
                                   competency=classify("Why Bank of America?"))}
    assert "swap_test" in rules


def test_letter_mode_follows_morgan_stanleys_close():
    """Morgan Stanley's guide requires thanking the reader and saying you look forward to a
    response; the general benchmark calls that close weak. The employer's own instruction
    governs a letter addressed to that employer, so the rule must not fire in letter mode."""
    from internship_os.writing.standards import check

    letter = ("Dear Sir/Madam, I am applying to Rothschild & Co. Rothschild & Co places "
              "analysts into M&A or Debt & Restructuring, and I saw leverage at 6x EBITDA. "
              "Thank you for considering my application; I look forward to hearing from you. "
              "Yours faithfully, the user")
    assert [f.rule for f in check(letter, company="Rothschild & Co", letter=True)] == []
    assert "close" in {f.rule for f in check(letter, company="Rothschild & Co", letter=False)}


def test_a_single_edit_now_produces_a_rule():
    import os

    from internship_os.writing import feedback

    if feedback.LEARNED_PATH.exists():
        os.remove(feedback.LEARNED_PATH)
    try:
        feedback.record("I am passionate about this role.", "I built the model.")
        assert any("passionate" in r for r in feedback.load().rules)
    finally:
        if feedback.LEARNED_PATH.exists():
            os.remove(feedback.LEARNED_PATH)


def test_user_standing_rules_are_enforced_mechanically():
    """His rules came as corrections on real drafts, so they live in code rather than only
    in a notes file that could be cleared. Each check below caused a real failure in the
    Bank of America answers before it existed."""
    from internship_os.writing.standards import check

    dash = "He built the model — quickly, and well, across several long working weeks."
    assert "dashes" in {f.rule for f in check(dash, competency="strength")}

    meta = "Three, in the order I would defend them, are analysis, initiative and delivery."
    assert "meta_structure" in {f.rule for f in check(meta, competency="strength")}

    hedge = "Leveraged Finance, though I know placement follows selection and I would take any."
    assert "hedging" in {f.rule for f in check(hedge, competency="strength")}

    heading = "**Analytical work with a decision attached.** I have built valuation models."
    assert "fragmented" in {f.rule for f in check(heading, competency="strength")}


def test_flow_check_catches_both_choppy_and_overlong():
    """The complaint was choppiness, and over-correcting it is the obvious failure: the
    rewritten answers first came back at a 40-word mean, which is unreadable."""
    from internship_os.writing.standards import check

    choppy = " ".join(["He built it. It worked. She agreed. They shipped. It sold. He left."] * 2)
    assert "flow" in {f.rule for f in check(choppy, competency="strength")}

    overlong = (" ".join(["word"] * 45) + ". ") * 6
    assert "flow" in {f.rule for f in check(overlong, competency="strength")}


def test_reframe_entity_guard_ignores_sentence_starts():
    """A capital at the start of a sentence is grammar, not a name. An early version
    flagged "Executing" as an invented entity and blocked an otherwise clean reframe."""
    from internship_os.writing.reframe import named_entities

    before = "I built models at Grant Thornton. It worked."
    gerund = "I built models at Grant Thornton. Executing them taught me a lot."
    assert named_entities(gerund) - named_entities(before) == set()

    # A firm appearing from nowhere is still caught.
    invented = "I built models at Grant Thornton and Goldman Sachs."
    assert {"Goldman", "Sachs"} <= named_entities(invented) - named_entities(before)


def test_reframe_result_is_a_proposal_not_a_save():
    """Nothing is written until the user accepts: the result object carries the original so a
    discard restores it, and exposes its own safety verdict."""
    from internship_os.writing.reframe import ReframeResult

    r = ReframeResult(original="before", text="after", invented_numbers=["25%"])
    assert not r.safe and r.original == "before"
    assert ReframeResult(original="a", text="b").safe


def test_reframe_allows_the_employers_own_name():
    """Naming Bank of America inside a Bank of America answer is adding context, not
    inventing a fact, and the swap test elsewhere actively wants it there."""
    import re

    from internship_os.writing.reframe import named_entities

    before = "I built models at Grant Thornton."
    after = "At Bank of America I would build models, as I did at Grant Thornton."
    allowed = {w for w in re.findall(r"[A-Za-z&'.\-]{2,}", "Bank of America")}
    assert named_entities(after) - named_entities(before) - allowed == set()


def test_uncited_but_factual_paragraphs_are_recovered_not_discarded():
    """The drafter sometimes writes a properly sourced paragraph and fails to return the
    citation ids. Treating that as fabrication threw away two good Ares and Capula answers
    over a bookkeeping error, so the substance is verified instead: every number must exist
    in the evidence pool. It is recorded as reframed, never grounded."""
    from internship_os.documents.cover import Paragraph, audit

    evidence = {
        1: "oversaw an $8,000 operating budget and delivered $1,000 net profit",
        2: "coordinating 100+ frontline staff across logistics",
        3: "automated ~65% of the LP report-generation process",
    }
    sourced = Paragraph(text="I ran an $8,000 budget to $1,000 profit, directed 100 staff, "
                             "and automated 65% of a reporting cycle.")
    assert audit(sourced, evidence, {}).grounding == "reframed"

    # An invented figure can never be recovered this way.
    invented = Paragraph(text="I managed a $50,000 budget and grew revenue 300%.")
    assert audit(invented, evidence, {}).grounding == "ungrounded"

    # Nor can a paragraph with nothing checkable in it.
    vague = Paragraph(text="I am a diligent and analytical person.")
    assert audit(vague, evidence, {}).grounding == "ungrounded"


def test_swap_test_matches_the_distinctive_word_not_the_legal_name():
    """Three correctly-written answers were flagged generic because the checker looked for
    "Qatalyst Partners" when the prose said "Qatalyst". Suffixes like Partners, Capital and
    Sachs are shared across firms and carry no identifying force."""
    from internship_os.writing.standards import check

    named = ("Qatalyst advises technology companies exclusively. Qatalyst is where I want "
             "to learn this, having built 3 products.")
    assert [f.rule for f in check(named, company="Qatalyst Partners", competency="why_firm")
            if f.severity == "fail"] == []

    generic = "A letter naming nobody at all, with 5 things in it."
    assert "swap_test" in {f.rule for f in check(generic, company="Qatalyst Partners",
                                                 competency="why_firm")}


def test_commercial_awareness_answers_need_not_name_the_firm():
    """"Name a news item that drove the markets" is about the world, not the employer.
    Forcing the firm's name in produces the contrived sentence the swap test exists to stop."""
    from internship_os.writing.answers import classify
    from internship_os.writing.standards import check

    answer = "An inflation print that surprises against consensus shifts the expected path."
    competency = classify("Please name a news item that has recently driven the financial markets?")
    assert "swap_test" not in {f.rule for f in check(answer, company="IMC Trading",
                                                     competency=competency)}
