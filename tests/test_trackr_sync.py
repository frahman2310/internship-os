"""Trackr account write-back: templating and safety, proven without a browser.

The safety property that matters: if the captured request cannot be parameterised, `push`
must refuse rather than replay a literal request that would update the wrong programme.
"""

from __future__ import annotations

import json

import pytest

from internship_os.sync.trackr_account import (
    PROGRAMME_TOKEN,
    STATUS_MAP,
    STATUS_TOKEN,
    StatusRecipe,
    render,
    templatise,
)

KNOWN_IDS = {"vz48bwh47m", "36jiyb6sx2", "iyocl5x6ux"}


def _recipe(url: str, body) -> StatusRecipe:
    return StatusRecipe(
        method="POST",
        url=url,
        headers={"content-type": "application/json"},
        body=body,
        observed_at="2026-08-21T10:00:00Z",
    )


class TestTemplatise:
    def test_finds_programme_id_in_body(self):
        recipe = _recipe(
            "https://api.the-trackr.com/programmes/status",
            {"programmeId": "vz48bwh47m", "status": "Applied"},
        )
        templated, found = templatise(recipe, KNOWN_IDS)
        assert found["programme_id"] == "vz48bwh47m"
        assert found["status"] == "Applied"
        assert templated.body["programmeId"] == PROGRAMME_TOKEN
        assert templated.body["status"] == STATUS_TOKEN

    def test_finds_programme_id_in_url(self):
        recipe = _recipe(
            "https://api.the-trackr.com/programmes/iyocl5x6ux/status", {"status": "Interview"}
        )
        templated, found = templatise(recipe, KNOWN_IDS)
        assert found["programme_id"] == "iyocl5x6ux"
        assert PROGRAMME_TOKEN in templated.url
        assert templated.body["status"] == STATUS_TOKEN

    def test_unknown_programme_leaves_recipe_literal(self):
        """A capture we cannot parameterise must not silently look reusable."""
        recipe = _recipe(
            "https://api.the-trackr.com/programmes/status",
            {"programmeId": "someoneelses", "status": "Applied"},
        )
        templated, found = templatise(recipe, KNOWN_IDS)
        assert "programme_id" not in found
        assert PROGRAMME_TOKEN not in json.dumps(templated.body)


class TestRender:
    def test_fills_both_tokens(self):
        recipe = _recipe(
            f"https://api.the-trackr.com/programmes/{PROGRAMME_TOKEN}/status",
            {"status": STATUS_TOKEN},
        )
        url, body = render(recipe, "36jiyb6sx2", "Applied")
        assert url.endswith("/programmes/36jiyb6sx2/status")
        assert body == {"status": "Applied"}

    def test_refuses_an_unparameterised_template(self):
        recipe = _recipe(
            "https://api.the-trackr.com/programmes/vz48bwh47m/status", {"status": "Applied"}
        )
        with pytest.raises(ValueError, match="no programme placeholder"):
            render(recipe, "36jiyb6sx2", "Applied")


class TestStatusMapping:
    def test_every_local_state_maps(self):
        from internship_os.models.base import OPPORTUNITY_STATES

        missing = set(OPPORTUNITY_STATES) - set(STATUS_MAP)
        assert not missing, f"states with no Trackr equivalent: {missing}"

    def test_only_submitted_counts_as_applied(self):
        """Drafting something is not applying for it — the remote board must not overstate."""
        assert STATUS_MAP["SUBMITTED"] == "Applied"
        # Pre-submission states may read as "Interested", never as "Applied".
        for state in ("DRAFTING", "AWAITING_REVIEW", "APPROVED", "APPLYING"):
            assert STATUS_MAP[state] in ("Not Applied", "Interested"), state
            assert STATUS_MAP[state] != "Applied", state

    def test_terminal_outcomes_map_honestly(self):
        assert STATUS_MAP["OFFER"] == "Offer"
        assert STATUS_MAP["REJECTED"] == "Rejected"
        assert STATUS_MAP["INTERVIEW"] == "Interview"


class TestRecipePersistence:
    def test_round_trips(self, tmp_path, monkeypatch):
        from internship_os.config import settings

        monkeypatch.setattr(settings, "var_dir", tmp_path)
        recipe = _recipe("https://api.the-trackr.com/x", {"a": 1})
        recipe.save()
        loaded = StatusRecipe.load()
        assert loaded is not None
        assert loaded.url == recipe.url and loaded.body == {"a": 1}


class TestBakedInStatusGuard:
    """A capture made before a label existed in STATUS_MAP leaves that label as a literal.
    Replaying it would write the captured status onto every programme, silently."""

    def _recipe(self, body):
        from internship_os.sync.trackr_account import StatusRecipe
        return StatusRecipe(
            method="PUT",
            url="https://api.the-trackr.com/programmes/update-status",
            headers={},
            body=body,
            observed_at="2026-08-28T00:00:00Z",
            note="test",
        )

    def test_refuses_to_rewrite_a_baked_in_status(self):
        import pytest
        from internship_os.sync.trackr_account import render
        r = self._recipe({"programmeId": "{{PROGRAMME_ID}}", "status": "Interested"})
        with pytest.raises(ValueError, match="baked in"):
            render(r, "abc123", "Applied")

    def test_allows_the_matching_status(self):
        from internship_os.sync.trackr_account import render
        r = self._recipe({"programmeId": "{{PROGRAMME_ID}}", "status": "Interested"})
        _, body = render(r, "abc123", "Interested")
        assert body == {"programmeId": "abc123", "status": "Interested"}

    def test_a_tokenised_recipe_renders_any_status(self):
        from internship_os.sync.trackr_account import render
        r = self._recipe({"programmeId": "{{PROGRAMME_ID}}", "status": "{{STATUS}}"})
        _, body = render(r, "abc123", "Applied")
        assert body == {"programmeId": "abc123", "status": "Applied"}
