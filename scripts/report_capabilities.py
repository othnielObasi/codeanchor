#!/usr/bin/env python3
"""Run the suite and report CAPABILITIES, not just test counts.

A judge reading "56 passed" learns nothing about what was proven. This maps
test outcomes to the claims the project makes, so the output answers
"does it actually do what it says?"
"""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# claim -> substrings of test names that prove it
CLAIMS = [
    ("Parsed Codex execution events",        ["parse_rollout_file", "extract_tool_traces"]),
    ("Extracted the task contract",          ["extract_task_contract", "colon_introduced_list", "soft_wrapped"]),
    ("Detected context compaction",          ["detect_compactions"]),
    ("Analysed EVERY compaction, not just the first", ["detects_all_three", "first_compaction_is_clean", "single_compaction_session"]),
    ("Tracked compounding constraint loss",  ["repeated_loss_is_marked_compounding", "loss_is_detected_at_the_compaction"]),
    ("Attributed violations to the right segment", ["segment_lines_do_not_span", "not_double_counted"]),
    ("Detected requirement drift",           ["drift_detection", "scorer_flags_dropped", "non_path_constraint_absent"]),
    ("Confirmed a real constraint violation",["find_actual_violations", "editing_the_protected_path"]),
    ("Avoided false accusations",            ["explicitly_allowed_path", "path_before_prohibition", "permissive_clause",
                                              "code_fence_contents", "claim_not_confirmed"]),
    ("Inspected the git working tree",       ["detects_a_modified_file", "untracked_new_file", "diff_for_returns"]),
    ("Cross-checked log claims against git", ["claim_confirmed_by_git", "rollout_plus_git", "out_of_band"]),
    ("Caught changes absent from the log",   ["git_only_violation"]),
    ("Validated GPT-5.6 response schema",    ["markdown_fenced_json", "missing_retained_field", "garbage_responses"]),
    ("Survived model/API failure safely",    ["survives_malformed_response", "survives_api_failure"]),
    ("Generated a valid recovery brief",     ["full_adapter_flow", "receipt_includes"]),
    ("Runs automatically at session end (Codex Stop hook)", ["hook_reports_violation", "hook_always_exits_zero", "hook_never_blocks"]),
    ("Installs safely into Codex config",    ["installer_is_idempotent", "preserves_existing", "refuses_to_clobber", "uninstall_removes_only"]),
    ("Ran without consuming credits",        ["gpt56_scorer_sends_constraint", "build_drift_candidates_accepts"]),
]

proc = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "-p", "no:cacheprovider"],
    cwd=ROOT, capture_output=True, text=True,
)
out = proc.stdout + proc.stderr

passed, failed, skipped = set(), set(), set()
for line in out.splitlines():
    if "::" not in line:
        continue
    name = line.split("::")[-1].split()[0]
    if " PASSED" in line:
        passed.add(name)
    elif " FAILED" in line or " ERROR" in line:
        failed.add(name)
    elif " SKIPPED" in line:
        skipped.add(name)

tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
G, R, Y, DIM, X = ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m") if tty else ("",) * 5

print()
for claim, keys in CLAIMS:
    hits_p = [t for t in passed if any(k in t for k in keys)]
    hits_f = [t for t in failed if any(k in t for k in keys)]
    hits_s = [t for t in skipped if any(k in t for k in keys)]
    if hits_f:
        print(f"{R}✗{X} {claim}  {DIM}({len(hits_f)} failing){X}")
    elif hits_p:
        note = f"  {DIM}({len(hits_p)} tests{', ' + str(len(hits_s)) + ' skipped' if hits_s else ''}){X}"
        print(f"{G}✓{X} {claim}{note}")
    elif hits_s:
        print(f"{Y}–{X} {claim}  {DIM}(skipped — see setup notes){X}")
    else:
        print(f"{Y}?{X} {claim}  {DIM}(no matching test){X}")

total = len(passed) + len(failed)
print()
if failed:
    print(f"{R}{len(failed)} failed{X}, {len(passed)} passed" + (f", {len(skipped)} skipped" if skipped else ""))
    print(f"\n{DIM}Re-run with `make test-quick` for the raw pytest output.{X}")
    sys.exit(1)
print(f"{G}{len(passed)} tests passed{X}" + (f", {len(skipped)} skipped" if skipped else ""))
if skipped:
    print(f"{DIM}Skipped tests need TRACEMEMORY_API_PATH — see `make help`.{X}")
sys.exit(0)
