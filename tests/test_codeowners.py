"""CODEOWNERS-derived protected-path tests."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters.codeowners import (
    constraints_from_codeowners,
    protected_paths_from_codeowners,
)


def test_reads_exact_and_directory_paths_from_codeowners(tmp_path):
    folder = tmp_path / ".github"
    folder.mkdir()
    (folder / "CODEOWNERS").write_text(
        "# compliance boundaries\n"
        "/billing/auth/** @security\n"
        "/config/production.yml @platform @security\n",
        encoding="utf-8",
    )
    assert protected_paths_from_codeowners(str(tmp_path)) == [
        "billing/auth/", "config/production.yml",
    ]


def test_uses_github_lookup_precedence(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("/preferred/ @a\n", encoding="utf-8")
    (tmp_path / "CODEOWNERS").write_text("/ignored/ @b\n", encoding="utf-8")
    assert protected_paths_from_codeowners(str(tmp_path)) == ["preferred/"]


def test_ignores_comments_invalid_rows_and_ambiguous_globs(tmp_path):
    (tmp_path / "CODEOWNERS").write_text(
        "# comment\n"
        "*.py @python-team\n"
        "docs/*.md @docs\n"
        "ownerless/path\n"
        "/safe/* @owners # trailing comment\n",
        encoding="utf-8",
    )
    assert protected_paths_from_codeowners(str(tmp_path)) == ["safe/"]


def test_missing_codeowners_returns_empty_fallback(tmp_path):
    assert protected_paths_from_codeowners(str(tmp_path)) == []
    assert constraints_from_codeowners(str(tmp_path)) == []


def test_constraints_are_explicit_and_inspectable(tmp_path):
    (tmp_path / "CODEOWNERS").write_text("/billing/auth/ @security\n", encoding="utf-8")
    assert constraints_from_codeowners(str(tmp_path)) == [
        "Do not modify billing/auth/ without owner review (protected by CODEOWNERS)."
    ]
