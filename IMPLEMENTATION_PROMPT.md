# Implementation Brief — ATE Test Log Visualizer & Diagnostics Suite

You are implementing a fully-specified project plan: `PROJECT_PLAN.md` in this
directory (`C:\Users\HP\Documents\ate-test-log-tool`). Read it completely
before writing any code.

## The plan is authoritative

The plan survived multiple rounds of adversarial design review. Every
structure in it is deliberate and load-bearing against a specific reviewed
defect — including (do not "simplify" any of these):

- the multi-start grammar whose `document` rule is composed FROM the fragment
  rules (`prologue`, `testblock_header`, `cycle_batch`, `block_trailer`,
  `end_log`) — §3.2;
- comments absorbed by the `NEWLINE` terminal (no `%ignore COMMENT`) — §3.2;
- `BlockId(name, occurrence, order=True)`, `VectorLocation`, and `WaveKey` —
  never bare names/vectors/times as run-wide keys — §4;
- three provenance-separated wave collections (driven / expected / captured),
  tuple-backed, never merged and never a dict/Mapping — §4;
- `WaveformSegment`/`WaveformSeries` with `__post_init__` invariants and
  structural retention gaps (gaps render as no-data hatch, never interpolate);
- assembly-time timing resolution (`FailureEvent.strobe_time`,
  `cycle_period`, `strobe_window`) — the renderer never evaluates the timing
  chain and `Cycle` objects are discarded after assembly — §6.3;
- `classify(expected, actual, failed)` authority policy with `INCONSISTENT`
  and `OTHER` — §4;
- job-generation IDs on every worker message; the pump discards stale jobs;
- the raw-byte framing scanner (frames are untouched byte slices; the
  normalized view is classification-only; trivia attaches to the preceding
  frame) — §6.1;
- renderer rules: coverage-edge anchoring, both-sides clamping,
  widening-only segments skipped, coalescing flushes the column's FINAL
  state, mismatch bands from `FailureEvent` only — §6.2.

Do not redesign, rename, or reduce any of this. If you find a genuine
contradiction, ambiguity, or infeasibility in the plan, STOP and raise it at
the current review gate instead of improvising. Where this brief and the
plan disagree, the plan wins — say so and ask.

## Process: gated steps

Work in the numbered steps below, one at a time. At the end of each step:

1. Run the step's verification: `pytest`, `mypy --strict ate_fa_suite`, plus
   the step-specific commands listed in the plan.
2. Commit to git with message `Step N: <name>` (run `git init` in Step 0;
   one commit per step so each review diff is clean).
3. Report to me: files created/changed; any deviation from the plan with
   justification; test results (counts, and verbatim output for anything
   notable); the exact commands I can run to verify manually; open questions.
4. **STOP and wait for my explicit approval before starting the next step.**
   Do not batch steps. Do not continue on silence.

Rules for every step:

- Python ≥ 3.10. Runtime dependency: `lark` only. GUI: stdlib `tkinter`
  only. Dev dependencies: `pytest`, `mypy` (strict mode configured in
  `pyproject.toml`).
- The §2.3 import firewall (AST-based test) is installed in Step 0 and must
  pass at every gate thereafter.
- `pytest` and `mypy --strict` green at every gate — skipped phase-stub
  tests are fine, failures are not.
- Match the plan's names exactly (modules, classes, fields, grammar rules)
  so the docs remain true of the code.
- Tick off completed milestones in `docs/ROADMAP.md` as part of each step.

## Steps

- **Step 0 — Scaffold.** Everything in the plan's "Deliverables on approval"
  table: `README.md`; `docs/ARCHITECTURE.md`, `docs/LOG_FORMAT_SPEC.md`,
  `docs/ROADMAP.md` (content per plan §§1–6); package skeleton with typed
  stubs; `atelog.lark` complete (multi-start, §3.2); `model/entities.py`
  complete per §4; three golden logs; `tools/gen_log.py`;
  `tools/build_release.py`; test harness (grammar-load, golden-parse,
  entities, and import-firewall tests passing; phase stubs skipped);
  `pyproject.toml` with package-data declaration; CI workflow. Verify per
  the plan's Verification section items 1–6 (offline `--no-index` wheelhouse
  install and zipapp launch included).
- **Step 1 — Parser core.** Phase 1 milestones 1–5: transformer to
  dataclasses, error mapping to `ParseFailed`, generator perf corpus,
  truncated golden as expected-failure. Gate: golden round-trips, the
  generator property test, and a recorded parse-throughput baseline.
- **Step 2 — Chunked path + validation.** Phase 1 milestones 6–7: raw-byte
  framing scanner, fragment parsing, truncation salvage, two-tier semantic
  validator. Gate: reassembly property test, independent fragment parses,
  boundary-trivia and CRLF chunked tests, salvage cycle-count assertion, one
  malformed golden per validation rule.
- **Step 3 — Domain logic.** All of Phase 2: classifier truth table (72
  cases), signature clustering, waveform builder with assembly-time timing
  resolution and retention segments, filter predicates, CSV/FA-summary
  exports. Gate: pure unit tests, no display required.
- **Step 4 — GUI shell.** All of Phase 3: pub-sub, ViewModel, worker thread
  with generation-ID pump, failure table, signature panel, status bar,
  cancel. Gate: headless ViewModel tests plus the Tk smoke test.
- **Step 5 — Waveform canvas.** Phase 4 milestones 1–7: segment rendering
  with hatches and clamps, coalescing with final-state flush, zoom/pan,
  crosshair, selection sync. Gate: the canvas introspection test list in
  Phase 4's Verification paragraph.
- **Step 6 — Rendering honesty + release.** Phase 4 milestones 8–9 (strobe
  ticks, legend, inferred-disagreement overlay with its off-by-default
  assertions), the < 50 ms redraw perf gate, end-to-end release proofs
  (wheelhouse `--no-index`, zipapp, CI green), README screenshots.

Begin with Step 0. Before writing any file, post a short (≤ 15 line)
inventory of what Step 0 will create, then proceed.
