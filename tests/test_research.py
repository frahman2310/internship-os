"""The research engine's guarantees, proven offline.

Two of these matter more than the rest:
  * a quote that is not literally in the page can never become an excerpt;
  * a claim about what causes hiring success cannot be scored as well-supported.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from internship_os.research.confidence import (
    ClaimSignals,
    band,
    half_life,
    score,
    stale_after,
)
from internship_os.research.orchestrator import quote_is_verbatim, recall
from internship_os.research.sources.fetch import assign_tier, html_to_text
from internship_os.models.research import ResearchClaim


def sig(**kw) -> ClaimSignals:
    base = dict(
        topic="process",
        epistemic_class="fact",
        method="direct_quote",
        statement="The process is a HireVue then an assessment centre.",
        source_tiers=[1],
        domains=["janestreet.com"],
        as_of="2026-08-01",
        disconfirming_search_ran=True,
    )
    base.update(kw)
    return ClaimSignals(**base)


class TestVerbatimGuard:
    def test_exact_quote_passes(self):
        page = "We run a HireVue, then an assessment centre in London."
        assert quote_is_verbatim("then an assessment centre", page)

    def test_whitespace_differences_are_tolerated(self):
        page = "We run  a  HireVue,\nthen an assessment centre."
        assert quote_is_verbatim("We run a HireVue, then an assessment centre.", page)

    def test_paraphrase_is_rejected(self):
        """This is the line that makes invented quotes unrepresentable rather than unlikely."""
        page = "We run a HireVue, then an assessment centre in London."
        assert not quote_is_verbatim("They conduct a video interview followed by an AC", page)

    def test_trivially_short_quote_is_rejected(self):
        assert not quote_is_verbatim("the", "the quick brown fox")


class TestConfidence:
    def test_never_certain(self):
        result = score(sig(source_tiers=[1, 1, 1], domains=["a.com", "b.com", "c.com"]))
        assert result.confidence <= 0.95

    def test_primary_source_beats_forum(self):
        primary = score(sig(source_tiers=[1], domains=["janestreet.com"]))
        forum = score(sig(source_tiers=[4], domains=["reddit.com"]))
        assert primary.confidence > forum.confidence

    def test_independent_domains_raise_confidence(self):
        one = score(sig(source_tiers=[2], domains=["ft.com"]))
        three = score(
            sig(source_tiers=[2, 2, 2], domains=["ft.com", "reuters.com", "gov.uk"])
        )
        assert three.confidence > one.confidence

    def test_one_domain_cannot_fake_independence(self):
        """Availability bias: a single prolific site is capped at 2 excerpts."""
        result = score(
            sig(source_tiers=[3, 3, 3, 3], domains=["blog.com"] * 4)
        )
        assert result.n_supporting == 2
        assert result.n_independent_domains == 1
        assert "availability_capped" in result.bias_flags

    def test_survivorship_caps_success_claims(self):
        result = score(
            sig(
                statement="Successful candidates all had a spring week beforehand.",
                source_tiers=[2, 2],
                domains=["efinancialcareers.co.uk", "targetjobs.co.uk"],
                n_rejections_observed=0,
            )
        )
        assert "survivorship" in result.bias_flags
        assert result.confidence <= 0.40, "cannot exceed weak-signal without rejections"

    def test_causal_language_is_downgraded(self):
        result = score(
            sig(
                statement="Doing a spring week is why you get in.",
                source_tiers=[3],
                domains=["forum.com"],
            )
        )
        assert result.epistemic_class == "inference"
        assert "causal_language_downgraded" in result.bias_flags

    def test_causal_claim_from_primary_source_survives(self):
        """The company stating its own mechanism is different from a forum inferring it."""
        result = score(
            sig(
                statement="Spring week attendance leads to a fast-tracked application.",
                source_tiers=[1],
                domains=["bank.com"],
            )
        )
        assert result.epistemic_class == "fact"

    def test_no_evidence_is_speculation(self):
        result = score(sig(source_tiers=[], domains=[]))
        assert result.epistemic_class == "speculation"
        assert result.confidence == 0.05

    def test_small_n_is_flagged(self):
        assert "small_n" in score(sig(source_tiers=[1], domains=["a.com"])).bias_flags

    def test_unrefuted_claims_are_discounted(self):
        refuted = score(sig(disconfirming_search_ran=True))
        unrefuted = score(sig(disconfirming_search_ran=False))
        assert unrefuted.confidence < refuted.confidence
        assert "unrefuted" in unrefuted.bias_flags

    def test_self_anchoring_capped(self):
        result = score(
            sig(
                source_tiers=[1, 1],
                domains=["myoutcome", "myoutcome2"],
                self_outcome_fraction=0.9,
            )
        )
        assert "self_anchored" in result.bias_flags
        assert result.confidence <= 0.60

    def test_speculation_cannot_be_confident(self):
        result = score(
            sig(epistemic_class="speculation", source_tiers=[1], domains=["a.com"])
        )
        assert result.confidence <= 0.35

    def test_old_evidence_decays(self):
        fresh = score(sig(as_of="2026-08-01", topic="comp"))
        old = score(sig(as_of="2024-08-01", topic="comp"))
        assert old.confidence < fresh.confidence


class TestStaleness:
    def test_volatile_topics_expire_sooner(self):
        assert half_life("comp") < half_life("culture")

    def test_stale_after_is_in_the_future(self):
        assert stale_after("process", "2026-08-01") > "2026-08-01"

    def test_bands_are_honest(self):
        assert band(0.9) == "well supported"
        assert band(0.2).startswith("speculative")


class TestTiering:
    def test_company_site_is_primary(self):
        tier, primary = assign_tier(
            "https://www.janestreet.com/careers", {"janestreet.com"}
        )
        assert (tier, primary) == (1, True)

    def test_forums_are_low_tier(self):
        assert assign_tier("https://www.reddit.com/r/FinancialCareers/x")[0] == 4
        assert assign_tier("https://www.glassdoor.co.uk/x")[0] == 4

    def test_quality_press_is_tier_two(self):
        assert assign_tier("https://www.ft.com/content/x")[0] == 2

    def test_unknown_defaults_to_specialist(self):
        assert assign_tier("https://someblog.dev/post")[0] == 3


class TestHtmlToText:
    def test_scripts_and_tags_are_stripped(self):
        html = "<html><script>var x=1;</script><p>Hello &amp; welcome</p></html>"
        text = html_to_text(html)
        assert "var x" not in text
        assert "Hello & welcome" in text


class TestRecall:
    async def test_stale_claims_are_not_recalled(self, session):
        session.add(
            ResearchClaim(
                subject_type="company",
                subject_id=1,
                topic="comp",
                statement="Pay was X last cycle.",
                epistemic_class="inference",
                confidence=0.7,
                method="synthesis",
                as_of="2020-01-01",
                stale_after="2021-01-01",
            )
        )
        await session.commit()
        assert await recall(session, "company", 1, "comp") == []

    async def test_fresh_claims_are_recalled(self, session):
        session.add(
            ResearchClaim(
                subject_type="company",
                subject_id=1,
                topic="process",
                statement="Two interviews then an AC.",
                epistemic_class="inference",
                confidence=0.7,
                method="synthesis",
                as_of="2026-08-01",
                stale_after="2099-01-01",
            )
        )
        await session.commit()
        found = await recall(session, "company", 1, "process")
        assert len(found) == 1
