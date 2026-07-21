"""Subprocess tests for the advisory Codex Stop hook and installer."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "bin" / "codeanchor-hook"
INSTALLER = ROOT / "scripts" / "install_codex_hook.py"
FIXTURE = ROOT / "fixtures" / "sample_rollout.jsonl"
SAMPLE_REPO = ROOT / "sample-app"


def _run_hook(payload: str, timeout: float = 5):
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, text=True,
        capture_output=True, timeout=timeout, cwd=ROOT,
    )


def test_hook_always_exits_zero_on_empty_input():
    proc = _run_hook("")
    assert proc.returncode == 0
    response = json.loads(proc.stdout)
    assert response["continue"] is True
    assert "verification unavailable" in response["systemMessage"]


def test_hook_reports_violation_with_evidence_sources():
    proc = _run_hook(json.dumps({"transcript_path": str(FIXTURE), "cwd": str(SAMPLE_REPO)}))
    assert proc.returncode == 0
    response = json.loads(proc.stdout)
    assert response["continue"] is True
    assert "NOT honoured" in response["systemMessage"]
    assert "billing/auth/eligibility.py" in response["systemMessage"]
    # sample-app may not have been initialized, in which case rollout evidence
    # is still reported accurately and the hook remains useful.
    assert "[log+git]" in response["systemMessage"] or "[log-only]" in response["systemMessage"]


def test_hook_never_blocks_on_malformed_input():
    proc = _run_hook("not-json", timeout=2)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["continue"] is True


def _run_installer(home: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--codex-home", str(home), *args],
        text=True, capture_output=True, timeout=5, cwd=ROOT,
    )


def test_installer_is_idempotent_and_preserves_existing(tmp_path):
    path = tmp_path / "hooks.json"
    existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-hook"}]}]}}
    path.write_text(json.dumps(existing), encoding="utf-8")
    assert _run_installer(tmp_path).returncode == 0
    first = path.read_text(encoding="utf-8")
    assert _run_installer(tmp_path).returncode == 0
    assert path.read_text(encoding="utf-8") == first
    assert "other-hook" in first
    assert first.count("codeanchor-hook") == 2  # command + commandWindows


def test_uninstall_removes_only_codeanchor_hook(tmp_path):
    assert _run_installer(tmp_path).returncode == 0
    config = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))
    config["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "keep-me"}]})
    (tmp_path / "hooks.json").write_text(json.dumps(config), encoding="utf-8")
    assert _run_installer(tmp_path, "--uninstall").returncode == 0
    result = (tmp_path / "hooks.json").read_text(encoding="utf-8")
    assert "codeanchor-hook" not in result
    assert "keep-me" in result


def test_dry_run_does_not_write(tmp_path):
    proc = _run_installer(tmp_path, "--dry-run")
    assert proc.returncode == 0
    assert "codeanchor-hook" in proc.stdout
    assert not (tmp_path / "hooks.json").exists()


def test_installer_refuses_to_clobber_invalid_json(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text("not json", encoding="utf-8")
    proc = _run_installer(tmp_path)
    assert proc.returncode == 1
    assert path.read_text(encoding="utf-8") == "not json"
