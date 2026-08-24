"""Local secret storage. Keys go clipboard → file → provider, and never anywhere else.

Design rules, in order of importance:

1. **A secret is never returned in any human-facing string.** `masked()` is the only
   representation that may be printed or logged.
2. **Never passed as a command-line argument** — argv is visible to `ps` and lands in
   shell history. The CLI reads from the clipboard or a hidden TTY prompt instead.
3. **File mode 0600**, in `var/` which is gitignored.
4. **Redaction at the boundary**: `redact()` scrubs any stored secret out of error text
   before it can reach a log, a traceback, or a chat transcript.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import settings

SECRETS_FILENAME = "secrets.json"


def _path() -> Path:
    settings.var_dir.mkdir(parents=True, exist_ok=True)
    return settings.var_dir / SECRETS_FILENAME


def _load() -> dict[str, str]:
    path = _path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def _save(data: dict[str, str]) -> None:
    path = _path()
    # Create with restrictive permissions BEFORE writing, so the secret is never
    # briefly world-readable between creation and chmod.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)
    os.chmod(path, 0o600)


def set_secret(name: str, value: str) -> None:
    value = value.strip()
    if not value:
        raise ValueError("refusing to store an empty secret")
    data = _load()
    data[name] = value
    _save(data)


def get_secret(name: str) -> str | None:
    return _load().get(name) or os.environ.get(name.upper())


def delete_secret(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    del data[name]
    _save(data)
    return True


def names() -> list[str]:
    return sorted(_load())


def masked(value: str | None) -> str:
    """The ONLY safe rendering of a secret. Never print the raw value anywhere."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)"


def redact(text: str) -> str:
    """Scrub every stored secret out of arbitrary text before it is shown or logged.

    Provider errors quite often echo the key back in a URL or message; without this a
    single 400 response could paste a live credential into a transcript.
    """
    for value in _load().values():
        if value and len(value) >= 8:
            text = text.replace(value, "«redacted»")
    return text


def read_clipboard() -> str | None:
    """macOS pbpaste. Keeps the secret out of argv and out of shell history."""
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def clear_clipboard() -> None:
    """Leave nothing behind after an import."""
    try:
        subprocess.run(["pbcopy"], input="", text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
