"""Codex rollout (~/.codex/sessions/**/rollout-*.jsonl) parsing for TraceMemory.

Grounded in Codex's real on-disk schema (codex-rs/rollout, codex-rs/state/src/extract.rs):
each line is a RolloutLine{timestamp, item: RolloutItem}, where RolloutItem is one of:
  - session_meta   {id, source, cwd, model_provider, cli_version, git_sha, git_branch, git_origin_url}
  - turn_context   {model, approval_policy, sandbox_policy}
  - response_item  {role, content: [input_text | output_text | tool_call]}
  - event_msg      {msg: {type: thread_goal_updated | tool_result | token_count | user_message, ...}}
  - compacted      {summary}   <-- explicit compaction marker, not inferred

This module has ZERO dependency on a running TraceMemory API — it only produces
plain dicts shaped like TraceMemory's existing request models (RunEvent, ToolTrace,
Checkpoint state, ContextCandidate) so callers can hand them to TraceMemoryClient
or to ContextHealthService directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator


# --- Parsing -----------------------------------------------------------------

@dataclass
class RolloutLine:
    timestamp: str
    item: dict[str, Any]

    @property
    def item_type(self) -> str:
        return self.item.get("type", "unknown")


def parse_rollout_file(path: str) -> list[RolloutLine]:
    """Read a Codex rollout JSONL file into a list of RolloutLine records.

    Codex materializes compressed (.zst) rollouts back to plain .jsonl on resume,
    so this always operates on plain-text JSONL, matching Codex's own reader.
    """
    lines: list[RolloutLine] = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            lines.append(RolloutLine(timestamp=record["timestamp"], item=record["item"]))
    return lines


# --- Task contract extraction --------------------------------------------------

@dataclass
class TaskContract:
    objective: str = ""
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    raw_instruction: str = ""


def extract_task_contract(lines: list[RolloutLine]) -> TaskContract:
    """Derive a task contract from the first user message and the first
    thread_goal_updated event (Codex's own extracted "Goals" metadata field)."""
    raw_instruction = ""
    for rl in lines:
        if rl.item_type == "response_item" and rl.item.get("role") == "user":
            for block in rl.item.get("content", []):
                if block.get("type") == "input_text":
                    raw_instruction = block["text"]
                    break
            if raw_instruction:
                break

    goal_text = ""
    for rl in lines:
        if rl.item_type == "event_msg" and rl.item.get("msg", {}).get("type") == "thread_goal_updated":
            goal_text = rl.item["msg"].get("goal", "")
            break

    constraints = _dedupe_constraints(
        _extract_constraints(raw_instruction) + _extract_constraints(goal_text)
    )
    acceptance = _extract_acceptance_criteria(raw_instruction)

    return TaskContract(
        objective=goal_text or raw_instruction,
        constraints=constraints,
        acceptance_criteria=acceptance,
        raw_instruction=raw_instruction,
    )


def _distribute_colon_lists(text: str) -> str:
    """Attach a colon-introduced prohibition to the list items it governs.

        Do not change:
        - existing API contracts
        - services/api/app/context_health/

    Each item IS a constraint, but only the lead-in carries the signal word, so
    the items were dropped and their protected paths lost. This rewrites each
    item to carry the lead-in, producing "Do not change: <item>".

    Runs on RAW markdown, before list markers are stripped.
    """
    import re

    signals = ("do not", "don't", "must not", "should not", "never")
    out: list[str] = []
    lead: str | None = None
    lead_indent = 0

    for line in text.split("\n"):
        stripped = line.strip()
        item = re.match(r"^([ \t]*)(?:[-*+]|\d+\.)[ \t]+(\[[ xX]\][ \t]+)?(.*)$", line)

        if item and lead is not None:
            indent = len(item.group(1).expandtabs())
            if indent >= lead_indent:
                body = item.group(3).strip()
                if body:
                    out.append(f"{item.group(1)}- {lead} {body}")
                    continue

        if stripped.endswith(":") and any(s in stripped.lower() for s in signals):
            # Strip any header/emphasis marks from the lead-in text.
            lead = re.sub(r"^[#>\s*_-]+", "", stripped).rstrip(":").strip() + ":"
            lead_indent = len(line[: len(line) - len(line.lstrip())].expandtabs())
            out.append(line)
            continue

        if not stripped:
            # A blank line between the lead-in and its list is normal markdown;
            # don't drop the lead-in for it. Only real content resets state.
            out.append(line)
            continue
        if not item:
            indent = len(line[: len(line) - len(line.lstrip())].expandtabs())
            # An indented non-item line is a CONTINUATION of the previous list
            # item (markdown soft wrap), not the end of the list. Resetting here
            # made every item after a multi-line item lose its lead-in.
            if lead is not None and indent > lead_indent:
                out.append(line)
                continue
            lead = None
        out.append(line)

    return "\n".join(out)


def strip_markdown_noise(text: str) -> str:
    """Remove markdown constructs that produce false-positive constraints.

    AGENTS.md files are written for humans and contain fenced code blocks, repo
    trees, and headers. Treating them as flat prose caused two problems:

      1. Paths inside code blocks (e.g. a repo-map tree) were extracted as
         "protected" purely because a prohibition appeared nearby in the text.
      2. Headers merged into the constraint that followed them, producing
         constraints like "Hard constraints ### 2.1 Do NOT redesign the UI ...".

    Fenced blocks are removed entirely rather than kept, because a path in an
    example command is not a prohibition. Inline code spans keep their CONTENT
    (constraints legitimately name files inline, e.g. "do not use `border-l-2`")
    but drop the backticks.
    """
    import re

    if not text:
        return ""
    # Must run on raw markdown, while list structure is still intact.
    text = _distribute_colon_lists(text)
    # Fenced code blocks (``` or ~~~), including unterminated trailing fences.
    text = re.sub(r"(?ms)^[ \t]*(```|~~~).*?(^[ \t]*\1[ \t]*$|\Z)", "\n", text)
    # Indented (4-space) code blocks, only when the line looks like code/tree art.
    text = re.sub(r"(?m)^[ \t]{4,}[│├└─|`+\\].*$", "", text)
    # Inline code: keep the content, drop the backticks.
    text = re.sub(r"`([^`\n]*)`", r"\1", text)
    # Markdown headers: drop the marker AND terminate the block, so a header
    # never merges into the sentence beneath it.
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*(.*)$", r"\n\1.\n", text)
    # Checkbox / bullet / numbered-list markers. A blank line is inserted so each
    # item becomes its own block -- otherwise reflow would glue separate list
    # items into one run-on constraint.
    text = re.sub(r"(?m)^[ \t]*[-*+][ \t]+(\[[ xX]\][ \t]+)?", "\n", text)
    text = re.sub(r"(?m)^[ \t]*\d+\.[ \t]+", "\n", text)
    # Blockquote markers and horizontal rules.
    text = re.sub(r"(?m)^[ \t]*>[ \t]?", "", text)
    text = re.sub(r"(?m)^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", "", text)
    # Bold/italic emphasis.
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)
    return text


def _extract_constraints(text: str) -> list[str]:
    """Deterministic constraint extraction: sentences containing constraint
    signal words. Kept deliberately simple/inspectable for the hackathon demo;
    GPT-5.6 is reserved for drift *judgment*, not constraint parsing (see TRD)."""
    if not text:
        return []
    text = strip_markdown_noise(text)
    signals = (
        "do not", "don't", "must not", "should not", "frozen", "constraint:",
        # phrasings that express a prohibition without a "not" verb
        "without modifying", "without touching", "without changing",
        "avoid ", "leave ", "untouched", "off limits", "off-limits",
        "read-only", "read only",
    )
    found = []
    for sentence in _split_sentences(text):
        low = sentence.lower()
        if any(sig in low for sig in signals):
            found.append(sentence.strip())
    return found


def _dedupe_constraints(constraints: list[str]) -> list[str]:
    """Collapse constraints that protect the same paths.

    The raw instruction and Codex's own extracted goal text usually express the
    SAME prohibition in different words ("do NOT touch billing/auth/" vs
    "...without modifying billing/auth/"). Keeping both duplicates every drift
    flag and clutters the recovery brief. Keep the most specific phrasing --
    the shortest one, which is the prohibition itself rather than a whole
    objective sentence that happens to contain it.
    """
    by_terms: dict[frozenset, str] = {}
    unkeyed: list[str] = []

    for constraint in constraints:
        terms = frozenset(_extract_protected_terms(constraint))
        if not terms:
            if constraint not in unkeyed:
                unkeyed.append(constraint)
            continue
        existing = by_terms.get(terms)
        if existing is None or len(constraint) < len(existing):
            by_terms[terms] = constraint

    return list(by_terms.values()) + unkeyed


def _extract_acceptance_criteria(text: str) -> list[str]:
    if not text:
        return []
    signals = ("acceptance criteria", "tests must", "must pass")
    found = []
    for sentence in _split_sentences(text):
        low = sentence.lower()
        if any(sig in low for sig in signals):
            found.append(sentence.strip())
    return found


def _split_sentences(text: str) -> list[str]:
    """Split into candidate constraint units.

    Blocks are separated by blank lines (headers and list items are promoted to
    their own blocks upstream in strip_markdown_noise). Within a block, single
    newlines are markdown SOFT WRAPS and are rejoined -- splitting on them
    truncated constraints mid-sentence ("Do not re-pitch this").
    """
    import re

    if not text:
        return []
    units: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        # Reflow soft-wrapped prose back into one line.
        reflowed = " ".join(line.strip() for line in block.split("\n") if line.strip())
        if not reflowed:
            continue
        # Sentence-split. Requires whitespace plus a sentence-like start, so the
        # dot in "auth.py or ..." doesn't split.
        for part in re.split(r"(?<=[.;!?])\s+(?=[A-Z(\"'])", reflowed):
            part = (part or "").strip()
            if part:
                units.append(part)
    return units


# --- Tool trace extraction -----------------------------------------------------

@dataclass
class ExtractedToolTrace:
    tool: str
    tool_type: str
    input: dict[str, Any]
    output: dict[str, Any]
    timestamp: str


def extract_tool_traces(lines: list[RolloutLine]) -> list[ExtractedToolTrace]:
    """Pairs tool_call response_items with their subsequent tool_result event_msg."""
    traces: list[ExtractedToolTrace] = []
    pending_call: dict[str, Any] | None = None
    pending_ts = ""

    for rl in lines:
        if rl.item_type == "response_item":
            for block in rl.item.get("content", []):
                if block.get("type") == "tool_call":
                    pending_call = block
                    pending_ts = rl.timestamp
        elif rl.item_type == "event_msg":
            msg = rl.item.get("msg", {})
            if msg.get("type") == "tool_result" and pending_call is not None:
                tool_name = pending_call.get("tool_name", "unknown")
                tool_type = "write" if tool_name in ("apply_patch", "shell") and _is_write(pending_call) else "read"
                traces.append(
                    ExtractedToolTrace(
                        tool=tool_name,
                        tool_type=tool_type,
                        input=pending_call.get("input", {}),
                        output=msg.get("output", {}),
                        timestamp=pending_ts,
                    )
                )
                pending_call = None
    return traces


def _is_write(tool_call: dict[str, Any]) -> bool:
    if tool_call.get("tool_name") == "apply_patch":
        return True
    if tool_call.get("tool_name") == "shell":
        cmd = tool_call.get("input", {}).get("command", "")
        return any(w in cmd for w in ("rm ", ">", "git commit", "git push"))
    return False


# --- Compaction detection (explicit, not inferred) -----------------------------

@dataclass
class CompactionEvent:
    index: int
    timestamp: str
    summary: str
    pre_lines: list[RolloutLine]
    post_lines: list[RolloutLine]
    # Lines from THIS compaction up to the next one (or end of session).
    # post_lines spans everything after, which double-counts violations when a
    # session compacts more than once; segment_lines is the correct scope for
    # attributing a violation to the compaction that caused it.
    segment_lines: list[RolloutLine] = field(default_factory=list)
    ordinal: int = 1          # 1-based: "the Nth compaction in this session"
    total_in_session: int = 1


def detect_compactions(lines: list[RolloutLine]) -> list[CompactionEvent]:
    """Codex emits an explicit `compacted` RolloutItem — no heuristic needed.

    Returns ALL compactions. Sessions that compact repeatedly are the severe
    case: reported loss compounds, and the model does not know an earlier
    compaction occurred (openai/codex#14347).
    """
    positions = [i for i, rl in enumerate(lines) if rl.item_type == "compacted"]
    events: list[CompactionEvent] = []
    for n, i in enumerate(positions):
        next_i = positions[n + 1] if n + 1 < len(positions) else len(lines)
        events.append(
            CompactionEvent(
                index=i,
                timestamp=lines[i].timestamp,
                summary=lines[i].item.get("summary", ""),
                pre_lines=lines[:i],
                post_lines=lines[i + 1 :],
                segment_lines=lines[i + 1 : next_i],
                ordinal=n + 1,
                total_in_session=len(positions),
            )
        )
    return events


def analyse_session(contract: "TaskContract", lines: list[RolloutLine], scorer=None) -> list[dict[str, Any]]:
    """Analyse EVERY compaction in a session, in order.

    Returns one record per compaction. Each carries the constraints this
    particular summary failed to retain, the violations committed in this
    segment, and whether the loss is *compounding* — i.e. the constraint was
    already missing from an earlier compaction and has now been dropped again.

    Compounding loss is the failure mode the upstream issues describe as worst:
    by the third compaction the model has no idea a constraint ever existed.
    """
    if scorer is None:
        from .drift_scoring import DeterministicDriftScorer

        scorer = DeterministicDriftScorer()

    events = detect_compactions(lines)
    results: list[dict[str, Any]] = []
    dropped_so_far: set[str] = set()

    for event in events:
        drift: list[dict[str, Any]] = []
        for constraint in contract.constraints:
            score = scorer.score(constraint, event.summary)
            if score.retained:
                continue
            compounding = constraint in dropped_so_far
            drift.append(
                {
                    "constraint": constraint,
                    "rationale": score.rationale,
                    "compounding": compounding,
                    "first_dropped_at_ordinal": None if compounding else event.ordinal,
                }
            )
            dropped_so_far.add(constraint)

        # Scope violations to this segment only.
        segment = CompactionEvent(
            index=event.index,
            timestamp=event.timestamp,
            summary=event.summary,
            pre_lines=event.pre_lines,
            post_lines=event.segment_lines,
        )
        results.append(
            {
                "ordinal": event.ordinal,
                "total_in_session": event.total_in_session,
                "at": event.timestamp,
                "summary": event.summary,
                "drift": drift,
                "violations": find_actual_violations(contract, segment),
                "event": event,
            }
        )

    return results


# --- Drift candidate construction (feeds ContextHealthService) ----------------

def build_drift_candidates(contract: TaskContract, event: CompactionEvent, scorer=None) -> list[dict[str, Any]]:
    """Builds ContextCandidate-shaped dicts comparing pre-compaction constraints
    against the post-compaction summary, so TraceMemory's existing Context Health
    exclusion logic (ctx-stale-block-v1 etc.) can flag dropped/contradicted
    constraints using its current, unmodified scoring rules.

    `scorer` implements DriftScorer (see drift_scoring.py). Defaults to the
    deterministic keyword-overlap scorer if none is given, so this stays keyless
    for tests/demo. Pass drift_scoring.GPT56DriftScorer(...) for the live,
    semantic version -- the one place in this adapter that calls GPT-5.6.
    """
    if scorer is None:
        from .drift_scoring import DeterministicDriftScorer

        scorer = DeterministicDriftScorer()

    candidates = []
    for idx, constraint in enumerate(contract.constraints):
        result = scorer.score(constraint, event.summary)
        candidates.append(
            {
                "source_ref": f"pre_compaction_constraint_{idx}",
                "source_type": "task_constraint",
                "title": "Constraint from original task contract",
                "content": constraint,
                "token_estimate": max(10, len(constraint) // 4),
                "relevance_score": 90,
                "freshness_score": 95,
                "trust_score": 95,
            }
        )
        candidates.append(
            {
                "source_ref": f"post_compaction_summary_re_constraint_{idx}",
                "source_type": "compaction_summary",
                "title": "Post-compaction summary (as it relates to this constraint)",
                "content": event.summary,
                "token_estimate": max(10, len(event.summary) // 4),
                "relevance_score": 90,
                "freshness_score": result.freshness_score,
                "trust_score": result.trust_score,
                "metadata": {
                    "constraint_retained": result.retained,
                    "constraint_ref": f"pre_compaction_constraint_{idx}",
                    "scorer_rationale": result.rationale,
                },
            }
        )
    return candidates


# Markers that switch a constraint sentence from prohibitive to permissive.
# Anything after one of these is an ALLOWANCE, not a protected path -- e.g.
# "do NOT touch billing/auth/ but you may edit billing/refunds.py".
_PERMISSIVE_MARKERS = (
    " but ", " but,", " however", " you may", " you can", " except",
    " it is fine to", " it's fine to", " feel free to", " ok to", " okay to",
)


def _prohibitive_clause(constraint: str) -> str:
    """Truncate a constraint at the first permissive marker so paths mentioned as
    explicitly-allowed don't get treated as protected (false-positive source)."""
    low = constraint.lower()
    cut = len(constraint)
    for marker in _PERMISSIVE_MARKERS:
        idx = low.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return constraint[:cut]


# Markers that BEGIN a prohibition. Paths before these are the edit target
# ("Add support to billing/refunds.py without modifying billing/auth/"), paths
# after them are protected.
_PROHIBITION_MARKERS = (
    "do not", "don't", "must not", "should not", "without modifying",
    "without touching", "without changing", "avoid", "leave", "except",
    "constraint:", "frozen", "off limits", "off-limits", "read-only", "read only",
)


def _extract_protected_terms(constraint: str) -> list[str]:
    import re

    # 1. Drop anything after a permissive marker ("...but you may edit X").
    clause = _prohibitive_clause(constraint)

    # 2. Start from the first prohibition marker, so paths named as the TARGET
    #    of the task (before the prohibition) aren't treated as protected.
    low = clause.lower()
    found = [low.find(m) for m in _PROHIBITION_MARKERS]
    positions = [i for i in found if i != -1]
    # Use None as the sentinel, not 0 -- a marker AT index 0 is legitimate and
    # must not reset the search (that bug made this jump to the LAST marker).
    start = min(positions) if positions else None
    protected_region = clause[start:] if start is not None else clause

    # path/module extraction, e.g. "auth.py", "billing/auth/"
    return [m.lower() for m in re.findall(r"[\w./]+\.\w+|[\w]+/[\w/]*", protected_region)]


def find_actual_violations(contract: TaskContract, event: CompactionEvent) -> list[dict[str, Any]]:
    """Checks post-compaction tool traces against pre-compaction constraints to
    find *actual* violations (not just summary drift) -- the strongest possible
    evidence for the recovery brief / receipt."""
    post_traces = extract_tool_traces(event.post_lines)

    # Keyed by the offending tool call so a single bad edit is reported ONCE,
    # even when several differently-phrased constraints all forbid it.
    by_call: dict[tuple, dict[str, Any]] = {}

    for constraint in contract.constraints:
        protected = _extract_protected_terms(constraint)
        for trace in post_traces:
            path = trace.input.get("path", "") or trace.input.get("command", "")
            if not any(term in path.lower() for term in protected):
                continue
            key = (trace.tool, path, trace.timestamp)
            if key in by_call:
                # Same violation, additional constraint it breaches.
                if constraint not in by_call[key]["constraints"]:
                    by_call[key]["constraints"].append(constraint)
                continue
            by_call[key] = {
                "constraints": [constraint],
                "violating_tool": trace.tool,
                "violating_input": trace.input,
                "timestamp": trace.timestamp,
            }

    violations = list(by_call.values())
    # Back-compat convenience: expose the primary constraint as `constraint`.
    for v in violations:
        v["constraint"] = v["constraints"][0]
    return violations
