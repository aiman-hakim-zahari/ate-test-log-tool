# ATE Test Log Visualizer & Diagnostics Suite — Project Plan & Architecture

## Context

The user is preparing a portfolio project for a Failure Analysis (FA) software development role (NXP PDQC-style). The goal is a **Python 3 tool that parses semi-structured ATE datalogs (Lark) and presents an interactive FA dashboard (Tkinter)** — failure table, fail-signature summary, and a hand-drawn digital waveform canvas around the failing vectors. The hard constraint is *radical uniqueness*: it must not read as "another log viewer," and its differentiation versus PySTDF / GTKWave / WaveDrom must be explicit and defensible.

**Scope agreed with user:** on approval, deliver the **design docs + project scaffold** into `C:\Users\HP\Documents\ate-test-log-tool` — architecture documents, package skeleton with typed stubs, the complete `.lark` grammar, implemented dataclasses, golden sample logs, a working synthetic log generator, and a pytest harness whose grammar/sample tests actually pass. The parser transformer, domain logic, and GUI remain for the user to implement per the roadmap (that's the portfolio work).

---

## 1. Competitive Audit & Unique Value Proposition

### 1.1 Audit of the existing landscape

| Tool | What it actually is | Why it fails the FA use case |
|---|---|---|
| **PySTDF** (and forks: `pystdf`, Semi-ATE `STDF`, `istdf`) | Python readers for binary STDF V4; emit records (PTR/FTR/PRR…) as ATDF text or CSV | Record extraction. The FTR record *contains* failing pin/vector data, but the audited readers surface it as records/CSV — not as reconstructed waveforms or clustered signatures. Core PySTDF effectively unmaintained; community STDF-viewer GUIs exist but present record tables and parametric charts. |
| **Semi-ATE Metis** (and Semi-ATE STDF analytics) | Open-source STDF analytics/viewer tooling from the Semi-ATE project | Record-level and parametric/population analytics over STDF; no per-vector waveform reconstruction, no expected-vs-actual differential view, no text-datalog ingestion. |
| **STDF-Viewer** (noonchen) | PyQt/pyqtgraph GUI with a Rust-accelerated binary STDF V4 parser; trend charts, histograms, Cpk stats, wafer maps, bin summaries, Excel export; last release Dec 2022 | The strongest open-source *parametric* viewer — but its own docs state FTR functional records are flattened to a pass/fail flag "used as the test value." The per-pin/per-vector fail payload is discarded: no waveforms, no expected-vs-actual, no signature clustering. Population altitude, not device FA. |
| **GTKWave** | The canonical VCD/FST waveform viewer (C/GTK), built for RTL simulation debug — its documentation is explicit about the dumpfile focus | Zero concept of *test failure*: no expected-vs-actual, no fail vector, no datalog ingestion. It shows what a signal *did*, never what it *should have done*. Heavy native install — a problem on locked-down fab-floor PCs. |
| **WaveDrom** | JSON → SVG timing diagrams for documentation | Hand-authored, static illustrations. Not data-driven, cannot ingest logs, no interactivity. |
| **Wafer-map / yield tools** (`wafermap`, `stdf2map`, commercial: Synopsys/Galaxy Examinator, PDF Solutions Exensio) | Population-level yield analytics | Operate at lot/wafer statistics altitude. Useless for *single-device, single-vector* failure analysis. Commercial tools are closed and five-figure licensed. |
| **What FA engineers actually do** | `grep` the datalog, paste into Excel, eyeball vector numbers | No structure, no visualization, no repeatability. This manual workflow **is** the competitor to beat. |

**The whitespace — among the audited tools:** none of the tools in this audit (PySTDF and forks, Semi-ATE STDF/Metis, STDF-Viewer, GTKWave, WaveDrom, wafer-map/yield tooling; audited July 2026) bridges *textual ATE datalogs → per-pin waveform reconstruction around the failing vector → automated fail-signature clustering*. PySTDF-family tools own the record domain; GTKWave owns the waveform domain; none of the audited set fuses them or speaks the diagnostic language of an FA report. All uniqueness claims in this document and the README are scoped to this audit, not to all software everywhere — that's both honest and more defensible in an interview.

### 1.2 Our five differentiators

1. **Click-to-Waveform "Vector Replay"** — the flagship feature. Click any failure row and the canvas instantly reconstructs the digital waveform of the failing pin(s) ±N cycles around the failing vector — parsed from a *text log*, not a simulation dump. This single interaction fuses PySTDF's domain (fail records) with GTKWave's domain (waveforms), which none of the audited tools does.
2. **Differential waveform lanes** — per pin, the canvas draws *actual* (solid), *expected* (ghosted overlay), and a **strobe-anchored mismatch strip** highlighting exactly where the comparator recorded silicon disagreeing with the test program. GTKWave draws one truth; we draw the disagreement — the thing an FA engineer actually needs to see.
3. **Automated Fail-Signature Clustering** — every failure is normalized into a signature tuple *(collapsed pin group, fail category, vector-window bucket)*, then clustered and ranked: *"83% of failures: SA0-candidate on DQ[7:0], vectors 1200–1299."* That sentence is 8D-report language, generated automatically. None of the audited tools produces it from a datalog.
4. **Grammar-as-data ingestion** — the parser is a formal Lark EBNF grammar (a *datalog compiler front-end*), not regex soup. Supporting another tester dialect (Teradyne UltraFLEX datalog, Advantest 93k dlog, ATDF, VCD) means adding a grammar/adapter file, not rewriting parsing code. Our native format is deliberately designed as an ATDF-header + VCD-timing hybrid so those adapters stay thin.
5. **Fab-floor deployability** — stdlib Tkinter + one pure-Python dependency (`lark`). Runs on offline, admin-locked lab PCs with no browser, no GPU, no Electron, and **no network**: deployment is either a two-wheel offline wheelhouse (our wheel + Lark's, installed with `pip install --no-index --find-links`) or a single-file `.pyz` zipapp that bundles Lark and runs on the machine's standard Python with no pip at all. Both artifacts are built by `tools/build_release.py` and verified offline (`--no-index`) in CI. This is a genuine constraint in semiconductor environments and quietly disqualifies most modern dashboard stacks.

---

## 2. Architectural Blueprint

### 2.1 Pattern: MVVM-lite for Tkinter

MVVM adapted to Tkinter's realities. The ViewModel owns all presentation *state* (current filter, selected failure, zoom window) and exposes it through a tiny observer/pub-sub — Views subscribe and re-render; they never hold state or business logic. This keeps everything below the View layer unit-testable **without a display**, which matters for CI.

```
┌────────────────────────── main thread (Tk) ──────────────────────────┐
│  VIEW        main_window · failure_table · signature_panel ·        │
│              waveform_canvas          (imports tkinter, NEVER lark) │
│                  ↑ subscribe/render        ↓ user commands          │
│  VIEWMODEL   app_viewmodel + events.py pub-sub                      │
│              (filter state, selection, zoom window — pure Python)   │
│                  ↑ immutable TestRun       ↓ load/cancel commands   │
├───────────── queue.Queue + root.after(50) pump ──────────────────────┤
│  SERVICES    ParserWorker thread (background.py)                    │
│  MODEL       entities.py · signature.py · waveform.py               │
│  PARSING     chunked_reader → Lark(atelog.lark) → transformer       │
│              (imports lark, NEVER tkinter)                          │
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
│   │   ├── validator.py          # post-parse semantic validation (Ph1 M7)
│   │   └── chunked_reader.py     # line-oriented framing scanner for large files
│   ├── model/
│   │   ├── entities.py           # frozen dataclasses (§4) — the AST/domain types
│   │   ├── signature.py          # fail-signature clustering engine
│   │   └── waveform.py           # WaveformSeries builder (value-change encoding)
│   ├── viewmodel/
│   │   ├── events.py             # minimal observer pub-sub (~30 lines)
│   │   └── app_viewmodel.py      # observable app state + commands
│   ├── services/
│   │   ├── background.py         # ParserWorker thread + message queue
│   │   └── file_service.py       # open/recent-files/export
│   └── view/
│       ├── main_window.py        # layout, menu, status bar, queue pump
│       ├── failure_table.py      # searchable ttk.Treeview
│       ├── signature_panel.py    # ranked cluster summary
│       └── waveform_canvas.py    # custom Canvas renderer (§6.2)
├── sample_logs/                  # golden .atelog files
├── tools/gen_log.py              # synthetic log generator (perf/test corpus)
├── tests/
├── docs/                         # ARCHITECTURE.md · LOG_FORMAT_SPEC.md · ROADMAP.md
└── pyproject.toml                # deps: lark; dev: pytest, mypy
```

### 2.3 Hard separation rules (enforced, not aspirational)

- **Import firewall:** nothing under `parsing/`, `model/`, `viewmodel/`, `services/` imports `tkinter`; nothing under `view/` imports `lark`. Enforced by an **AST-based test**: walk every module file in each package with `ast.parse` and reject any `import`/`from` statement crossing the boundary — this catches conditional and function-local imports and is immune to test-ordering contamination, both of which defeat a `sys.modules` probe. A `sys.modules` check after importing each package remains as a supplementary smoke test only.
- **Threading contract:** only the main thread touches Tk widgets (Tkinter/Tcl is not thread-safe; the worker never calls Tk, not even `root.after`). The `ParserWorker` thread communicates exclusively by putting frozen dataclass messages (`ParseProgress` / `ParseComplete` / `ParseFailed`) on a `queue.Queue`, drained by a `root.after(50, pump)` loop in bounded batches. Cancellation via `threading.Event` checked between chunks. Every message carries a **job-generation ID**; the pump discards messages from superseded jobs — the stale-`ParseComplete`-after-cancel/reload race is the one logical race message-passing alone doesn't remove.
- **Immutability at the boundary:** everything crossing the thread boundary is a frozen dataclass whose containers are tuples all the way down — never dict/Mapping, because a frozen dataclass holding a dict is only *shallowly* immutable. That makes hand-off thread-safe by construction and the payload picklable. If the thread is ever swapped for a `multiprocessing` worker, the **message schema is reusable** as-is, but the transport and lifecycle change: `multiprocessing.Queue`, process start/join, and cancellation via a sentinel or event proxy instead of a shared `threading.Event`.

---

## 3. Log File Specification & Lark Grammar

### 3.1 ADF-1: the ATE Datalog Format (`.atelog`)

Line-oriented text, deliberately hybridizing **ATDF** (header/metadata records) and **VCD** (timescale + per-pin state changes), so future real-format adapters are thin. Logic states use the VCD/IEEE-1364 alphabet `0 1 X Z L H`. An expected value of `X` is a **mask** (don't-compare) per industry convention. Keywords are reserved words (a pin may not be named `PIN`, `END`, etc.). `//` comments are allowed both trailing a record and as standalone (optionally indented) lines, anywhere after the magic line — except on metadata lines, where `//` is literal value text.

**Coordinate semantics:** `CYCLE` vector numbers and `T=` timestamps are guaranteed unique and monotonic only **within one test-block invocation** — real testers restart pattern time per pattern, and ADF-1 makes no cross-block guarantee. Block *names* are not unique either: the same pattern may legally appear several times in one log (retest loops, corner re-runs), so block identity is *(name, occurrence)* — `BlockId` in §4, occurrence assigned in document order. The run-wide address of a cycle is therefore always *(BlockId, vector, time)* — modeled as `VectorLocation` in §4 — never a bare vector, timestamp, pin name, or block name.

**Positioning — what ADF-1 models (real-data reality check):** ADF-1 is the *FA re-test (capture-all) datalog* — the verbose log produced when a failing unit is re-run in engineering mode with full fail capture — **not** a production datalog. Production datalogs (STDF/ATDF `FTR` records) are failure-sparse: failing vector, failing pins, and captured states *at failing strobes only*, capped by tester fail-memory depth; drive/expect/passing-cycle states live in the pattern file (STIL/WGL/.avc), not the log. No tool can draw a full waveform from a production log alone. The suite therefore defines three ingestion fidelity tiers, and the IR + renderer degrade gracefully across them:

| Tier | Source | Failure table & clustering | Waveform view |
|---|---|---|---|
| **A** | FA re-test log (ADF-1) or VCD bench capture | full | full differential waveform |
| **B** | Production STDF/ATDF (`FTR` records) | full | sparse **fail-capture strip**: states at failing strobes only; expected inferred from failed-low/failed-high capture codes |
| **C** | Tier B + STIL (IEEE 1450) pattern file | full | reconstructed expected waveform with fail-capture overlay |

Phases 1–4 implement Tier A; Tiers B/C are adapter work on the future roadmap. This tiering is documented in `docs/LOG_FORMAT_SPEC.md` — it is the honest answer to "does this work with real tester logs?"

```
#ATELOG v1.0
LOT: K78842A-07B
WAFER: 14
DEVICE: S32K344_QFN48
TESTER: UFLEX-BLR-22
PROGRAM: s32k_prod_r3.1
DATE: 2026-07-11T03:14:22
TIMESCALE: 1ns

PINDEF CLK IN
PINDEF RST_N IN
PINDEF DQ0 IO
PINDEF DQ1 IO

TESTBLOCK mbist_march_c
CYCLE 1200 T=1200000
PIN CLK DRV 1
PIN RST_N DRV 1
PIN DQ0 EXP 1 GOT 1 PASS
PIN DQ1 EXP 0 GOT 1 FAIL      // SA1 candidate
END CYCLE
CYCLE 1201 T=1201000
PIN CLK DRV 0
PIN DQ0 EXP 0 GOT 0 PASS
PIN DQ1 EXP 0 GOT 1 FAIL
END CYCLE
FAILSUMMARY 2 VECTORS 1200,1201
END TESTBLOCK
END LOG
```

Contains everything the requirements demand: timestamps (`T=` in `TIMESCALE` units), failed pin names, failed vector numbers (`CYCLE n`), expected-vs-actual states, and per-compare PASS/FAIL flags, plus a per-block `FAILSUMMARY` carrying two independently checkable claims: the count is the number of failing **compare lines** in the block (pin-granular — one vector with three failing pins contributes three), cross-checked against the observed `FAIL` records; and the `VECTORS` list enumerates the distinct failing vectors, cross-checked against the set of vectors having at least one `FAIL`. Both mismatches are recoverable warnings (Phase 1 M7). Real datalogs contain such inconsistencies; we detect them.

### 3.2 Complete Lark grammar (`atelog.lark`)

```lark
// ADF-1 grammar — parser="lalr", lexer="contextual" (see notes). The
// document rule is composed FROM the chunk-mode fragment rules (§6.1), and
// all six entry points are exposed via Lark's start=[...] — the strict and
// chunked paths share every production, so they cannot drift apart.
document: prologue testblock+ end_log

prologue: header pindef+
header: MAGIC NEWLINE meta+
meta: META_KEY VALUE NEWLINE
pindef: "PINDEF" PIN_NAME DIRECTION NEWLINE

testblock: testblock_header cycle_batch block_trailer
testblock_header: "TESTBLOCK" BLOCK_ID NEWLINE
cycle_batch: cycle+
block_trailer: failsummary? "END" "TESTBLOCK" NEWLINE
end_log: "END" "LOG" NEWLINE?

cycle: "CYCLE" INT TIME_FIELD NEWLINE pin_event+ "END" "CYCLE" NEWLINE
pin_event: drive | compare
drive: "PIN" PIN_NAME "DRV" STATE NEWLINE
compare: "PIN" PIN_NAME "EXP" STATE "GOT" STATE RESULT NEWLINE
failsummary: "FAILSUMMARY" INT "VECTORS" vector_list NEWLINE
vector_list: INT ("," INT)*

MAGIC: /#ATELOG v\d+\.\d+/
META_KEY: /(LOT|WAFER|DEVICE|TESTER|PROGRAM|DATE|TIMESCALE):/
VALUE: /[^\r\n]+/
TIME_FIELD: /T=\d+/
DIRECTION: "IN" | "OUT" | "IO"
RESULT: "PASS" | "FAIL"
STATE: /[01XZLH]/
PIN_NAME: /[A-Za-z_][A-Za-z0-9_\[\]]*/
BLOCK_ID: /[a-z][a-z0-9_]*/
NEWLINE: /((\/\/[^\r\n]*)?\r?\n[ \t]*)+/

%import common.INT
%ignore /[ \t]+/
```

Instantiation:

```python
STARTS = ["document", "prologue", "testblock_header",
          "cycle_batch", "block_trailer", "end_log"]
parser = Lark(grammar, parser="lalr", lexer="contextual", start=STARTS,
              propagate_positions=True, maybe_placeholders=False, cache=True)
# strict path: parser.parse(text, start="document")
# chunk path:  parser.parse(frame_text, start=frame.fragment_rule)
```

**Why these choices (the senior-level details):**
- `parser="lalr"` — O(n) parsing, roughly an order of magnitude faster than Earley; mandatory for multi-hundred-MB datalogs.
- `lexer="contextual"` — resolves deliberate terminal collisions per parse state: `STATE /[01XZLH]/` vs `INT` ("1" is both), `VALUE` vs everything, `DIRECTION "IN"` vs `PIN_NAME`. The contextual lexer only considers terminals *expected in the current LALR state*, so `EXP 1` lexes `1` as STATE while `CYCLE 1200` lexes as INT. With the standard lexer this grammar would be unbuildable.
- **Comments ride on `NEWLINE`** — the terminal `/((\/\/[^\r\n]*)?\r?\n[ \t]*)+/` absorbs runs of line terminators *including* comment-only lines (indented or not) and blank lines, and handles CRLF (Windows tester dumps). A trailing comment is the optional group before its own newline. This is deliberate: the naive `%ignore COMMENT` approach only supports trailing comments — a standalone comment line leaves an orphan `NEWLINE` token in a parser state that expects a construct, which LALR rejects. Consuming comment and newline as one token makes the orphan impossible. It also removes the need for any `VALUE` priority: on metadata lines the contextual lexer expects only `VALUE`, so `//` there is literal text. One limit: no comment may precede the `#ATELOG` magic (it must be line 1 regardless).
- `propagate_positions=True` — every dataclass carries its source line; parse errors and table rows link back to the raw log line (FA engineers always want the raw line).
- `cache=True` — caches the generated LALR table; near-instant startup.
- **Multi-start grammar** — the six entry points (`document` plus the five fragment rules) live in one grammar file and one `Lark` instance via `start=[...]`. Because `document` is *built from* the fragments rather than duplicated alongside them, an edit to any production changes both parse paths at once — the strict/chunked drift bug is structurally impossible, and a test parses every emitted frame independently with its fragment rule.
- **Grammar loaded via `importlib.resources`** — `atelog.lark` is declared as package data in `pyproject.toml` and read with `importlib.resources.files("ate_fa_suite.parsing.grammar")`, never a `__file__`-relative path. This is what keeps it working from an installed wheel or zipapp; an editable install reads the source tree directly and therefore proves nothing about packaging (see Verification).

---

## 4. Reusable Data Structures (`model/entities.py`)

All frozen + slotted: immutable (thread-safe hand-off), memory-lean (slots matter at 10⁶ cycles), hashable where used as keys. Requires Python ≥ 3.10 (dataclass `slots=True` and `bisect`'s `key=` both land there; `pyproject.toml` pins it).

```python
from __future__ import annotations
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import Enum


class PinDirection(Enum):
    IN = "IN"; OUT = "OUT"; IO = "IO"

class LogicState(Enum):
    LOW = "0"; HIGH = "1"; UNKNOWN = "X"; HIGH_Z = "Z"
    WEAK_LOW = "L"; WEAK_HIGH = "H"

class FailCategory(Enum):
    """Single-vector evidence → *candidate* categories (honest FA language:
    one vector can suggest, not prove, a stuck-at)."""
    SA0_CANDIDATE = "stuck-at-0 candidate"    # expected 1, captured 0
    SA1_CANDIDATE = "stuck-at-1 candidate"    # expected 0, captured 1
    FLOATING = "floating / contention"        # captured X
    OPEN_TRISTATE = "open / tri-state"        # captured Z
    WEAK_DRIVE = "weak drive"                 # captured L or H
    OTHER = "other mismatch"                  # genuine fail, no canonical bucket
    INCONSISTENT = "inconsistent record"      # FAIL flag contradicts the states

    @staticmethod
    def classify(expected: LogicState, actual: LogicState,
                 failed: bool) -> FailCategory | None:
        """Authority policy: the tester's PASS/FAIL flag decides failure
        *membership* (it is what the comparator recorded — §6.3 ground truth);
        the states are evidence only for the failure's *kind*. Contradictions
        are surfaced as INCONSISTENT, never silently reinterpreted. Returns
        None for non-failing compares (masked compares pass by definition)."""
        if not failed:
            return None
        if expected is LogicState.UNKNOWN:    # masked compare cannot fail
            return FailCategory.INCONSISTENT
        if expected is actual:                # FAIL flag, yet states agree
            return FailCategory.INCONSISTENT
        if actual is LogicState.UNKNOWN:
            return FailCategory.FLOATING
        if actual is LogicState.HIGH_Z:
            return FailCategory.OPEN_TRISTATE
        if actual in (LogicState.WEAK_LOW, LogicState.WEAK_HIGH):
            return FailCategory.WEAK_DRIVE
        if expected is LogicState.HIGH and actual is LogicState.LOW:
            return FailCategory.SA0_CANDIDATE
        if expected is LogicState.LOW and actual is LogicState.HIGH:
            return FailCategory.SA1_CANDIDATE
        return FailCategory.OTHER   # e.g. expected Z/L/H, captured strong 0/1


@dataclass(frozen=True, slots=True)
class LogHeader:
    lot: str; wafer: str; device: str; tester: str
    program: str; date: str; timescale_ns: float

class WaveShape(Enum):
    NRZ = "NRZ"; RZ = "RZ"; RO = "RO"; SBC = "SBC"

@dataclass(frozen=True, slots=True)
class PinTiming:
    """Optional intra-cycle timing for one pin (a timeset excerpt). Offsets
    are in native timescale units from cycle start; None = unknown. A pin
    with no PinTiming at all renders as the NRZ idealization (§6.3). This is
    the IR hook a future ADF-1 `FORMAT` line or a STIL/VCD adapter fills."""
    shape: WaveShape = WaveShape.NRZ
    drive_on: int | None = None        # d1: drive edge offset
    drive_off: int | None = None       # d2: return edge (RZ/RO/SBC)
    strobe: int | None = None          # compare strobe offset (edge strobe)
    strobe_window: int | None = None   # window-strobe width, if windowed

@dataclass(frozen=True, slots=True)
class PinDef:
    name: str; direction: PinDirection
    timing: PinTiming | None = None    # run-default; absent ⇒ NRZ idealization

@dataclass(frozen=True, slots=True)
class TimingSet:
    """Named per-pin timing, selectable per cycle — real tester/STIL patterns
    switch timesets mid-block. Resolution chain (§6.3): Cycle.timeset →
    TimingSet entry for the pin → PinDef.timing → NRZ idealization."""
    name: str
    entries: tuple[tuple[str, PinTiming], ...]   # (pin, timing), sorted

    def __post_init__(self) -> None:
        pins = [p for p, _ in self.entries]
        if pins != sorted(pins) or len(set(pins)) != len(pins):
            raise ValueError("timing entries must be sorted, unique by pin")

@dataclass(frozen=True, slots=True)
class DriveEvent:
    pin: str; state: LogicState

@dataclass(frozen=True, slots=True)
class CompareEvent:
    pin: str; expected: LogicState; actual: LogicState; passed: bool

@dataclass(frozen=True, slots=True)
class Cycle:
    vector: int; time: int                    # time in native timescale units
    drives: tuple[DriveEvent, ...]
    compares: tuple[CompareEvent, ...]
    src_line: int
    timeset: str | None = None                # TimingSet reference (§6.3 chain)

@dataclass(frozen=True, slots=True, order=True)
class BlockId:
    """Identity of one test-block *invocation*. Real flows legally re-run the
    same pattern (retest loops, multi-corner runs), so the name alone is not
    unique; the assembler numbers repeats in document order. `order=True`
    (lexicographic over (name, occurrence)) makes `WaveKey` tuples natively
    sortable/bisectable — required by the sorted wave collections."""
    name: str
    occurrence: int                       # 1-based, document order

    def label(self) -> str:               # UI display: "mbist_march_c" / "…#2"
        return self.name if self.occurrence == 1 \
            else f"{self.name}#{self.occurrence}"

@dataclass(frozen=True, slots=True)
class VectorLocation:
    """Unambiguous run-wide address of one cycle. Vector numbers and T=
    timestamps are unique/monotonic only within their block invocation
    (§3.1), so every selection, waveform lookup, and cross-reference
    carries the full triple."""
    block: BlockId; vector: int; time: int    # time is invocation-local

@dataclass(frozen=True, slots=True)
class FailureEvent:
    """Flattened row for the failure table: one per failing compare."""
    location: VectorLocation; pin: str
    strobe_time: int    # resolved at assembly via the §6.3 timing chain
                        # (location.time + strobe offset; == location.time
                        # when no timing is known) — the renderer never
                        # evaluates the chain itself
    cycle_period: int   # local period, resolved at assembly from the
                        # neighboring cycle's time delta (cycles are then
                        # discarded); drives mismatch-band width (§6.2)
    expected: LogicState; actual: LogicState
    category: FailCategory; src_line: int
    strobe_window: int | None = None   # resolved PinTiming, if window-strobed

@dataclass(frozen=True, slots=True)
class FailSignature:
    block: BlockId          # buckets only comparable within one invocation
    pin_group: str          # bus-collapsed, e.g. "DQ[*]"
    category: FailCategory
    vector_bucket: int      # vector // SIGNATURE_BUCKET (default 100)

@dataclass(frozen=True, slots=True)
class SignatureCluster:
    signature: FailSignature; count: int; share: float
    members: tuple[FailureEvent, ...]

WaveKey = tuple[BlockId, str]   # (block invocation, pin) — neither a pin name
                                # nor a block name alone is unique run-wide

@dataclass(frozen=True, slots=True)
class WaveformSegment:
    """One contiguous *retained* interval of a pin's history. Inside
    [t_start, t_end] the value-change semantics apply (no transition ⇒ state
    held). Outside every segment the data was *discarded* — a different fact
    from 'held', and it must never render as a waveform."""
    t_start: int; t_end: int              # inclusive, block-local
    times: tuple[int, ...]                # ascending; invariant: times[0] == t_start
    states: tuple[LogicState, ...]        # parallel to times

    def __post_init__(self) -> None:      # invariants live in the model —
        if not self.times or len(self.times) != len(self.states):
            raise ValueError("segment arrays empty or not parallel")
        if (self.t_start > self.t_end or self.times[0] != self.t_start
                or self.times[-1] > self.t_end):
            raise ValueError("segment bounds violated")
        if any(a >= b for a, b in zip(self.times, self.times[1:])):
            raise ValueError("times not strictly ascending")

    def state_at(self, t: int) -> LogicState:
        return self.states[bisect_right(self.times, t) - 1]   # t within bounds

    def clipped(self, t0: int, t1: int) -> WaveformSegment | None:
        """Intersection with [t0, t1]; carry-in state re-anchored so the
        times[0] == t_start invariant survives clipping."""
        if t1 < self.t_start or t0 > self.t_end:
            return None
        start, end = max(t0, self.t_start), min(t1, self.t_end)
        lo = bisect_right(self.times, start) - 1
        hi = bisect_right(self.times, end)
        return WaveformSegment(start, end,
                               (start,) + self.times[lo + 1:hi],
                               self.states[lo:hi])

@dataclass(frozen=True, slots=True)
class WaveformSeries:
    """A pin's retained history within one block invocation: disjoint
    ascending segments (the builder merges overlapping/adjacent ±W retention
    windows). The gaps between segments are structural no-data — never
    interpolated across."""
    block: BlockId
    pin: str
    segments: tuple[WaveformSegment, ...]

    def __post_init__(self) -> None:
        for a, b in zip(self.segments, self.segments[1:]):
            if a.t_end >= b.t_start:
                raise ValueError("segments must be sorted and disjoint")

    def state_at(self, t: int) -> LogicState | None:
        """None ⇒ not retained at t: render as no-data, never extrapolate."""
        i = bisect_right(self.segments, t, key=lambda s: s.t_start) - 1
        if i < 0 or t > self.segments[i].t_end:
            return None
        return self.segments[i].state_at(t)

    def window(self, t0: int, t1: int) -> tuple[WaveformSegment, ...]:
        # bisect to the overlapping segment range: O(log n) in segment count
        lo = bisect_left(self.segments, t0, key=lambda s: s.t_end)
        hi = bisect_right(self.segments, t1, key=lambda s: s.t_start)
        return tuple(c for c in (s.clipped(t0, t1)
                                 for s in self.segments[lo:hi])
                     if c is not None)   # overlap guaranteed; filter for typing

@dataclass(frozen=True, slots=True)
class TestBlockResult:
    id: BlockId; first_vector: int; last_vector: int
    fail_count: int; declared_fail_vectors: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class TestRun:
    """Root aggregate handed from parser thread to the ViewModel."""
    header: LogHeader
    pins: tuple[PinDef, ...]
    blocks: tuple[TestBlockResult, ...]
    failures: tuple[FailureEvent, ...]
    # Three provenance-separated wave collections — never merged: DRV is
    # programmed stimulus (continuously known by definition), GOT is a
    # comparator observation (known only at strobes). Retained fail windows
    # only; each tuple sorted by WaveKey (BlockId is order=True, so keys
    # compare naturally) and looked up via the wave_for() bisect helper in
    # model/waveform.py. Tuple-backed rather than a Mapping because a frozen
    # dataclass holding a dict is only shallowly immutable, and
    # MappingProxyType is unpicklable.
    driven_waves: tuple[WaveformSeries, ...]      # programmed stimulus (DRV)
    expected_waves: tuple[WaveformSeries, ...]    # what the program demanded
    captured_waves: tuple[WaveformSeries, ...]    # comparator captures (GOT)
    timing_sets: tuple[TimingSet, ...] = ()       # per-cycle timing (§6.3)
    warnings: tuple[str, ...] = ()    # log inconsistencies, each with source line:
                                      # truncation salvage (Ph1 M6), FAILSUMMARY
                                      # count mismatches, INCONSISTENT records,
                                      # non-masked PASS with disagreeing states

    def __post_init__(self) -> None:
        for waves in (self.driven_waves, self.expected_waves,
                      self.captured_waves):
            keys = [(w.block, w.pin) for w in waves]
            if keys != sorted(keys) or len(set(keys)) != len(keys):
                raise ValueError("wave tuples must be sorted, unique by WaveKey")
        names = [ts.name for ts in self.timing_sets]
        if len(set(names)) != len(names):
            raise ValueError("timing-set names must be unique")


# --- worker → UI queue messages --------------------------------------------
# Every message carries the job-generation ID from the §2.3 threading
# contract: the pump discards any message whose job_id is not the current
# job's, killing the stale-result-after-cancel/reload race by construction.

@dataclass(frozen=True, slots=True)
class ParseProgress:
    job_id: int
    bytes_done: int; bytes_total: int; cycles: int; fails: int

@dataclass(frozen=True, slots=True)
class ParseComplete:
    job_id: int
    run: TestRun; elapsed_s: float

@dataclass(frozen=True, slots=True)
class ParseFailed:
    job_id: int
    line: int; column: int; message: str; context: str
```

---

## 5. Implementation Roadmap — 4 Phases

### Phase 1 — Grammar & Parsing Engine (~4–5 days)
**Milestones**
1. Freeze the ADF-1 spec; author 3 golden logs (`clean_pass`, `multi_fail`, `truncated`).
2. Grammar parses the two well-formed goldens (`Lark(...).parse(text, start="document")` — no transformer yet). The strict `document` rule requires complete blocks and `END LOG`, so `truncated` is an **expected-failure fixture** at this layer: its test asserts `UnexpectedToken`/`UnexpectedEOF` pointing at the break, not a successful parse.
3. `AteLogTransformer` produces fully populated dataclasses; every grammar rule covered.
4. `tools/gen_log.py --cycles 1000000 --pins 16 --fail-rate 0.0005 --seed 42` generates a deterministic perf corpus.
5. Error mapping: `UnexpectedToken/UnexpectedCharacters` → `ParseFailed` with line, column, and the offending source line.
6. Prefix salvage — a **chunking-layer** behavior, never a grammar behavior: the `chunked_reader` framing scanner (§6.1 item 2 — line-oriented state machine; CRLF-safe; frame markers recognized by leading token so comment text can't fake a boundary; every frame tagged with block name and absolute line/byte offsets) feeds only complete frames to the segment grammars. On truncation the scanner emits an explicit truncated-tail frame: every complete cycle before the break is recovered, block identity and `FAILSUMMARY` cross-checks are preserved, and the tail becomes an entry in `TestRun.warnings` (§4) on a partial result delivered as `ParseComplete`, never a crash. Tests: CRLF golden through the chunked path; a log whose trailing comment contains the text `END CYCLE`; the lossless-framing reassembly property test; every emitted frame parsed independently with its fragment start rule (§3.2 multi-start); blank/comment lines immediately before and after a batch boundary (trivia attaches to the preceding frame — §6.1); the truncated golden asserting exact recovered-cycle count and the warning's absolute line number.
7. Semantic validation (`parsing/validator.py`) — the grammar accepts structurally valid but semantically wrong logs, so a post-assembly pass enforces the spec under a **two-tier policy**. *Fatal → `ParseFailed`*: unsupported `#ATELOG` major version; required metadata violated (each of the seven keys exactly once, `TIMESCALE` parseable as a valid unit); non-strictly-increasing vector or time within a block invocation — the waveform bisects depend on sorted transitions, so this can never be a warning. *Recoverable → `TestRun.warnings` + a deterministic rule*: duplicate `PINDEF` (first wins); pin event on an undeclared pin (auto-declare `IO`, warn); duplicate pin event for one pin within one cycle (first wins); reserved word appearing as an identifier where the contextual lexer happened to accept it; `FAILSUMMARY` mismatches — both checks: declared count vs observed failing compare lines (pin-granular, §3.1), and declared `VECTORS` vs the observed set of vectors with ≥1 `FAIL`. `WaveformSegment`/`WaveformSeries`/`TimingSet` structural invariants are enforced by the model itself — `__post_init__` validation raising `ValueError` (§4), because `assert` statements vanish under `python -O` and builder-side checks don't guard alternate construction paths — backed by a property test. Tests: one malformed golden per rule, asserting tier, message, and source line.

**Edge cases:** CRLF vs LF; blank lines/trailing whitespace; standalone and indented comment lines between any records (absorbed by the `NEWLINE` terminal — covered by a golden with comments in every legal position); repeated `TESTBLOCK` names (**legal** — retest loops and corner re-runs; the assembler numbers each invocation into a distinct `BlockId`, and a golden contains the same pattern twice to prove failures/waves/signatures stay separate); truncated file mid-cycle (real tester dumps truncate — the strict grammar rejects it by design; salvage is milestone 6's chunked path); duplicate `PINDEF`; compare on undeclared pin (semantic check *after* parse, not in grammar); zero-failure log; `FAILSUMMARY` count disagreeing with actual FAIL lines (flag as log inconsistency, don't crash).

**Verification:** pytest golden tests; property test — generator emits N seeded failures → parse → exactly N `FailureEvent`s with matching vectors; record a parse-throughput baseline on the 1M-cycle corpus (budget, then optimize via §6.1 chunking if needed).

### Phase 2 — Domain Logic (~3–4 days)
**Milestones**
1. `FailCategory.classify` per the authority policy: the PASS/FAIL flag decides failure membership, states decide the kind. Non-failing → `None`; contradictory FAIL records (equal states, or masked expected `X`) → `INCONSISTENT` plus a `TestRun.warnings` entry; off-bucket mismatches (expected `Z`/`L`/`H`, captured `0`/`1`) → `OTHER`; stuck-at candidates only for strong `0`/`1` mismatches. Symmetrically, a non-masked `PASS` line with disagreeing states is a warning, not a failure. Exhaustive truth-table unit test over all (expected, actual, flag) combinations.
2. Signature engine: bus collapsing (`DQ3` → `DQ[*]`), vector bucketing, cluster ranking by share.
3. `WaveformSeries` builder: cycles → value-change encoding, **three provenance-separated series kinds** per `WaveKey` — driven (from `DRV`), expected and captured (from compares) — never merged (§6.3 stimulus-is-not-observation); ±W retention windows emitted as disjoint `WaveformSegment`s, with overlapping/adjacent windows merged and the `times[0] == t_start` invariant enforced; resolves the §6.3 timing chain at assembly — drive edges and strobe placement baked into transition times, `FailureEvent.strobe_time` populated — after which `Cycle` objects are discarded. Tests: two distant failures → two segments with `state_at` returning `None` in the gap; overlapping windows → one merged segment; `clipped()` boundary cases; an IO pin alternating drive/compare yields disjoint driven and captured series with no cross-contamination; `TestRun.__post_init__` rejection cases (unsorted wave tuples, duplicate `WaveKey`s, duplicate timing-set names).
4. Composable filter predicates (pin substring, vector range, block, category) shared by table search and signature-panel click-to-filter.
5. Exports: failures CSV + plain-text FA summary ("8D-ready" paragraph per top cluster).

**Edge cases:** drive-only pins (CLK — a driven series only; nothing is synthesized into expected or captured); failure at first/last vector (window clamping); vector numbers and timestamps reused across block invocations — including re-runs of the *same* pattern (every selection carries a `VectorLocation`, every wave lookup a `WaveKey`; bare vectors, times, pin names, or block *names* are never used as run-wide keys); all-X pins; single-failure log (cluster share = 100%); `INCONSISTENT` records (still shown in the failure table as evidence, clustered under their own signature, surfaced in the status-bar warnings — never folded into stuck-at counts).

**Verification:** pure unit tests, zero display required; clustering snapshot tests with fixed seeds; window-extraction boundary tests.

### Phase 3 — GUI Shell (~4–5 days)
**Milestones**
1. Main window skeleton, menu, Open dialog, recent files.
2. `ParserWorker` + queue + `after(50)` pump + progress bar + cancel button.
3. Failure table (`ttk.Treeview`): column sort, 300 ms-debounced search box, batched inserts (500 rows/tick), paging above 10k rows.
4. Signature panel: ranked clusters; click applies the corresponding filter.
5. Status bar: file, parse time, cycle/fail counts, inconsistency warnings.

**Edge cases:** opening a second file mid-parse (cancel or queue); cancel responsiveness (< 200 ms — worker checks `Event` between chunks); parse-error dialog showing the offending line; empty result set; minimum window size / resize behavior.

**Verification:** ViewModel unit tests headless (no Tk); one smoke test instantiating real Tk on Windows (load golden → assert table row count == failure count); manual checklist in `docs/ROADMAP.md`.

### Phase 4 — Custom Canvas Waveform Renderer (~5–6 days)
**Milestones**
1. Static single-pin lane: square-wave polyline from one `WaveformSeries` (driven or captured — provenance decides the style, §6.3).
2. Expected ghost overlay + mismatch bands: a narrow red band at each failing strobe for the lane's `(block, pin)`, derived from `FailureEvent`s (§6.2), drawn behind both lanes.
3. Multi-lane layout, pin-label gutter, time-axis ruler with tick auto-density.
4. Cursor-anchored mouse-wheel zoom, drag pan, "zoom to fail" command.
5. Hover crosshair with time + per-pin state readout.
6. Virtualization: viewport culling + sub-pixel transition coalescing (§6.2).
7. Selection sync: table row ↔ canvas highlight, both directions.
8. Honest-observability rendering (§6.3): strobe ticks on compare states, placed at assembly-resolved strobe instants (`FailureEvent.strobe_time` and captured-series transition times — the renderer evaluates no timing chain), dimmed held-state inference, canvas legend noting the NRZ idealization.
9. Optional inferred-disagreement overlay: interval-XOR of held expected/actual states within retained segments only; hatched amber, legend-labeled "inferred", off by default. Verification: with the overlay off, mismatch items appear only at `FailureEvent` strobe times; masked-compare regions never produce a band in either mode.

**Edge cases:** single-transition window and all-same-state pin (both resolved by the synthesized coverage endpoints in §6.2 — carry-in re-anchored to segment start, final state held to the segment's coverage edge); retention gaps between segments and empty windows (no-data hatch, no `create_line` call — never an interpolated line); `X` drawn as gray hatched block, `Z` as dashed mid-level line (industry convention); >20 failing pins (vertical lane scroll); rapid zoom event storms (debounce via `after_idle`); Windows DPI scaling (`tk scaling`); sparse-capture input (Tier B — render fail-capture strip, never fabricate continuous lanes).

**Verification:** canvas introspection tests — render a known series, assert item counts/coords via `canvas.find_withtag`/`coords`; explicit coverage tests: a flat fully-retained signal yields a two-point line spanning the viewport; a series with two retained segments yields exactly two `wave` lines with hatch items between them and **no line item crossing the gap**; a segment ending mid-screen yields hatch from its coverage edge to the canvas edge; an empty window yields hatch only, zero `wave` items; a segment lying wholly in the floor/ceil-widened margin outside the float viewport renders nothing (no reversed lines, no cursor regression); a pixel column coalescing several transitions must exit at the *final* transition's level — the run following a busy column sits at the last state's y, with exactly one busy marker on the column; perf gate: full redraw < 50 ms at 200 visible transitions × 12 lanes; visual golden checklist.

**Future work (documented, deliberately out of scope):** Tier B adapter — STDF/ATDF `FTR` ingestion (real production data: full table + clustering, fail-capture-strip waveform); Tier C adapter — STIL (IEEE 1450) pattern parsing via a second Lark grammar to reconstruct full expected waveforms; VCD front-end; shmoo-plot panel; similarity-based signature clustering.

---

## 6. Technical Challenges & Solutions

### 6.1 Large logs without freezing Tkinter

1. **Never parse on the Tk thread.** `ParserWorker` thread; frozen-dataclass messages over `queue.Queue`; UI drains via `root.after(50, pump)`. The GIL is not a problem for *responsiveness* — the UI thread needs only tiny slices — and because messages are picklable, tuple-backed frozen dataclasses, the **message schema is reusable** under a future `multiprocessing` worker for true parallelism; the transport and lifecycle (queue type, process start/join, cancellation mechanism) would change, the payloads would not.
2. **Chunked parsing via a raw-byte framing scanner.** `chunked_reader.py` is a small state machine, not a substring search — a naive `str.find("END CYCLE\n")` would miss CRLF records, false-match inside trailing `//` comment text, and lose block context. The scanner streams **raw bytes**, splitting on `\n` (the byte common to LF and CRLF endings), and every frame is an untouched slice of the original byte stream — original line endings included. That raw fidelity is what makes true byte offsets and byte-for-byte reassembly possible; frames are *never* normalized. Normalization exists only in the **classification view**: a decoded throwaway copy of each line (trailing `\r` tolerated) is inspected for its **leading token only**, so the frame markers `TESTBLOCK` / `CYCLE` / `END CYCLE` / `FAILSUMMARY` / `END TESTBLOCK` / `END LOG` can never be faked by comment or value text. Frames are handed to Lark decoded but with their original endings intact — the grammar's `NEWLINE` terminal accepts both, so the parser needed no normalization in the first place. **Frame-boundary ownership rule:** comment and blank lines attach to the *preceding* frame — the scanner cuts a batch immediately before the next marker line — because the segment grammars cannot accept leading trivia (a frame must start with its marker token; a leading comment line would lex to an orphan `NEWLINE`). Verified by a chunked-path test with blank/comment lines immediately before and after a ~5k-cycle batch boundary. It emits typed frames: a prologue frame (header + pindefs), a **block-start frame** for each `TESTBLOCK` line (parsed by a `testblock_header` segment grammar; this is also where the assembler assigns the `BlockId` occurrence), cycle-batch frames (~5k complete cycles, each tagged with its enclosing `BlockId` and absolute start line/byte), block-trailer frames (`FAILSUMMARY` / `END TESTBLOCK`), an **end-log frame** (`END LOG`), and — when EOF arrives mid-record — an explicit truncated-tail frame. Every input line belongs to exactly one frame, and every frame type maps 1:1 to a fragment start rule in the grammar (§3.2 multi-start); two tests pin this down — byte-for-byte reassembly proves totality, and an independent parse of each emitted frame with its fragment rule proves the frames are self-contained. Each frame is parsed with its matching fragment start rule (`cycle_batch` for batches; `prologue`, `testblock_header`, `block_trailer`, `end_log` for the rest — §3.2 multi-start, one shared grammar); Lark's frame-relative error positions are rebased by the frame's start line so all reporting stays absolute. The scanner does no real parsing — Lark remains the single syntactic authority — and a lossless-framing property test asserts the concatenated frames reproduce the input byte-for-byte. This bounds peak memory, yields byte-accurate progress ticks and cancellation points, and is the truncation-salvage path (Phase 1 milestone 6): the truncated-tail frame becomes the partial-result warning, while the strict whole-file grammar deliberately rejects truncated input.
3. **Don't keep what FA doesn't need.** Two compressions: (a) *value-change encoding* — store transitions, not per-cycle states (a clock toggling for 10⁶ cycles is 2·10⁶ transitions, but a quiet data pin is ~2 entries); (b) *retention windows* — full waveform fidelity only within ±W vectors of any failure; elsewhere keep only counters. A 1 M-cycle log with 10 failures reduces to kilobytes of waveform data. Crucially, the gaps are **structural**: `WaveformSeries` stores disjoint retained `WaveformSegment`s (§4), so "discarded" is representable and distinct from "held" — `state_at` returns `None` in a gap and the renderer hatches it (§6.3), rather than carrying the last state across a region the tool chose to forget.
4. **UI-side hygiene.** Batched Treeview inserts, paging, debounced search — the table never receives 100k rows in one tick.

### 6.2 Dynamic pixel scaling on the waveform Canvas

World coordinates are *time in native timescale units*; the view is fully described by two numbers:

```python
class Viewport:
    t_left: float        # world time at canvas x=0
    ppu: float           # pixels per time-unit (zoom level)

    def x_of(self, t: float) -> int:
        return round((t - self.t_left) * self.ppu)

    def zoom(self, factor: float, x_mouse: int) -> None:
        # keep the time under the cursor stationary (anchor-point zoom)
        t_anchor = self.t_left + x_mouse / self.ppu
        self.ppu *= factor
        self.t_left = t_anchor - x_mouse / self.ppu
```

Lane geometry: `lane_top(i) = MARGIN + i * (LANE_H + GAP)`; logic-1 at `lane_top + PAD`, logic-0 at `lane_top + LANE_H - PAD`. Rendering loop per lane:

```python
def render(self, series, vp, canvas, lane):
    t0 = floor(vp.t_left)                # model API is integer-only: widen
    t1 = ceil(vp.t_left + canvas_w / vp.ppu)   # the query, never narrow it
    segs = series.window(t0, t1)         # clipped retained segments only
    cursor = 0                           # left edge of not-yet-painted x
    for seg in segs:
        if (vp.x_of(seg.t_end) < 0 or
                vp.x_of(seg.t_start) > self.canvas_w):
            continue        # in the widened query, but off the float viewport
        x_lo = min(max(vp.x_of(seg.t_start), 0), self.canvas_w)  # clamp BOTH
        x_hi = min(max(vp.x_of(seg.t_end), 0), self.canvas_w)    # ends, BOTH
        if x_lo > cursor:                                        # sides
            self.hatch(cursor, x_lo, lane)           # retention gap: no-data
        pts: list[int] = []
        px = py = None                   # column being coalesced, its FINAL y
        busy = False
        for t, state in zip(seg.times, seg.states):
            x = min(max(vp.x_of(t), 0), self.canvas_w)   # clamp to canvas
            y = self.y_of(lane, state)
            if x == px:
                busy, py = True, y       # coalesce, but keep the last state
                continue
            self._flush(pts, px, py, busy, lane)     # close previous column
            px, py, busy = x, y, False
        self._flush(pts, px, py, busy, lane)
        pts += [x_hi, pts[-1]]       # hold final state to the COVERAGE edge
        canvas.create_line(*pts, tags=("wave", series.pin))
        cursor = x_hi
    if cursor < self.canvas_w:
        self.hatch(cursor, self.canvas_w, lane)      # trailing gap / no data

def _flush(self, pts, px, py, busy, lane):
    """Close one pixel column: mark activity if it coalesced several
    transitions, and emit the edge to the column's FINAL state so the
    following horizontal run holds the true level."""
    if px is None:
        return                           # no column open yet
    if busy:
        self.mark_busy(px, lane)         # activity block over the column
    if pts: pts += [px, pts[-1], px, py] # horizontal run + edge to final y
    else:   pts += [px, py]
```

Key decisions:
- **Block-scoped viewport:** the canvas always renders exactly one test block; `t_left`/`ppu` are block-local coordinates (§3.1). Selecting a failure in another block swaps the series set (via its `VectorLocation` and `(block, pin)` wave keys) rather than scrolling — cross-block time is deliberately not a single axis, because no such axis exists in the log.
- **Cursor-anchored zoom** (above) is the difference between a tool that feels professional and one that doesn't — naive zoom that rescales about x=0 makes the failure fly off-screen.
- **Culling via `WaveformSeries.window`** — bisect at both levels: key-based bisect over segment bounds to find the overlapping segments, then bisect within each segment's transitions. Viewport extraction is O(log n + k + v) — logarithmic to *find* the k visible segments, then linear to materialize them and their v visible transitions, which is optimal since the output must be produced; render cost tracks *visible* transitions only.
- **Float/int boundary** — the viewport is float-valued (zoom math needs it), the model API is integer-only (native timescale units). The view owns the conversion: `floor(t0)` / `ceil(t1)` widen the query so edge data is never lost, and all sub-unit precision lives in pixel space via `x_of`. The widening has a display consequence: clipped segments can extend slightly past the float viewport, and at high zoom a sub-unit overhang is thousands of pixels — so every display coordinate — segment endpoints *and* transitions, both sides — is clamped to `[0, canvas_w]` at emit time, collapsing the overhang into the edge column where the coalescing logic already handles it, and a segment with no overlap of the real float viewport (present only because of the widening) is skipped outright rather than half-clamped into a reversed line or a backward-moving paint cursor. Segments never hold float times; `mypy --strict` enforces the boundary.
- **Synthesized coverage endpoints** — each segment's polyline is anchored at its *retained-coverage* edges, never at raw transition times. `clipped()` re-anchors the carry-in state to the segment start (fixing the flat-signal case — a single retained state still yields a two-point line, `create_line` needs two points — and avoiding the enormous negative Tk coordinates a long-quiet pin's real transition time would produce), and the final state is held to the segment's `t_end`, so a held signal reads as holding *only where data exists*. Every x-range not covered by a segment — retention gaps between failure windows, and windows with nothing retained at all — renders as the no-data hatch, never as an interpolated line.
- **Sub-pixel coalescing** — when several transitions land on one pixel column, draw a filled "activity" block instead of aliased garbage (the GTKWave trick) — while **keeping the column's final state**: the polyline exits the busy column at the last transition's level (`_flush` above), so the following horizontal run holds the true state. Dropping later transitions wholesale would leave the wave at a stale level until the next visible edge — wrong data, not just an aliasing artifact.
- **Expected lane** renders the same way at reduced intensity. The **mismatch strip is derived from `FailureEvent`s, never from waveform XOR**: each failing compare on the lane's `(block, pin)` contributes one narrow red band centered on its **resolved** strobe instant, `FailureEvent.strobe_time` — computed once at assembly via the §6.3 timing chain, so the renderer evaluates no chain; width = `strobe_window` when the resolved timing was window-strobed, else a fixed fraction of `FailureEvent.cycle_period` (both assembly-resolved fields — no cycle data is needed at render time), minimum 2 px — drawn behind both lanes. This is ground truth by construction — it inherits the §4 authority policy (the FAIL flag decides membership, so masked compares and `INCONSISTENT` records behave correctly) and stays valid even where waveform retention has gaps. An optional **inferred-disagreement overlay** — interval-XOR of the held expected/actual states, computed within retained segments only — can be toggled for spotting persistent disagreement; it renders in hatched amber, carries an "inferred" legend label, and is off by default, because held-state XOR between strobes is inference, not measurement (§6.3).
- **Redraw strategy:** full `delete("wave")` + redraw, throttled through `after_idle` so zoom event storms collapse into one repaint; integer-snapped coordinates keep edges crisp.

### 6.3 Waveform ground truth & honest rendering

A tester never observes a continuous waveform. For output pins it knows the comparator result *only at the strobe instant*; between strobes the true pin state is unknowable from any datalog. Naively drawing solid continuous "actual" waves silently claims measurements nobody made. The renderer is explicit about observability:

- **Strobe ticks:** each compare state carries a tick mark at its strobe instant — the only ground truth. The held line between strobes renders dimmed, so it reads as *inference*, not measurement.
- **Cycle-domain idealization:** a pin's real intra-cycle shape depends on its timeset wave format (NRZ, RZ, RO, SBC) and drive-edge/strobe placement. ADF-1 v1 renders the NRZ idealization and says so in the spec and on the canvas legend. The IR already carries the full upgrade path, including mid-run timeset switching (real tester/STIL patterns select different timesets across blocks and cycles): `TestRun.timing_sets` holds named `TimingSet`s, `Cycle.timeset` references one, and `PinDef.timing` is the run default. Resolution chain: `Cycle.timeset` → `TimingSet` entry for the pin → `PinDef.timing` → NRZ idealization. A future ADF-1 `FORMAT`/`TIMESET` extension or a STIL/VCD adapter fills these existing fields as data — the no-IR-change claim holds because the fields exist now, not because they could be added later. The chain is evaluated **once, at assembly time**, while `Cycle` objects still exist: the builder bakes drive edges and strobe placement into waveform transition times and stamps each failure's resolved strobe into `FailureEvent.strobe_time`; cycles are then discarded, and the renderer consumes only resolved values.
- **Sparse-capture mode (Tier B):** when only failing-strobe captures exist (production STDF/ATDF), the lane renders as a **fail-capture strip** — markers at failing strobes showing captured vs inferred-expected state — instead of pretending to know a full waveform.
- **Stimulus is not observation:** a `DRV` record is programmed tester stimulus — continuously known *by definition*, so driven series render solid — while a `GOT` capture is a comparator observation that exists only at its strobe. The two live in separate series (`driven_waves` vs `captured_waves`, §4) and are never merged into one "actual" trace: carrying a captured value through a drive interval, or presenting stimulus as a DUT observation, would fabricate data. On an IO lane both render together, visually distinct, with the direction turnaround readable.
- **Retention gaps render as no-data:** "no transition ⇒ state held" is true only *inside* a retained `WaveformSegment`; between segments the tool discarded the data, `state_at` returns `None`, and the lane shows the hatch. The renderer never extrapolates across its own storage optimization — a gap and a held state are different facts and look different on screen.
- **Mismatch strip anchors to strobe instants,** not whole cycles: narrow red bands at the failing strobes, derived from `FailureEvent` records rather than any waveform reconstruction — the strip marks exactly where the comparator failed and nothing more. The optional interval-XOR overlay (§6.2) is inference and is labeled as such, visually and in the legend.
- **Analog truth is out of scope by physics:** slew, runts, and mid-rail floats between strobes never reach a datalog; even STDF's "glitch" capture code is a comparator artifact. The docs state this so the tool never overclaims.

---

## Deliverables on approval (docs + scaffold)

Create in `C:\Users\HP\Documents\ate-test-log-tool`:

| Path | Content | State |
|---|---|---|
| `README.md` | Pitch, UVP table, screenshot placeholders, quick-start | complete |
| `docs/ARCHITECTURE.md` | §1, §2, §6 of this plan, expanded | complete |
| `docs/LOG_FORMAT_SPEC.md` | Full ADF-1 spec (§3) | complete |
| `docs/ROADMAP.md` | §5 with checkbox milestones + manual test checklists | complete |
| `ate_fa_suite/parsing/grammar/atelog.lark` | Grammar from §3.2 | complete, load-tested |
| `ate_fa_suite/model/entities.py` | Dataclasses from §4 | complete |
| all other `ate_fa_suite/` modules | Typed stubs: signatures, docstrings, `NotImplementedError` | stubs (user's Phase 1–4 work) |
| `sample_logs/*.atelog` | 3 golden logs | complete |
| `tools/gen_log.py` | Working synthetic generator with seeded failures | complete |
| `tools/build_release.py` | Builds `dist/wheelhouse/` (app wheel + Lark wheel via `pip wheel`) and single-file `dist/ate_fa_suite.pyz` zipapp bundling Lark | complete |
| `tests/` | `test_grammar.py` (passing: grammar loads, parses well-formed goldens, and asserts the truncated golden raises), `test_entities.py` (passing), phase-stub tests marked `skip` | partial by design |
| `pyproject.toml` | `lark` dep; `pytest`, `mypy` dev config; `atelog.lark` declared as package data | complete |
| `.github/workflows/ci.yml` | `windows-latest` job: wheelhouse build → fresh-venv `pip install --no-index --find-links` → grammar/golden tests + Tk root smoke via `importlib.resources`; zipapp launch check | complete |

## Verification

1. `pip install -e .[dev]` (or `pip install lark pytest`) succeeds.
2. `pytest` — grammar-load, golden-parse, and entities tests **pass**; phase stubs report skipped, not failed. This proves the grammar in §3.2 is genuinely valid Lark, parses every well-formed sample log, and cleanly rejects the truncated golden with a positioned error.
3. `python tools/gen_log.py --cycles 5000 --pins 8 --fail-rate 0.001 --seed 42 -o sample_logs/generated.atelog` then re-run pytest golden parse against it.
4. `python -m ate_fa_suite` prints a clear "GUI not yet implemented — see docs/ROADMAP.md Phase 3" message rather than a traceback.
5. **Offline install proof** (editable installs bypass packaging, and an internet-enabled venv can't prove an offline property — pip would silently download Lark): `python tools/build_release.py` to produce `dist/wheelhouse/`, then in a throwaway venv run `pip install --no-index --find-links dist/wheelhouse ate-fa-suite` — any dependency missing from the wheelhouse fails loudly. From that venv, a smoke script (a) loads `atelog.lark` via `importlib.resources` and parses a golden log — proving the grammar ships as package data — and (b) creates and destroys a `tkinter.Tk` root — proving Tk works on the target Windows install.
6. **Zipapp proof**: from a venv with *nothing* pip-installed, `python dist/ate_fa_suite.pyz` runs and prints the Phase-3 placeholder banner — the true single-file deployment. Both proofs run in CI on `windows-latest`.
