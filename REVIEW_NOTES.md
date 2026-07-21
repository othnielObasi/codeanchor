# Review Notes — Day 2 hardening pass

Six defects found and fixed during a deliberate review pass after the GPT-5.6
scorer landed. All are locked in by regression tests in `tests/test_regressions.py`.

These are worth keeping in the submission README: they're concrete evidence of
engineering judgment (the "where did YOU make key decisions" part of the Build
Week judging criteria), not just generated code that happened to pass.

---

### Bug 1 — Low-confidence drift verdicts silently passed
`GPT56DriftScorer` mapped a "not retained" verdict at 55% confidence to a score
of 40. Context Health excludes below 35, so an uncertain drift flag would have
**passed silently** instead of surfacing.

**Fix:** cap any "not retained" verdict at 34. A low-confidence drift flag is
still safer than a silent pass.

---

### Bug 2 — Permissive clauses produced false-positive violations
`"do NOT touch billing/auth/ but you may freely edit billing/refunds.py"`
extracted **both** paths as protected. Editing the explicitly-allowed file would
have been reported as a constraint violation — in front of judges.

**Fix:** `_prohibitive_clause()` truncates the sentence at permissive markers
(`but`, `however`, `you may`, `except`, ...) before extracting paths.

---

### Bug 3 — Constraints phrased without a "not" verb were dropped entirely
`"...without modifying billing/auth/"` matched none of the signal words, so the
constraint never entered the task contract and was never checked.

**Fix:** extended the signal list with prohibition phrasings that carry no "not"
verb (`without modifying/touching/changing`, `avoid`, `leave`, `untouched`,
`off-limits`, `read-only`).

---

### Bug 4 — Non-path constraints silently passed as "retained"
`"do not use regex"` yielded no path-shaped terms, and the scorer's
`(not terms) or ...` short-circuit returned `retained=True`. Any constraint that
wasn't about a file path was **never actually checked**.

**Fix:** fall back to content-word overlap on the prohibitive clause (with a
stopword filter). When nothing checkable remains, return *flagged for review*
rather than passing — same bias-toward-surfacing principle as Bug 1.

---

### Bug 5 — The task's own edit target was treated as protected
After fixing Bug 3, `"Add partial-refund support to billing/refunds.py without
modifying billing/auth/"` extracted `refunds.py` as protected — meaning editing
the file you were **asked** to edit would be flagged as a violation.

**Fix:** extraction now starts at the first prohibition marker. Paths named
*before* the prohibition are the target; paths *after* it are protected.

---

### Bug 6 — A marker at index 0 broke the prohibition search
The marker scan used `0` as its "not found" sentinel:
```python
start = idx if start == 0 else min(start, idx)
```
A legitimate marker **at position 0** (`"Constraint: do NOT touch..."`) reset the
tracking, so the scan jumped to the *last* marker (`"frozen"`, at the end of the
sentence) and returned **zero** protected paths — silently disabling violation
detection for the most naturally-phrased constraints.

**Fix:** use `None` as the sentinel and take `min()` over all found positions.

---

### Follow-on: duplicate reporting
Fixing Bug 3 meant the raw instruction and Codex's own extracted goal text both
yielded a constraint forbidding the same path, so one offending `apply_patch`
was reported **twice**.

**Fix (two parts):**
1. `find_actual_violations()` keys violations by the offending tool call, so one
   bad edit is reported once, listing every constraint it breaches.
2. `_dedupe_constraints()` collapses constraints protecting an identical path
   set, keeping the most specific phrasing.

---

## Why this matters for the submission

Bugs 2, 5, 6, and 7 were all **correctness bugs in the core claim of the product** —
the thing being demoed is "we detect when Codex violates a constraint," and each
of these would have produced either a false accusation or a silent miss. Bugs 1
and 4 were both instances of the same design smell: an uncertain or unverifiable
result defaulting to "fine." The consistent fix was to bias every ambiguous case
toward *surfacing for review*, which is the correct default for a verification
layer.

---

### Bug 7 — Markdown parsed as flat prose (found by running the parser on this project's own AGENTS.md)

`_extract_constraints` treated markdown as plain text. Running it against the
project's own `AGENTS.md` surfaced four distinct defects at once:

1. **Code fences leaked.** Paths in a repo-map tree were extracted as protected
   purely because a prohibition appeared nearby — `fixtures/sample_rollout.jsonl`
   was marked frozen. Same false-positive class as Bugs 2 and 5, new source.
2. **Headers merged downward**, producing constraints like
   `"Hard constraints ### 2.1 Do NOT redesign the UI ..."`.
3. **Soft wraps truncated constraints.** Splitting on single newlines cut
   `"Do not re-pitch this as memory for Codex; it will be judged..."` down to
   `"Do not re-pitch this"`.
4. **Colon-introduced lists lost their prohibition.** `"Do not change:"` followed
   by a list meant only the lead-in carried the signal word, so every item was
   dropped — losing the most important protected paths in the project
   (`context_health/`, `crewai.py`, `langgraph.py`, `openai_agents.py`).

**Fix:** `strip_markdown_noise()` removes fenced/indented code blocks, promotes
headers and list items to their own blocks, and keeps inline-code *content*
(constraints legitimately name files inline). `_split_sentences()` reflows
soft-wrapped lines within a block instead of splitting on them.
`_distribute_colon_lists()` runs on raw markdown and rewrites each governed list
item to carry its lead-in.

Two follow-on bugs surfaced while fixing (4): a blank line between the lead-in
and its list reset the state, and an indented **continuation** line of a
multi-line list item did too — so every item after a wrapped item silently lost
its lead-in. Both are covered by
`test_colon_introduced_list_distributes_the_prohibition`.

**Why it matters:** the demo is stronger against a real repo's `AGENTS.md` than
against a synthetic fixture — but only if the parser reads markdown correctly.
Before this fix it would have flagged a file nobody froze and missed the four
paths that actually are.
