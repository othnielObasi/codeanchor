"""Tests for git inspection and GPT-5.6 response schema validation.

Git tests create REAL temporary repositories and run REAL git commands. Mocking
git here would defeat the purpose: the whole point of this module is to be an
independent second source of truth about the filesystem.
"""
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracememory.adapters.git_inspect import (
    GitError,
    GitInspector,
    path_matches_protected,
    reconcile,
    session_start_ref_from_lines,
    verify_violations,
)
from tracememory.adapters.codex_rollout import RolloutLine
from tracememory.adapters.drift_scoring import (
    DriftScoreParseError,
    GPT56DriftScorer,
    parse_drift_response,
)


# --- helpers -----------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo():
    """A real git repo with one commit."""
    with tempfile.TemporaryDirectory() as d:
        _git(d, "init", "-q")
        _git(d, "config", "user.email", "t@example.com")
        _git(d, "config", "user.name", "t")
        os.makedirs(os.path.join(d, "billing", "auth"), exist_ok=True)
        for p, body in [
            ("billing/refunds.py", "def refund():\n    pass\n"),
            ("billing/auth/eligibility.py", "def eligible():\n    return True\n"),
        ]:
            with open(os.path.join(d, p), "w") as f:
                f.write(body)
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "initial")
        yield d


def _edit(repo, path, text):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a") as f:
        f.write(text)


# --- GitInspector ------------------------------------------------------------

def test_rejects_a_non_repository():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(GitError):
            GitInspector(d)


def test_clean_repo_reports_no_changes(repo):
    assert GitInspector(repo).changed_files() == []


def test_detects_a_modified_file(repo):
    _edit(repo, "billing/refunds.py", "\n# changed\n")
    changes = GitInspector(repo).changed_files()
    assert [c.path for c in changes] == ["billing/refunds.py"]
    assert changes[0].insertions >= 1


def test_detects_an_untracked_new_file_in_a_protected_directory(repo):
    """`git diff` alone misses new files. An agent CREATING a file inside a
    frozen directory is still a violation."""
    _edit(repo, "billing/auth/new_rule.py", "x = 1\n")
    paths = [c.path for c in GitInspector(repo).changed_files()]
    assert "billing/auth/new_rule.py" in paths


def test_file_changed_helper(repo):
    _edit(repo, "billing/refunds.py", "\n# x\n")
    gi = GitInspector(repo)
    assert gi.file_changed("billing/refunds.py")
    assert not gi.file_changed("billing/auth/eligibility.py")


def test_diff_for_returns_patch_text(repo):
    _edit(repo, "billing/refunds.py", "\n# marker-xyz\n")
    assert "marker-xyz" in GitInspector(repo).diff_for("billing/refunds.py")


def test_session_start_ref_includes_committed_session_changes(repo):
    _edit(repo, "billing/refunds.py", "\n# before session\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pre-existing work")
    session_start = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    _edit(repo, "billing/auth/eligibility.py", "\n# changed during session\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "session work")

    assert GitInspector(repo).changed_files() == []
    paths = [change.path for change in GitInspector(repo, session_start).changed_files()]
    assert paths == ["billing/auth/eligibility.py"]


def test_invalid_session_start_ref_falls_back_to_working_tree(repo):
    _edit(repo, "billing/refunds.py", "\n# dirty\n")
    inspector = GitInspector(repo, "not-a-real-ref")
    assert inspector.session_start_ref is None
    assert [change.path for change in inspector.changed_files()] == ["billing/refunds.py"]


def test_extracts_session_start_ref_from_both_metadata_shapes():
    legacy = [RolloutLine("t", {"type": "session_meta", "git_sha": "abc123"})]
    current = [RolloutLine("t", {"type": "session_meta", "git": {"commit_hash": "def456"}})]
    assert session_start_ref_from_lines(legacy) == "abc123"
    assert session_start_ref_from_lines(current) == "def456"


# --- reconciliation ----------------------------------------------------------

def test_claim_confirmed_by_git(repo):
    _edit(repo, "billing/auth/eligibility.py", "\n# touched\n")
    rec = reconcile(GitInspector(repo), ["billing/auth/eligibility.py"], ["billing/auth/"])
    assert rec.claimed_and_confirmed == ["billing/auth/eligibility.py"]
    assert rec.claimed_not_confirmed == []


def test_claim_not_confirmed_is_flagged_as_possible_false_accusation(repo):
    """The rollout says a file was patched but git shows it unchanged."""
    rec = reconcile(GitInspector(repo), ["billing/auth/eligibility.py"], ["billing/auth/"])
    assert rec.claimed_not_confirmed == ["billing/auth/eligibility.py"]
    assert rec.claimed_and_confirmed == []


def test_out_of_band_change_to_protected_path_is_caught(repo):
    """The silent-miss case: a protected file changed with NOTHING in the
    session log claiming it. Rollout-only detection cannot see this."""
    _edit(repo, "billing/auth/eligibility.py", "\n# changed by a shell command\n")
    rec = reconcile(GitInspector(repo), claimed_paths=[], protected_terms=["billing/auth/"])
    assert "billing/auth/eligibility.py" in rec.changed_but_unclaimed
    entry = [e for e in rec.protected_paths_changed if e["path"] == "billing/auth/eligibility.py"]
    assert entry and entry[0]["claimed_by_rollout"] is False


def test_unprotected_change_is_not_reported_as_protected(repo):
    _edit(repo, "billing/refunds.py", "\n# fine\n")
    rec = reconcile(GitInspector(repo), ["billing/refunds.py"], ["billing/auth/"])
    assert rec.protected_paths_changed == []


def test_file_path_matching_respects_component_boundaries():
    assert path_matches_protected("billing/auth.py", ["auth.py"])
    assert not path_matches_protected("billing/notauth.py", ["auth.py"])
    assert not path_matches_protected("billing/auth.py.backup", ["auth.py"])


def test_directory_path_matching_respects_component_boundaries():
    assert path_matches_protected("billing/auth/eligibility.py", ["billing/auth/"])
    assert not path_matches_protected("billing/auth-old/eligibility.py", ["billing/auth/"])


def test_git_reconciliation_ignores_substring_lookalikes(repo):
    _edit(repo, "billing/notauth.py", "x = 1\n")
    _edit(repo, "billing/auth.py.backup", "x = 1\n")
    rec = reconcile(GitInspector(repo), claimed_paths=[], protected_terms=["auth.py"])
    assert rec.protected_paths_changed == []


# --- verify_violations -------------------------------------------------------

def _violation(path):
    return {
        "constraint": "do NOT touch billing/auth/",
        "constraints": ["do NOT touch billing/auth/"],
        "violating_tool": "apply_patch",
        "violating_input": {"path": path},
        "timestamp": "t",
    }


def test_violation_confirmed_by_git_is_marked_rollout_plus_git(repo):
    _edit(repo, "billing/auth/eligibility.py", "\n# yes\n")
    out = verify_violations([_violation("billing/auth/eligibility.py")], GitInspector(repo), ["billing/auth/"])
    assert len(out) == 1
    assert out[0]["evidence"] == "rollout+git"
    assert out[0]["git_verified"] is True


def test_violation_git_cannot_confirm_is_marked_rollout_only(repo):
    out = verify_violations([_violation("billing/auth/eligibility.py")], GitInspector(repo), ["billing/auth/"])
    assert out[0]["evidence"] == "rollout-only"
    assert out[0]["git_verified"] is False


def test_git_only_violation_is_added_when_rollout_never_reported_it(repo):
    _edit(repo, "billing/auth/eligibility.py", "\n# out of band\n")
    out = verify_violations([], GitInspector(repo), ["billing/auth/"])
    assert len(out) == 1
    assert out[0]["evidence"] == "git-only"
    assert out[0]["violating_tool"] == "(not recorded in session log)"


def test_without_an_inspector_evidence_is_unknown_not_false():
    """No repo available must not silently downgrade a real violation."""
    out = verify_violations([_violation("billing/auth/x.py")], None, ["billing/auth/"])
    assert out[0]["evidence"] == "rollout-only"
    assert out[0]["git_verified"] is None


# --- GPT-5.6 response schema validation --------------------------------------

def test_parses_plain_json():
    assert parse_drift_response('{"retained": false, "confidence": 90, "rationale": "r"}') == (False, 90, "r")


def test_parses_markdown_fenced_json():
    """openai/codex#15451: structured output degrades to fenced JSON when tools
    are active. This previously raised JSONDecodeError."""
    raw = '```json\n{"retained": true, "confidence": 80, "rationale": "ok"}\n```'
    assert parse_drift_response(raw) == (True, 80, "ok")


def test_parses_json_embedded_in_prose():
    raw = 'Here is my assessment:\n{"retained": false, "confidence": 70, "rationale": "dropped"}\nHope that helps.'
    assert parse_drift_response(raw)[0] is False


def test_accepts_string_booleans():
    assert parse_drift_response('{"retained": "false", "confidence": 60}')[0] is False
    assert parse_drift_response('{"retained": "yes", "confidence": 60}')[0] is True


def test_clamps_out_of_range_confidence():
    assert parse_drift_response('{"retained": true, "confidence": 150}')[1] == 100
    assert parse_drift_response('{"retained": true, "confidence": -20}')[1] == 0


def test_missing_retained_field_is_rejected():
    """Previously raised a bare KeyError."""
    with pytest.raises(DriftScoreParseError):
        parse_drift_response('{"confidence": 90}')


def test_empty_and_garbage_responses_are_rejected():
    for bad in ["", "   ", "I'm not sure about that.", "null", "[1,2,3]"]:
        with pytest.raises(DriftScoreParseError):
            parse_drift_response(bad)


def test_non_boolean_retained_is_rejected():
    with pytest.raises(DriftScoreParseError):
        parse_drift_response('{"retained": "maybe"}')


# --- scorer resilience -------------------------------------------------------

class _Client:
    def __init__(self, response=None, exc=None):
        self.response, self.exc = response, exc

    def create_response(self, model, input):
        if self.exc:
            raise self.exc
        return self.response


def test_scorer_survives_malformed_response_and_flags_for_review():
    s = GPT56DriftScorer(_Client(response="not json at all"))
    r = s.score("do not touch auth.py", "summary")
    assert r.retained is False
    assert r.freshness_score < 35, "unverifiable must surface, never pass"
    assert "could not be verified" in r.rationale


def test_scorer_survives_api_failure_and_flags_for_review():
    s = GPT56DriftScorer(_Client(exc=ConnectionError("network down")))
    r = s.score("do not touch auth.py", "summary")
    assert r.retained is False
    assert r.freshness_score < 35
    assert "could not be verified" in r.rationale


def test_scorer_handles_fenced_response_end_to_end():
    s = GPT56DriftScorer(_Client(response='```json\n{"retained": false, "confidence": 95, "rationale": "dropped"}\n```'))
    r = s.score("do not touch billing/auth/", "summary")
    assert r.retained is False
    assert r.rationale == "dropped"
    assert r.freshness_score < 35
