# Roadmap

Four phases, gated. Each gate requires `pytest` green, `mypy --strict
ate_fa_suite` green, and the §2.3 import firewall passing — skipped phase-stub
tests are fine, failures are not.

Legend: `[x]` done · `[ ]` outstanding.

---

## Step 0 — Scaffold (complete)

- [x] `README.md`, `docs/ARCHITECTURE.md`, `docs/LOG_FORMAT_SPEC.md`, this file
- [x] Package skeleton with typed stubs across all five layers
- [x] `atelog.lark` complete — multi-start, LALR + contextual, load-tested
- [x] `model/entities.py` complete, with `__post_init__` invariants
- [x] Three golden logs: `clean_pass`, `multi_fail`, `truncated`
- [x] `tools/gen_log.py` — deterministic generator + property-test oracle
- [x] `tools/build_release.py` — wheelhouse + single-file zipapp
- [x] `tools/smoke_offline.py` — offline install proof, refuses a source tree
- [x] `tools/check_zipapp.py` — zipapp proof, refuses when ambient Lark exists
- [x] Import firewall (AST-based) installed and passing
- [x] `pyproject.toml` with `atelog.lark` as package data; CI workflow
- [x] Verification items 1–6 all pass, including offline `--no-index` install
      and zipapp launch from a venv with nothing pip-installed

---

## Phase 1 — Grammar & Parsing Engine (~4–5 days)

**Step 1 — Parser core.** Gate: golden round-trips, the generator property
test, and a recorded parse-throughput baseline.

- [x] M1. Freeze the ADF-1 spec; author 3 golden logs
- [x] M2. Grammar parses the two well-formed goldens; `truncated` is an
      **expected-failure** fixture asserting `UnexpectedToken`/`UnexpectedEOF`
      at the break
- [ ] M3. `AteLogTransformer` produces fully populated dataclasses; every
      grammar rule covered
- [ ] M4. `tools/gen_log.py --cycles 1000000 --pins 16 --fail-rate 0.0005
      --seed 42` perf corpus *(generator done in Step 0; the 1M-cycle corpus run
      and its baseline belong to this gate)*
- [ ] M5. Error mapping: `UnexpectedToken`/`UnexpectedCharacters` →
      `ParseFailed` with line, column, and the offending source line

**Step 2 — Chunked path + validation.** Gate: reassembly property test,
independent fragment parses, boundary-trivia and CRLF chunked tests, salvage
cycle-count assertion, one malformed golden per validation rule.

- [ ] M6. Raw-byte framing scanner; fragment parsing; truncation salvage
  - [ ] Frames are untouched byte slices; normalization is classification-only
  - [ ] Markers recognized by **leading token** only
  - [ ] Trivia attaches to the **preceding** frame
  - [ ] Byte-for-byte reassembly property test
  - [ ] Every emitted frame parses independently with its fragment rule
  - [ ] Truncated golden: exactly **3** recovered cycles + absolute warning line
- [ ] M7. Two-tier semantic validator (fatal → `ParseFailed`, recoverable →
      `TestRun.warnings` + a deterministic rule); one malformed golden per rule
  - [ ] `FAILSUMMARY` **count** vs observed failing compare lines (pin-granular)
  - [ ] `FAILSUMMARY` **`VECTORS`** vs the observed set of vectors with ≥1 `FAIL`
        — a separate warning from the count check, so it stays visible which
        witness disagreed

---

## Phase 2 — Domain Logic (~3–4 days)

**Step 3.** Gate: pure unit tests, no display required.

- [ ] M1. `FailCategory.classify` authority policy; exhaustive 72-case truth
      table *(the classifier itself is complete in `entities.py`; this gate is
      the exhaustive test plus the warnings integration)*
- [ ] M2. Signature engine: bus collapsing, vector bucketing, ranking by share
- [ ] M3. `WaveformSeries` builder — three provenance-separated series kinds,
      ±W retention windows merged into disjoint segments, **assembly-time**
      timing resolution, `Cycle` objects discarded afterwards
- [ ] M4. Composable filter predicates
- [ ] M5. Exports: failures CSV + plain-text FA summary

---

## Phase 3 — GUI Shell (~4–5 days)

**Step 4.** Gate: headless ViewModel tests plus the Tk smoke test.

- [ ] M1. Main window skeleton, menu, Open dialog, recent files
- [ ] M2. `ParserWorker` + queue + `after(50)` pump + progress + cancel, with
      job-generation IDs discarding stale messages
- [ ] M3. Failure table: column sort, 300 ms-debounced search, batched inserts
      (500 rows/tick), paging above 10k rows
- [ ] M4. Signature panel: ranked clusters, click-to-filter
- [ ] M5. Status bar: file, parse time, cycle/fail counts, warnings

### Manual test checklist — Phase 3

Run against `sample_logs/multi_fail.atelog` and a 1M-cycle generated corpus.

- [ ] Window opens at a sane default size; below the minimum size nothing
      overlaps or clips
- [ ] Open a file; progress bar advances smoothly, UI stays responsive
- [ ] Press Cancel mid-parse — UI responds in **< 200 ms**
- [ ] Open a second file *while the first is still parsing*: the first result
      never lands (job-generation IDs), and the status bar reflects the second
- [ ] Open `truncated.atelog`: partial result loads, warning visible in the
      status bar with its **absolute** line number — no traceback
- [ ] Open a malformed file: the error dialog shows the offending raw line
- [ ] Open `clean_pass.atelog`: empty failure table and empty signature panel,
      no crash, no "0%" nonsense
- [ ] Search box: typing fast does not stutter (debounce works)
- [ ] Sort each column ascending and descending
- [ ] Click a signature cluster: the failure table filters to its members
- [ ] Both invocations of `mbist_march_c` appear separately (`#2` suffix)

---

## Phase 4 — Custom Canvas Waveform Renderer (~5–6 days)

**Step 5 — Waveform canvas.** Gate: the canvas introspection tests.

- [ ] M1. Static single-pin lane: square-wave polyline from one series
- [ ] M2. Expected ghost overlay + mismatch bands from `FailureEvent`s
- [ ] M3. Multi-lane layout, pin-label gutter, time-axis ruler
- [ ] M4. Cursor-anchored wheel zoom, drag pan, "zoom to fail"
- [ ] M5. Hover crosshair with time + per-pin state readout
- [ ] M6. Virtualization: viewport culling + sub-pixel coalescing that flushes
      the column's **final** state
- [ ] M7. Selection sync: table row ↔ canvas highlight, both directions

**Step 6 — Rendering honesty + release.** Gate: the < 50 ms redraw budget and
the end-to-end release proofs.

- [ ] M8. Strobe ticks at assembly-resolved instants, dimmed held-state
      inference, canvas legend noting the NRZ idealization
- [ ] M9. Optional inferred-disagreement overlay — hatched amber, legend-labeled
      "inferred", **off by default**, confined to retained segments
- [ ] Perf gate: full redraw **< 50 ms** at 200 visible transitions × 12 lanes
- [ ] Release proofs re-run end to end; README screenshots

### Manual test checklist — Phase 4

- [ ] Click a failure row: the canvas jumps to that vector, in that block
- [ ] Wheel-zoom with the cursor over a transition — that transition stays
      under the cursor (anchor-point zoom)
- [ ] Zoom out until several transitions share a pixel column: an activity block
      appears, and the wave leaves the column at the **correct** level
- [ ] Zoom in past 1 pixel/time-unit: no reversed lines, no runaway coordinates
- [ ] Retention gaps show the no-data hatch — **never** a line across the gap
- [ ] A pin that never transitions in view still draws a flat line edge to edge
- [ ] `X` renders as a gray hatched block; `Z` as a dashed mid-level line
- [ ] Mismatch bands appear only at failing strobes; a masked compare produces
      none
- [ ] Toggle the inferred-disagreement overlay: it is off on startup, hatched
      amber when on, and labeled "inferred" in the legend
- [ ] An IO pin shows driven and captured lanes distinctly; the turnaround is
      readable
- [ ] Select a failure in the *other* invocation of `mbist_march_c`: the series
      set swaps, the axis does not try to span both blocks
- [ ] > 20 failing pins: lanes scroll vertically
- [ ] Windows DPI scaling at 150%: text and lanes stay aligned

---

## Future work (deliberately out of scope)

- **Tier B adapter** — STDF/ATDF `FTR` ingestion: full table + clustering,
  fail-capture-strip waveform
- **Tier C adapter** — STIL (IEEE 1450) pattern parsing via a second Lark
  grammar, reconstructing full expected waveforms
- VCD front-end; shmoo-plot panel; similarity-based signature clustering
