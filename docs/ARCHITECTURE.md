# Architecture

This document covers the competitive positioning (§1), the architectural
blueprint (§2), and the three hard technical problems and their solutions (§6).
The log format lives in [LOG_FORMAT_SPEC.md](LOG_FORMAT_SPEC.md); the phase plan
in [ROADMAP.md](ROADMAP.md).

---

## 1. Competitive audit & unique value proposition

### 1.1 The existing landscape

| Tool | What it actually is | Why it fails the FA use case |
|---|---|---|
| **PySTDF** (and forks: `pystdf`, Semi-ATE `STDF`, `istdf`) | Python readers for binary STDF V4; emit records (PTR/FTR/PRR…) as ATDF text or CSV | Record extraction. The FTR record *contains* failing pin/vector data, but the audited readers surface it as records/CSV — not as reconstructed waveforms or clustered signatures. Core PySTDF effectively unmaintained; community STDF-viewer GUIs present record tables and parametric charts. |
| **Semi-ATE Metis** (and Semi-ATE STDF analytics) | Open-source STDF analytics/viewer tooling | Record-level and parametric/population analytics over STDF; no per-vector waveform reconstruction, no expected-vs-actual differential view, no text-datalog ingestion. |
| **STDF-Viewer** (noonchen) | PyQt/pyqtgraph GUI with a Rust-accelerated STDF V4 parser; trend charts, histograms, Cpk stats, wafer maps, bin summaries, Excel export; last release Dec 2022 | The strongest open-source *parametric* viewer — but its own docs state FTR functional records are flattened to a pass/fail flag "used as the test value". The per-pin/per-vector fail payload is discarded: no waveforms, no expected-vs-actual, no signature clustering. Population altitude, not device FA. |
| **GTKWave** | The canonical VCD/FST waveform viewer (C/GTK), built for RTL simulation debug | Zero concept of *test failure*: no expected-vs-actual, no fail vector, no datalog ingestion. It shows what a signal *did*, never what it *should have done*. Heavy native install — a problem on locked-down fab-floor PCs. |
| **WaveDrom** | JSON → SVG timing diagrams for documentation | Hand-authored, static illustrations. Not data-driven, cannot ingest logs, no interactivity. |
| **Wafer-map / yield tools** (`wafermap`, `stdf2map`; commercial: Synopsys Examinator, PDF Solutions Exensio) | Population-level yield analytics | Lot/wafer statistics altitude. Useless for *single-device, single-vector* failure analysis. Commercial tools are closed and five-figure licensed. |
| **What FA engineers actually do** | `grep` the datalog, paste into Excel, eyeball vector numbers | No structure, no visualization, no repeatability. This manual workflow **is** the competitor to beat. |

**The whitespace — among the audited tools:** none of the tools in this audit
(PySTDF and forks, Semi-ATE STDF/Metis, STDF-Viewer, GTKWave, WaveDrom,
wafer-map/yield tooling; audited July 2026) bridges *textual ATE datalogs →
per-pin waveform reconstruction around the failing vector → automated
fail-signature clustering*. PySTDF-family tools own the record domain; GTKWave
owns the waveform domain; none of the audited set fuses them or speaks the
diagnostic language of an FA report.

> **Scope of the claim.** Every uniqueness claim in this repository is scoped to
> *this audit*, not to all software everywhere. That is both honest and more
> defensible in an interview than an unbounded "nothing like this exists".

### 1.2 Five differentiators

1. **Click-to-Waveform "Vector Replay"** — the flagship. Click any failure row
   and the canvas reconstructs the digital waveform of the failing pin(s) ±N
   cycles around the failing vector — parsed from a *text log*, not a simulation
   dump. This single interaction fuses PySTDF's domain (fail records) with
   GTKWave's domain (waveforms).
2. **Differential waveform lanes** — per pin: *actual* (solid), *expected*
   (ghosted overlay), and a **strobe-anchored mismatch strip** marking exactly
   where the comparator recorded silicon disagreeing with the test program.
   GTKWave draws one truth; we draw **the disagreement**.
3. **Automated fail-signature clustering** — every failure normalizes into
   *(block invocation, collapsed pin group, fail category, vector bucket)*, then
   clusters and ranks: *"83% of failures: SA0-candidate on DQ[7:0], vectors
   1200–1299."* That sentence is 8D-report language, generated automatically.
4. **Grammar-as-data ingestion** — the parser is a formal Lark EBNF grammar (a
   *datalog compiler front-end*), not regex soup. Supporting another tester
   dialect means adding a grammar/adapter file, not rewriting parsing code. The
   native format is deliberately an ATDF-header + VCD-timing hybrid so those
   adapters stay thin.
5. **Fab-floor deployability** — stdlib Tkinter plus one pure-Python dependency
   (`lark`). Runs on offline, admin-locked lab PCs with no browser, no GPU, no
   Electron and **no network**: either a two-wheel offline wheelhouse
   (`pip install --no-index --find-links`) or a single-file `.pyz` zipapp that
   bundles Lark and runs on the machine's stock Python with no pip at all. Both
   are built by `tools/build_release.py` and verified offline in CI. This is a
   genuine semiconductor-environment constraint that quietly disqualifies most
   modern dashboard stacks.

---

## 2. Architectural blueprint

### 2.1 Pattern: MVVM-lite for Tkinter

MVVM adapted to Tkinter's realities. The ViewModel owns all presentation *state*
(current filter, selected failure, zoom window) and exposes it through a tiny
observer/pub-sub. Views subscribe and re-render; they never hold state or
business logic. Everything below the View layer is unit-testable **without a
display**, which is what makes CI possible.

```
┌────────────────────────── main thread (Tk) ──────────────────────────┐
│  VIEW        main_window · failure_table · signature_panel ·         │
│              waveform_canvas          (imports tkinter, NEVER lark)  │
│                  ↑ subscribe/render        ↓ user commands           │
│  VIEWMODEL   app_viewmodel + events.py pub-sub                       │
│              (filter state, selection, zoom window — pure Python)    │
│                  ↑ immutable TestRun       ↓ load/cancel commands    │
├───────────── queue.Queue + root.after(50) pump ──────────────────────┤
│  SERVICES    ParserWorker thread (background.py)                     │
│  MODEL       entities.py · signature.py · waveform.py                │
│  PARSING     chunked_reader → Lark(atelog.lark) → transformer        │
│              (imports lark, NEVER tkinter)                           │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Package layout

```
ate-test-log-tool/
├── ate_fa_suite/
│   ├── __main__.py               # composition root: python -m ate_fa_suite [file]
│   ├── parsing/
│   │   ├── grammar/atelog.lark   # EBNF grammar — data, not code
│   │   ├── parser.py             # LogParser: path → ParseComplete|ParseFailed
│   │   ├── transformer.py        # lark.Transformer → dataclasses
│   │   ├── validator.py          # post-parse semantic validation
│   │   └── chunked_reader.py     # raw-byte framing scanner for large files
│   ├── model/
│   │   ├── entities.py           # frozen dataclasses — the IR/domain types
│   │   ├── signature.py          # fail-signature clustering engine
│   │   └── waveform.py           # WaveformSeries builder (value-change encoding)
│   ├── viewmodel/
│   │   ├── events.py             # minimal observer pub-sub
│   │   └── app_viewmodel.py      # observable app state + commands
│   ├── services/
│   │   ├── background.py         # ParserWorker thread + message queue
│   │   └── file_service.py       # open/recent-files/export
│   └── view/
│       ├── main_window.py        # layout, menu, status bar, queue pump
│       ├── failure_table.py      # searchable ttk.Treeview
│       ├── signature_panel.py    # ranked cluster summary
│       └── waveform_canvas.py    # custom Canvas renderer
├── sample_logs/                  # golden .atelog files
├── tools/                        # gen_log.py · build_release.py · smoke_offline.py
├── tests/
├── docs/
└── pyproject.toml
```

### 2.3 Hard separation rules (enforced, not aspirational)

**Import firewall.** Nothing under `parsing/`, `model/`, `viewmodel/`,
`services/` imports `tkinter`; nothing under `view/` imports `lark`. Enforced by
an **AST-based test** (`tests/test_import_firewall.py`): every module file in
each package is walked with `ast.parse` and any `import`/`from` crossing the
boundary is rejected.

Why AST rather than a `sys.modules` probe — two failure modes the probe has:

* it misses **conditional and function-local** imports entirely;
* it is **contaminated by test ordering** — once any test imports `tkinter`, the
  probe reports it as imported forever.

A `sys.modules` check survives as a supplementary smoke test, run in a
**subprocess** so it stays meaningful. `__main__.py` is exempt: composing the
layers is precisely the composition root's job.

**Threading contract.** Only the main thread touches Tk widgets — Tkinter/Tcl is
not thread-safe, and the worker never calls Tk, *not even* `root.after`. The
`ParserWorker` thread communicates exclusively by putting frozen dataclass
messages (`ParseProgress` / `ParseComplete` / `ParseFailed`) on a `queue.Queue`,
drained by a `root.after(50, pump)` loop in bounded batches. Cancellation is a
`threading.Event` checked between chunks.

Every message carries a **job-generation ID**, and the pump discards messages
from superseded jobs. This is not belt-and-braces: the
stale-`ParseComplete`-after-cancel/reload race is the one logical race that
message-passing alone does *not* remove.

**Immutability at the boundary.** Everything crossing the thread boundary is a
frozen dataclass whose containers are tuples all the way down — never
dict/Mapping, because a frozen dataclass holding a dict is only *shallowly*
immutable. That makes hand-off thread-safe by construction and the payload
picklable.

If the thread is ever swapped for a `multiprocessing` worker, the **message
schema is reusable as-is** — but the transport and lifecycle change:
`multiprocessing.Queue`, process start/join, and cancellation via a sentinel or
event proxy instead of a shared `threading.Event`.

---

## 6. Technical challenges & solutions

### 6.1 Large logs without freezing Tkinter

**1. Never parse on the Tk thread.** `ParserWorker` thread; frozen-dataclass
messages over `queue.Queue`; UI drains via `root.after(50, pump)`. The GIL is
not a problem for *responsiveness* — the UI thread needs only tiny slices.

**2. Chunked parsing via a raw-byte framing scanner.** `chunked_reader.py` is a
small state machine, **not a substring search**. A naive
`str.find("END CYCLE\n")` would miss CRLF records, false-match inside trailing
`//` comment text, and lose block context.

* The scanner streams **raw bytes**, splitting on `\n` (the byte common to LF
  and CRLF endings). Every frame is an **untouched slice of the original byte
  stream**, original line endings included. That raw fidelity is what makes true
  byte offsets and byte-for-byte reassembly possible; frames are *never*
  normalized.
* Normalization exists only in the **classification view**: a decoded throwaway
  copy of each line (trailing `\r` tolerated) is inspected for its **leading
  token only**, so the markers `TESTBLOCK` / `CYCLE` / `END CYCLE` /
  `FAILSUMMARY` / `END TESTBLOCK` / `END LOG` can never be faked by comment or
  value text.
* Frames go to Lark decoded but with original endings intact — the `NEWLINE`
  terminal accepts both, so the parser never needed normalization anyway.
* **Frame-boundary ownership:** comment and blank lines attach to the
  *preceding* frame; the scanner cuts a batch immediately *before* the next
  marker line. The segment grammars cannot accept leading trivia — a frame must
  start with its marker token, and a leading comment line would lex to an orphan
  `NEWLINE`.
* Frame types: a prologue frame; per block a **block-start frame** (where the
  assembler assigns the `BlockId` occurrence), cycle-batch frames (~5k complete
  cycles, each tagged with its enclosing `BlockId` and absolute start line/byte),
  and a block-trailer frame; an end-log frame; and — when EOF arrives mid-record
  — an explicit **truncated-tail** frame.
* Every input line belongs to **exactly one** frame, and every frame type maps
  1:1 to a fragment start rule. Two tests pin this down: **byte-for-byte
  reassembly** proves totality, and an **independent parse of each frame** with
  its fragment rule proves self-containment.
* Lark's frame-relative error positions are rebased by the frame's start line so
  all reporting stays absolute.
* The scanner does **no real parsing** — Lark remains the single syntactic
  authority.

This bounds peak memory, yields byte-accurate progress ticks and cancellation
points, and is the truncation-salvage path.

**3. Don't keep what FA doesn't need.** Two compressions:

* *value-change encoding* — store transitions, not per-cycle states. A clock
  toggling for 10⁶ cycles is 2·10⁶ transitions, but a quiet data pin is ~2
  entries.
* *retention windows* — full waveform fidelity only within ±W vectors of a
  failure; elsewhere keep only counters. A 1M-cycle log with 10 failures reduces
  to kilobytes of waveform data.

Crucially the gaps are **structural**: `WaveformSeries` stores disjoint retained
`WaveformSegment`s, so "discarded" is representable and *distinct from* "held".
`state_at` returns `None` in a gap and the renderer hatches it, rather than
carrying the last state across a region the tool chose to forget.

**4. UI-side hygiene.** Batched Treeview inserts, paging, debounced search — the
table never receives 100k rows in one tick.

### 6.2 Dynamic pixel scaling on the waveform canvas

World coordinates are *time in native timescale units*; the view is fully
described by two numbers, `t_left` and `ppu` (pixels per time-unit).

Key decisions:

* **Block-scoped viewport** — the canvas renders exactly one test block, in
  block-local coordinates. Selecting a failure in another block *swaps the
  series set* rather than scrolling: cross-block time is deliberately not a
  single axis, **because no such axis exists in the log**.
* **Cursor-anchored zoom** — the time under the cursor stays stationary. Naive
  zoom that rescales about x=0 makes the failure fly off-screen; this is the
  difference between a tool that feels professional and one that doesn't.
* **Culling via `WaveformSeries.window`** — bisect at both levels: over segment
  bounds to find the overlapping segments, then within each segment's
  transitions. Extraction is O(log n + k + v), optimal since the output must be
  produced; render cost tracks *visible* transitions only.
* **Float/int boundary** — the viewport is float-valued (zoom math needs it),
  the model API integer-only. The view owns the conversion: `floor(t0)` /
  `ceil(t1)` **widen** the query so edge data is never lost, and all sub-unit
  precision lives in pixel space.

  The widening has a display consequence: clipped segments can extend slightly
  past the float viewport, and at high zoom a sub-unit overhang is *thousands of
  pixels*. So **every display coordinate — segment endpoints and transitions,
  both sides — is clamped** to `[0, canvas_w]` at emit time, and a segment with
  no overlap of the real float viewport (present only because of the widening) is
  **skipped outright** rather than half-clamped into a reversed line or a
  backward-moving paint cursor. `mypy --strict` enforces the boundary.
* **Synthesized coverage endpoints** — each segment's polyline is anchored at its
  *retained-coverage* edges, never at raw transition times. `clipped()`
  re-anchors the carry-in state to the segment start (which fixes the flat-signal
  case — `create_line` needs two points — and avoids the enormous negative Tk
  coordinates a long-quiet pin's real transition time would produce), and the
  final state is held to the segment's `t_end`, so a held signal reads as holding
  *only where data exists*. Every x-range not covered by a segment renders as the
  no-data hatch, never as an interpolated line.
* **Sub-pixel coalescing** — when several transitions land on one pixel column,
  draw a filled "activity" block instead of aliased garbage (the GTKWave trick)
  — **while keeping the column's final state**. The polyline exits the busy
  column at the *last* transition's level, so the following horizontal run holds
  the true state. Dropping later transitions wholesale would leave the wave at a
  stale level until the next visible edge: **wrong data, not just an aliasing
  artifact**.
* **Mismatch strip from `FailureEvent`s, never from waveform XOR** — each
  failing compare on the lane's `(block, pin)` contributes one narrow red band
  centred on its **assembly-resolved** `strobe_time`; width is `strobe_window`
  when window-strobed, else a fixed fraction of `cycle_period`, minimum 2 px.
  Both are assembly-resolved fields, so no cycle data is needed at render time.
  This is ground truth by construction: it inherits the authority policy (so
  masked compares and `INCONSISTENT` records behave correctly) and stays valid
  even where waveform retention has gaps.
* **Redraw strategy** — full `delete("wave")` + redraw, throttled through
  `after_idle` so zoom event storms collapse into one repaint; integer-snapped
  coordinates keep edges crisp.

### 6.3 Waveform ground truth & honest rendering

A tester never observes a continuous waveform. For output pins it knows the
comparator result *only at the strobe instant*; between strobes the true pin
state is unknowable from any datalog. Naively drawing solid continuous "actual"
waves silently claims measurements nobody made.

* **Strobe ticks** — each compare state carries a tick at its strobe instant,
  the only ground truth. The held line between strobes renders **dimmed**, so it
  reads as *inference*, not measurement.
* **Cycle-domain idealization** — ADF-1 v1 renders the NRZ idealization and says
  so in the spec and on the canvas legend. The resolution chain is evaluated
  **once, at assembly time**, while `Cycle` objects still exist: the builder
  bakes drive edges and strobe placement into transition times and stamps
  `FailureEvent.strobe_time`. Cycles are then **discarded**, and the renderer
  consumes only resolved values. The no-IR-change upgrade claim holds because
  `TestRun.timing_sets`, `Cycle.timeset` and `PinDef.timing` **exist now**, not
  because they could be added later.
* **Stimulus is not observation** — `driven_waves` and `captured_waves` are
  separate series and never merge into one "actual" trace. On an IO lane both
  render together, visually distinct, with the direction turnaround readable.
* **Retention gaps render as no-data** — "no transition ⇒ state held" is true
  only *inside* a retained segment. The renderer never extrapolates across its
  own storage optimization.
* **Sparse-capture mode (Tier B)** — when only failing-strobe captures exist,
  the lane renders as a **fail-capture strip**, not a pretend waveform.
* **The optional interval-XOR overlay is inference** — hatched amber,
  legend-labeled "inferred", **off by default**, and confined to retained
  segments.
* **Analog truth is out of scope by physics** — slew, runts and mid-rail floats
  between strobes never reach a datalog. The docs say so, so the tool never
  overclaims.
