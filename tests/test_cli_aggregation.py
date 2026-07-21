"""Tests for final CLI result aggregation."""
from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_cli_module():
    loader = SourceFileLoader("codeanchor_cli_for_test", str(ROOT / "bin" / "codeanchor"))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_final_aggregation_deduplicates_git_only_paths():
    cli = _load_cli_module()
    git_finding = {
        "evidence": "git-only",
        "violating_input": {"path": "billing/auth/__init__.py"},
    }
    rollout_finding = {
        "evidence": "rollout-only",
        "violating_input": {"path": "billing/auth/eligibility.py"},
    }

    result = cli.deduplicate_git_only_violations(
        [git_finding, rollout_finding, dict(git_finding), dict(rollout_finding)]
    )

    assert result == [git_finding, rollout_finding, rollout_finding]
