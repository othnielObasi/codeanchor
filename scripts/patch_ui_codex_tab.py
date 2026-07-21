#!/usr/bin/env python3
"""Add a "Codex Recovery" tab to TraceMemory's existing console UI.

UI_SOURCE_OF_TRUTH.md forbids redesigning the approved Continuum UI. This script
therefore does the ONLY thing that document allows: backend wiring around the
existing demo structure. Specifically it:

  1. adds one entry to the existing `tabs` array and `labels` map in consoleView()
  2. adds one branch to the existing consoleTab conditional
  3. injects a render function that uses ONLY classes already defined in the file
     (pro-panel, pro-kicker, pro-copy, pro-timeline, pro-step, badge(), and
     existing Tailwind utilities)

No new CSS. No new colours. No new layout primitives. No card redesign.

Idempotent: running twice is a no-op. Use --revert to undo.

Usage:
    python3 scripts/patch_ui_codex_tab.py <path-to-repo-root>
    python3 scripts/patch_ui_codex_tab.py <path-to-repo-root> --revert
    python3 scripts/patch_ui_codex_tab.py <path-to-repo-root> --check
"""
from __future__ import annotations

import argparse
import os
import sys

# All three synced copies named in UI_SOURCE_OF_TRUTH.md.
UI_FILES = [
    "HACKATHON_UI.html",
    "apps/console/public/hackathon-ui.html",
    "docs/assets/tracememory-hackathon-ui.html",
]

MARKER = "codexRecovery"

# --- 1. tabs array -----------------------------------------------------------
TABS_OLD = '''const tabs = ["overview","runs","recovery","checkpoints","toolEvidence","modelRoutes","memory","audit","settings"];'''
TABS_NEW = '''const tabs = ["overview","runs","recovery","codexRecovery","checkpoints","toolEvidence","modelRoutes","memory","audit","settings"];'''

LABELS_OLD = '''const labels = {overview:"Overview",runs:"Runs",recovery:"Recovery",checkpoints:"Checkpoints",toolEvidence:"Tool Evidence",modelRoutes:"Model Routes",memory:"Memory",audit:"Audit",settings:"Settings"};'''
LABELS_NEW = '''const labels = {overview:"Overview",runs:"Runs",recovery:"Recovery",codexRecovery:"Codex Recovery",checkpoints:"Checkpoints",toolEvidence:"Tool Evidence",modelRoutes:"Model Routes",memory:"Memory",audit:"Audit",settings:"Settings"};'''

# --- 2. consoleTab branch ----------------------------------------------------
BRANCH_OLD = '''${consoleTab==="modelRoutes"?`<div class="mt-8 pro-panel p-6">${integrationPanel()}</div>`:consoleTab==="settings"?`<div class="mt-8 pro-panel p-6">${operatingMap()}</div>`:`'''
BRANCH_NEW = '''${consoleTab==="codexRecovery"?codexRecoveryPanel():consoleTab==="modelRoutes"?`<div class="mt-8 pro-panel p-6">${integrationPanel()}</div>`:consoleTab==="settings"?`<div class="mt-8 pro-panel p-6">${operatingMap()}</div>`:`'''

# --- 3. the panel itself -----------------------------------------------------
# Uses only pre-existing classes. Calls the real API when reachable; falls back
# to the shipped fixture result so judges can run it with no credentials and no
# backend (same keyless principle as DEMO_MODE elsewhere in this repo).
PANEL_JS = r'''
/* ---- Codex Recovery (Build Week adapter) ---------------------------------
   Wiring only: reuses the existing console shell, pro-panel surfaces, badge()
   and pro-timeline. No new visual system per UI_SOURCE_OF_TRUTH.md. */
let codexResult = null;
let codexLoading = false;

const CODEX_FIXTURE_RESULT = {
  session: "rollout-2026-07-14T09-00-00-8f21.jsonl",
  objective: "Add partial-refund support to billing/refunds.py without modifying billing/auth/ or auth.py; keep existing refund tests green; add rounding tests.",
  constraints: ["Constraint: do NOT touch auth.py or anything in billing/auth/ - that module is frozen for the compliance audit this week"],
  protected_paths: ["auth.py", "billing/auth/"],
  compaction: {
    at: "2026-07-14T09:01:10Z",
    summary: "User asked to add partial refunds to refunds.py. Assistant added calculate_partial_refund and rounding logic, tests passing (6 passed). Continuing to add rounding edge-case tests next."
  },
  drift: [{ policy_id: "ctx-stale-block-v1", rationale: "Protected path from constraint is absent from the compaction summary." }],
  violations: [{ tool: "apply_patch", path: "billing/auth/eligibility.py", at: "2026-07-14T09:01:30Z",
                 constraint: "do NOT touch auth.py or anything in billing/auth/" }],
  timeline: [
    ["Task contract captured", "objective + 1 constraint extracted from the opening instruction"],
    ["Tool evidence recorded", "3 tool calls traced (apply_patch, shell, apply_patch)"],
    ["Checkpoint saved", "pre-compaction state stored, safe_to_resume=true"],
    ["Compaction detected", "explicit Compacted item in the rollout - not inferred"],
    ["Drift flagged", "compaction summary dropped the frozen-module constraint"],
    ["Violation confirmed", "post-resume apply_patch touched billing/auth/eligibility.py"],
    ["Recovery brief issued", "original constraint restored for the resumed session"]
  ]
};

async function runCodexDemo(){
  codexLoading = true; render();
  try {
    const res = await fetch("/api/demo/codex-recovery", {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
    if(!res.ok) throw new Error(res.status);
    codexResult = await res.json();
  } catch (e) {
    // Keyless fallback so the story runs with no backend and no credentials.
    codexResult = Object.assign({}, CODEX_FIXTURE_RESULT, {offline:true});
  }
  codexLoading = false; render();
}

function codexRecoveryPanel(){
  const r = codexResult;
  if(!r){
    return `<div class="mt-8 pro-panel p-6">
      ${badge("Build Week adapter","dark")}
      <h2 class="mt-4 text-2xl font-black tracking-[-0.04em]">Codex session recovery</h2>
      <p class="pro-copy mt-3 max-w-3xl">Codex can preserve the code while losing the state of the work. When a long session is compacted, the files survive but the constraints governing them may not. This reads a real Codex rollout, checks the post-compaction summary against the original task contract, and proves whether the resumed session still honoured it.</p>
      <div class="pro-section mt-8">
        <p class="pro-kicker">Run</p>
        <p class="pro-copy mt-2 max-w-3xl">Replays a recorded Codex session through the TraceMemory recovery pipeline: task contract, tool evidence, checkpoint, compaction, drift check, recovery brief.</p>
        <button onclick="runCodexDemo()" class="mt-5 rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-bold text-white hover:bg-neutral-800">${codexLoading?'<span class="tspin"></span> Running':'Run Codex Recovery Demo'}</button>
      </div>
    </div>`;
  }

  const timeline = r.timeline.map(([t,d])=>`<div class="pro-step"><p class="text-sm font-bold">${t}</p><p class="pro-copy mt-1">${d}</p></div>`).join("");
  const drift = (r.drift||[]).map(d=>`<div class="pro-row"><div><p class="pro-kicker">${d.policy_id}</p></div><div><p class="pro-copy">${d.rationale}</p></div></div>`).join("");
  const viol = (r.violations||[]).map(v=>`<div class="pro-row"><div><p class="text-sm font-bold">${v.tool}</p><p class="mt-1 text-xs font-medium text-neutral-500">${v.at||""}</p></div><div><p class="font-mono text-sm text-rose-700">${v.path}</p><p class="pro-copy mt-1">Breaches: ${v.constraint}</p></div></div>`).join("");

  return `<div class="mt-8 pro-panel p-6">
    <div class="flex flex-wrap items-center gap-2">
      ${badge("Build Week adapter","dark")}
      ${r.violations && r.violations.length ? badge("Constraint violated","amber") : badge("No violation","green")}
      ${r.offline ? badge("Offline fixture","neutral") : badge("Live runtime","green")}
    </div>
    <h2 class="mt-4 text-2xl font-black tracking-[-0.04em]">Codex session recovery</h2>
    <p class="mt-2 font-mono text-xs text-neutral-500">${r.session}</p>

    <div class="pro-section mt-8">
      <p class="pro-kicker">Task contract</p>
      <p class="pro-copy mt-2 max-w-3xl">${r.objective}</p>
      ${(r.constraints||[]).map(c=>`<div class="mt-3 rounded-xl bg-neutral-50 px-4 py-3 text-sm leading-6 text-neutral-700 ring-1 ring-neutral-200">${c}</div>`).join("")}
      <p class="mt-3 text-xs font-medium text-neutral-500">Protected paths: <span class="font-mono">${(r.protected_paths||[]).join(", ")}</span></p>
    </div>

    <div class="pro-section mt-8">
      <p class="pro-kicker">Compaction</p>
      <p class="mt-2 text-xs font-medium text-neutral-500">${r.compaction.at}</p>
      <div class="mt-3 rounded-xl bg-amber-100 px-4 py-3 text-sm leading-6 text-amber-700">${r.compaction.summary}</div>
      <p class="pro-copy mt-3">The summary is accurate about progress. It says nothing about the frozen module.</p>
    </div>

    ${drift?`<div class="pro-section mt-8"><p class="pro-kicker">Drift flagged by Context Health</p><div class="mt-2">${drift}</div></div>`:""}
    ${viol?`<div class="pro-section mt-8"><p class="pro-kicker">Confirmed violation after resume</p><div class="mt-2">${viol}</div></div>`:""}

    <div class="pro-section mt-8">
      <p class="pro-kicker">Recovery record</p>
      <div class="pro-timeline mt-4">${timeline}</div>
    </div>

    <div class="pro-section mt-8">
      <button onclick="codexResult=null; render()" class="rounded-full px-4 py-2 text-sm font-bold text-neutral-600 hover:bg-neutral-100">Reset</button>
      <button onclick="runCodexDemo()" class="ml-2 rounded-full bg-neutral-950 px-5 py-2.5 text-sm font-bold text-white hover:bg-neutral-800">Run again</button>
    </div>
  </div>`;
}
'''

ANCHOR = "function consoleView(){"


def patch_text(src: str) -> tuple[str, list[str]]:
    notes = []
    if MARKER in src:
        return src, ["already patched (no changes)"]

    for old, new, what in (
        (TABS_OLD, TABS_NEW, "tabs array"),
        (LABELS_OLD, LABELS_NEW, "labels map"),
        (BRANCH_OLD, BRANCH_NEW, "consoleTab branch"),
    ):
        if old not in src:
            raise SystemExit(f"ANCHOR NOT FOUND: {what}\nThe UI file differs from the expected v6 build; aborting rather than guessing.")
        src = src.replace(old, new, 1)
        notes.append(f"patched {what}")

    if ANCHOR not in src:
        raise SystemExit("ANCHOR NOT FOUND: function consoleView(){")
    src = src.replace(ANCHOR, PANEL_JS + "\n" + ANCHOR, 1)
    notes.append("injected codexRecoveryPanel()")
    return src, notes


def revert_text(src: str) -> tuple[str, list[str]]:
    if MARKER not in src:
        return src, ["not patched (no changes)"]
    src = src.replace(TABS_NEW, TABS_OLD, 1)
    src = src.replace(LABELS_NEW, LABELS_OLD, 1)
    src = src.replace(BRANCH_NEW, BRANCH_OLD, 1)
    src = src.replace(PANEL_JS + "\n" + ANCHOR, ANCHOR, 1)
    return src, ["reverted"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="TraceMemory repo root")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true", help="report status, write nothing")
    args = ap.parse_args()

    any_missing = False
    for rel in UI_FILES:
        path = os.path.join(args.repo, rel)
        if not os.path.exists(path):
            print(f"[skip] {rel} (not found)")
            any_missing = True
            continue
        src = open(path, encoding="utf-8").read()

        if args.check:
            print(f"[{'patched' if MARKER in src else 'clean  '}] {rel}")
            continue

        out, notes = (revert_text(src) if args.revert else patch_text(src))
        if out != src:
            open(path, "w", encoding="utf-8").write(out)
        print(f"[ok] {rel}: {', '.join(notes)}")

    if any_missing:
        print("\nSome synced UI copies were missing; UI_SOURCE_OF_TRUTH.md lists three.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
