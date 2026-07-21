"""Derive protected repository paths from CODEOWNERS without external dependencies."""
from __future__ import annotations

import os
import shlex


CODEOWNERS_LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def _normalise_owned_path(pattern: str) -> str | None:
    """Return a concrete file/directory term for deterministic verification.

    Exact paths and whole-directory wildcards are safe to convert. Patterns
    such as ``*.py`` or ``docs/*.md`` are intentionally skipped because turning
    them into a directory prohibition would overstate what CODEOWNERS says.
    """
    pattern = pattern.strip().replace("\\", "/").lstrip("/")
    if not pattern or pattern.startswith("!"):
        return None
    if pattern.endswith("/**"):
        pattern = pattern[:-3].rstrip("/") + "/"
    elif pattern.endswith("/*") and not any(char in pattern[:-2] for char in "*?["):
        pattern = pattern[:-2].rstrip("/") + "/"
    elif any(char in pattern for char in "*?["):
        return None
    return pattern or None


def protected_paths_from_codeowners(repo_path: str) -> list[str]:
    """Read the first CODEOWNERS file in GitHub lookup order."""
    for relative in CODEOWNERS_LOCATIONS:
        path = os.path.join(repo_path, *relative.split("/"))
        if not os.path.isfile(path):
            continue
        protected: list[str] = []
        with open(path, encoding="utf-8") as stream:
            for raw_line in stream:
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    fields = shlex.split(stripped, comments=True, posix=True)
                except ValueError:
                    continue
                if len(fields) < 2:  # A pattern without an owner is invalid.
                    continue
                owned = _normalise_owned_path(fields[0])
                if owned and owned not in protected:
                    protected.append(owned)
        return protected
    return []


def constraints_from_codeowners(repo_path: str) -> list[str]:
    """Create inspectable constraints compatible with the existing pipeline."""
    return [
        f"Do not modify {path} without owner review (protected by CODEOWNERS)."
        for path in protected_paths_from_codeowners(repo_path)
    ]
