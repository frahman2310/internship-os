"""Secret handling. These tests are the guarantee, not the docstrings.

The one that matters most is `test_provider_error_echoing_the_key_is_redacted`: providers
routinely echo a bad key back in an error, and without redaction a single 400 could paste
a live credential into a log or a chat transcript.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from internship_os import secrets

FAKE = "AIzaSyFAKEKEYFORTESTS1234567890abcdef"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    from internship_os.config import settings

    monkeypatch.setattr(settings, "var_dir", tmp_path)
    monkeypatch.delenv("GEMINI", raising=False)
    yield


class TestStorage:
    def test_round_trips(self):
        secrets.set_secret("gemini", FAKE)
        assert secrets.get_secret("gemini") == FAKE

    def test_file_is_owner_only(self):
        secrets.set_secret("gemini", FAKE)
        mode = stat.S_IMODE(os.stat(secrets._path()).st_mode)
        assert mode == 0o600, f"secrets file is {oct(mode)}, must be 0600"

    def test_empty_is_refused(self):
        with pytest.raises(ValueError):
            secrets.set_secret("gemini", "   ")

    def test_whitespace_is_stripped(self):
        secrets.set_secret("gemini", f"  {FAKE}\n")
        assert secrets.get_secret("gemini") == FAKE

    def test_delete(self):
        secrets.set_secret("gemini", FAKE)
        assert secrets.delete_secret("gemini") is True
        assert secrets.get_secret("gemini") is None


class TestNeverExposed:
    def test_masked_hides_the_middle(self):
        out = secrets.masked(FAKE)
        assert FAKE not in out
        assert out.startswith("AIza") and out.endswith(f"({len(FAKE)} chars)")

    def test_masked_handles_missing(self):
        assert secrets.masked(None) == "(not set)"

    def test_short_secrets_are_fully_masked(self):
        assert secrets.masked("abc123") == "******"

    def test_provider_error_echoing_the_key_is_redacted(self):
        """A 400 that quotes the key back must not carry it into a log."""
        secrets.set_secret("gemini", FAKE)
        leak = f"401 Unauthorized for url ...?key={FAKE} — API key not valid"
        cleaned = secrets.redact(leak)
        assert FAKE not in cleaned
        assert "«redacted»" in cleaned

    def test_redact_is_a_noop_without_secrets(self):
        assert secrets.redact("nothing to hide") == "nothing to hide"


class TestBackendRouting:
    def test_direct_provider_used_when_key_present(self):
        from internship_os.backends import resolve

        secrets.set_secret("gemini", FAKE)
        backend = resolve("gemini/gemini-2.5-flash")
        assert backend is not None
        assert backend.name == "gemini"
        assert backend.model == "gemini-2.5-flash", "prefix must be stripped for upstream"
        assert "googleapis.com" in backend.base_url
        assert backend.api_key == FAKE

    def test_missing_key_skips_rather_than_breaks(self):
        """A chain may name providers you have not configured yet."""
        from internship_os.backends import resolve

        assert resolve("gemini/gemini-2.5-flash") is None

    def test_unprefixed_models_go_to_the_gateway(self):
        from internship_os.backends import resolve

        backend = resolve("auto/best-free")
        assert backend is not None and backend.name == "omniroute"
        assert backend.model == "auto/best-free"


class TestKeyNeverInArgv:
    def test_cli_set_takes_no_key_argument(self):
        """argv is visible to `ps` and lands in shell history, so the key cannot be one."""
        import inspect

        from internship_os.cli import key_set

        params = set(inspect.signature(key_set).parameters)
        assert params == {"provider", "from_clipboard"}, params
