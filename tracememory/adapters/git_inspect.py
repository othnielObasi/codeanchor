"""Git inspection — verify what a Codex session CLAIMED against what the
repository actually shows.

Why this exists
---------------
Until now, violation detection trusted the rollout's own account: it read
`input.path` from `apply_patch` tool calls and assumed that was the truth about
what changed. That is a single-source claim from the same system whose
reliability is in question. Two failure modes it cannot see:

  1. **Claimed but not applied.** A patch is recorded in the rollout but failed,
     was reverted, or wrote somewhere else. The adapter reports a violation that
     never happened -- a false accusation.

  2. **Applied but not claimed.** A file changed via a shell command, a script,
     a build step, or a tool call that didn't surface as `apply_patch`. A
     protected path is modified and the rollout-only check sees nothing --
     a silent miss, which is the worse of the two.

Git is the independent second source. When a constraint says "do not touch
billing/auth/" and `git` shows billing/auth/ modified, that is evidence that
does not depend on the agent's self-report.

All git calls are read-only (`diff`, `status`, `log`, `rev-parse`). Nothing here
writes to the repository.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any


class GitError(RuntimeError):
    pass


@dataclass
class FileChange:
    path: str
    status: str          # M, A, D, R, ?? (untracked)
    insertions: int = 0
    deletions: int = 0

    @property
    def is_deletion(self) -> bool:
        return self.status == "D"


@dataclass
class Reconciliation:
    """Rollout claims vs. actual git state."""
    claimed_and_confirmed: list[str] = field(default_factory=list)
    claimed_not_confirmed: list[str] = field(default_factory=list)   # possible false accusation
    changed_but_unclaimed: list[str] = field(default_factory=list)   # silent, out-of-band change
    protected_paths_changed: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claimed_and_confirmed": self.claimed_and_confirmed,
            "claimed_not_confirmed": self.claimed_not_confirmed,
            "changed_but_unclaimed": self.changed_but_unclaimed,
            "protected_paths_changed": self.protected_paths_changed,
        }


class GitInspector:
    """Read-only inspection of a git working tree."""

    def __init__(self, repo_path: str, session_start_ref: str | None = None):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            raise GitError(f"not a git repository: {self.repo_path}")
        self.session_start_ref: str | None = None
        self.set_session_start_ref(session_start_ref)

    # -- plumbing ----------------------------------------------------------

    def _run(self, *args: str) -> str:
        # Scope a safe.directory exemption to our own git calls. Extracted or
        # copied repos (i.e. anything a judge downloads) otherwise trip git's
        # ownership check. This does NOT modify the user's git config.
        env = dict(os.environ)
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        })
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise GitError("git executable not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {' '.join(args)} timed out") from exc
        if proc.returncode != 0:
            err = proc.stderr.strip()
            if "dubious ownership" in err:
                # Extracted/copied repos trip git's safe.directory check. Surface
                # an actionable message instead of a raw git error.
                raise GitError(
                    f"git refuses to read {self.repo_path} (ownership check). "
                    f"Run: git config --global --add safe.directory {self.repo_path} "
                    f"— or regenerate it with scripts/setup_sample_app.sh"
                )
            raise GitError(f"git {' '.join(args)} failed: {err}")
        return proc.stdout

    def is_available(self) -> bool:
        try:
            self._run("rev-parse", "--git-dir")
            return True
        except GitError:
            return False

    def set_session_start_ref(self, ref: str | None) -> None:
        """Use a valid session-start commit as the default comparison base."""
        self.session_start_ref = None
        if not ref:
            return
        try:
            self._run("rev-parse", "--verify", f"{ref}^{{commit}}")
        except GitError:
            return
        self.session_start_ref = ref

    def head_sha(self) -> str:
        try:
            return self._run("rev-parse", "HEAD").strip()
        except GitError:
            return ""  # repo with no commits yet

    # -- change detection --------------------------------------------------

    def changed_files(self, since_ref: str | None = None, include_untracked: bool = True) -> list[FileChange]:
        """Files changed since `since_ref`, or in the working tree if None.

        Includes untracked files by default -- an agent creating a NEW file
        inside a protected directory is a violation, and `git diff` alone
        would not report it.
        """
        changes: dict[str, FileChange] = {}

        effective_ref = since_ref if since_ref is not None else self.session_start_ref
        diff_args = ["diff", "--numstat"]
        if effective_ref:
            diff_args.append(effective_ref)
        for line in self._run(*diff_args).splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ins, dels, path = parts[0], parts[1], parts[2]
            changes[path] = FileChange(
                path=path,
                status="M",
                insertions=0 if ins == "-" else int(ins),
                deletions=0 if dels == "-" else int(dels),
            )

        # Staged changes too.
        for line in self._run("diff", "--numstat", "--cached").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ins, dels, path = parts[0], parts[1], parts[2]
            if path not in changes:
                changes[path] = FileChange(
                    path=path, status="M",
                    insertions=0 if ins == "-" else int(ins),
                    deletions=0 if dels == "-" else int(dels),
                )

        # Status gives us adds/deletes/renames and untracked files.
        for line in self._run("status", "--porcelain").splitlines():
            if len(line) < 4:
                continue
            code, path = line[:2].strip(), line[3:].strip()
            if " -> " in path:  # rename
                path = path.split(" -> ", 1)[1]
            if code == "??":
                if not include_untracked:
                    continue
                changes.setdefault(path, FileChange(path=path, status="??"))
            elif code:
                existing = changes.get(path)
                status = code[0] if code[0] in "MADR" else "M"
                if existing:
                    existing.status = status
                else:
                    changes[path] = FileChange(path=path, status=status)

        return sorted(changes.values(), key=lambda c: c.path)

    def file_changed(self, path: str, since_ref: str | None = None) -> bool:
        return any(c.path == path for c in self.changed_files(since_ref))

    def diff_for(self, path: str, since_ref: str | None = None, max_chars: int = 4000) -> str:
        args = ["diff"]
        effective_ref = since_ref if since_ref is not None else self.session_start_ref
        if effective_ref:
            args.append(effective_ref)
        args += ["--", path]
        try:
            out = self._run(*args)
        except GitError:
            return ""
        return out[:max_chars]


def session_start_ref_from_lines(lines: list[Any]) -> str | None:
    """Read the starting commit from legacy or current normalized session metadata."""
    for line in lines:
        item = getattr(line, "item", {})
        if not isinstance(item, dict) or item.get("type") != "session_meta":
            continue
        direct = item.get("git_sha")
        git_meta = item.get("git") or {}
        nested = git_meta.get("commit_hash") if isinstance(git_meta, dict) else None
        ref = direct or nested
        return str(ref) if ref else None
    return None


# --- reconciliation ---------------------------------------------------------

def _matches_protected(path: str, protected_terms: list[str]) -> bool:
    low = path.lower()
    return any(term and term in low for term in protected_terms)


def reconcile(
    inspector: GitInspector,
    claimed_paths: list[str],
    protected_terms: list[str],
    since_ref: str | None = None,
) -> Reconciliation:
    """Cross-check rollout claims against actual git state.

    `claimed_paths` — paths the rollout says were patched after the compaction.
    `protected_terms` — protected path fragments from the task contract.
    """
    result = Reconciliation()
    actual = inspector.changed_files(since_ref)
    actual_paths = [c.path for c in actual]

    for claimed in claimed_paths:
        if not claimed:
            continue
        # A claim matches if git shows the same path, or a path ending with it
        # (rollout paths may be repo-relative in a different form).
        hit = next((p for p in actual_paths if p == claimed or p.endswith("/" + claimed) or claimed.endswith("/" + p)), None)
        (result.claimed_and_confirmed if hit else result.claimed_not_confirmed).append(claimed)

    claimed_norm = {c for c in claimed_paths if c}
    for change in actual:
        if change.path in claimed_norm:
            continue
        if any(change.path.endswith("/" + c) or c.endswith("/" + change.path) for c in claimed_norm):
            continue
        result.changed_but_unclaimed.append(change.path)

    for change in actual:
        if _matches_protected(change.path, protected_terms):
            result.protected_paths_changed.append(
                {
                    "path": change.path,
                    "status": change.status,
                    "insertions": change.insertions,
                    "deletions": change.deletions,
                    # This is the key field: git saw it, the rollout did not.
                    "claimed_by_rollout": change.path in claimed_norm
                    or any(change.path.endswith("/" + c) or c.endswith("/" + change.path) for c in claimed_norm),
                }
            )

    return result


def verify_violations(
    violations: list[dict[str, Any]],
    inspector: GitInspector | None,
    protected_terms: list[str],
    since_ref: str | None = None,
) -> list[dict[str, Any]]:
    """Annotate rollout-derived violations with independent git evidence, and
    ADD any protected-path change git found that the rollout never reported.

    Each returned violation carries an `evidence` field:
      - "rollout+git"  both agree -- strongest
      - "rollout-only" claimed but git can't confirm -- treat with caution
      - "git-only"     git found it, the rollout never mentioned it -- a change
                       the agent's own log does not account for
    """
    if inspector is None or not inspector.is_available():
        for v in violations:
            v.setdefault("evidence", "rollout-only")
            v.setdefault("git_verified", None)  # unknown, not false
        return violations

    rec = reconcile(
        inspector,
        [v.get("violating_input", {}).get("path", "") for v in violations],
        protected_terms,
        since_ref,
    )
    confirmed = set(rec.claimed_and_confirmed)

    out: list[dict[str, Any]] = []
    for v in violations:
        path = v.get("violating_input", {}).get("path", "")
        v = dict(v)
        v["git_verified"] = path in confirmed
        v["evidence"] = "rollout+git" if path in confirmed else "rollout-only"
        out.append(v)

    known = {v.get("violating_input", {}).get("path", "") for v in out}
    for entry in rec.protected_paths_changed:
        if entry["path"] in known or entry["claimed_by_rollout"]:
            continue
        out.append(
            {
                "constraint": f"protected path modified: {entry['path']}",
                "constraints": [f"protected path modified: {entry['path']}"],
                "violating_tool": "(not recorded in session log)",
                "violating_input": {"path": entry["path"]},
                "timestamp": "",
                "git_verified": True,
                "evidence": "git-only",
                "git_status": entry["status"],
                "insertions": entry["insertions"],
                "deletions": entry["deletions"],
            }
        )
    return out
