# ADF-1 — the ATE Datalog Format (`.atelog`)

Version 1.0. This document is the normative spec; `atelog.lark` is its
executable form. Where the two disagree, that is a bug in one of them.

---

## 1. Positioning — what ADF-1 models

**Read this section before anything else.** It is the honest answer to "does
this work with real tester logs?"

ADF-1 is the **FA re-test (capture-all) datalog** — the verbose log produced
when a failing unit is re-run in engineering mode with full fail capture. It is
**not** a production datalog.

The STDF **`FTR`** record is failure-sparse: it carries the failing vector, the
failing pins, and captured states (`RTN_STAT`) *at failing strobes only*, capped
by tester fail-memory depth. It carries no expected states at all.

That is a fact about `FTR`, not about the format. Three claims hold, and only
these three:

* A production datalog carries no **drive** data and no **passing-cycle** data.
  A *complete* waveform still requires the pattern file (STIL/WGL/`.avc`).
* An **`STR`-bearing** log does carry expected *and* captured states at every
  logged failing pin/cycle. `STR` (Scan Test Record, `REC_TYP` 15 / `REC_SUB`
  30) was added in **STDF V4-2007** and holds `EXP_DATA` and `CAP_DATA` arrays
  keyed by `PMR_INDX` and `CYC_BASE + CYCL_NUM`, with `DATA_CHR` mapping the
  packed values to what the spec itself calls *waveform characters*. That is
  precisely the differential this canvas draws.
* `STR` is still capped: `TOTL_CNT` (fails actually logged) may be less than
  `TOTF_CNT` (total pin × cycle failures detected). Fail-memory truncation is
  therefore *visible in the data* and must be surfaced, never hidden.

The suite defines four ingestion fidelity tiers, and the IR and renderer degrade
gracefully across them:

| Tier | Source | Failure table & clustering | Waveform view |
|---|---|---|---|
| **A** | FA re-test log (ADF-1) or VCD bench capture | full | full differential waveform: drive, expected and captured on every cycle |
| **B** | Production **STDF V4-2007 with `STR`** records | full | **differential fail-capture waveform**: expected *and* captured at every logged failing pin/cycle, straight from `EXP_DATA`/`CAP_DATA`. No drive data, no passing cycles; the gaps render as no-data, never interpolated |
| **C** | Production STDF/ATDF, **`FTR` records only** | full | sparse **fail-capture strip**: captured states at failing strobes; expected only where the capture code implies it |
| **D** | Tier B or C **+ STIL (IEEE 1450)** pattern file | full | reconstructed expected/drive waveform with the fail-capture overlay |

**Tier B is the point of the exercise.** STDF has carried per-pin/per-cycle
expected-vs-captured since V4-2007; what has been missing is a tool that renders
it. Phases 1–4 implement **Tier A**; Phase 5 implements **Tier B**. Tiers C and
D remain adapter work on the future roadmap.

---

## 2. Shape of the format

Line-oriented text, deliberately hybridizing **ATDF** (header/metadata records)
and **VCD** (timescale + per-pin state changes), so that future real-format
adapters stay thin.

* Logic states use the VCD / IEEE-1364 alphabet: `0 1 X Z L H`.
* An expected value of `X` is a **mask** (don't-compare), per industry
  convention. A masked compare cannot fail — a `FAIL` flag on one is a
  contradiction, reported as `INCONSISTENT`.
* Keywords are **reserved words**: a pin may not be named `PIN`, `END`,
  `CYCLE`, `TESTBLOCK`, `PINDEF`, `DRV`, `EXP`, `GOT`, `FAILSUMMARY`,
  `VECTORS`, `PASS`, `FAIL`, `IN`, `OUT`, `IO`.
* Encoding is UTF-8. Both LF and CRLF line endings are accepted (Windows tester
  dumps are CRLF), including mixed within one file.

### 2.1 Comments

`//` comments are allowed both **trailing a record** and as **standalone lines**
(optionally indented), anywhere after the magic line.

Two exceptions, both deliberate:

1. **On metadata lines `//` is literal value text**, not a comment — `VALUE`
   matches everything to end of line. A lot ID may legitimately contain slashes.
2. **No comment may precede the `#ATELOG` magic line**, which must be line 1
   regardless.

---

## 3. Coordinate semantics — the rule that shapes the whole IR

`CYCLE` vector numbers and `T=` timestamps are guaranteed unique and monotonic
**only within one test-block invocation**. Real testers restart pattern time per
pattern, and ADF-1 makes no cross-block guarantee.

Block *names* are not unique either: the same pattern may legally appear several
times in one log (retest loops, corner re-runs). Block identity is therefore
**(name, occurrence)** — `BlockId`, with occurrence assigned in document order.

**Consequently the run-wide address of a cycle is always the triple
*(BlockId, vector, time)*** — `VectorLocation` — and never a bare vector,
timestamp, pin name, or block name. Waveform lookups are keyed by
`WaveKey = (BlockId, pin)` for the same reason.

`sample_logs/multi_fail.atelog` re-runs `mbist_march_c` specifically so that any
consumer keying on a bare name collapses two invocations that must stay
separate.

---

## 4. Record reference

### 4.1 Magic line (required, line 1)

```
#ATELOG v1.0
```

Major version mismatch is a **fatal** validation error.

### 4.2 Metadata (required, each key exactly once)

```
LOT: K78842A-07B
WAFER: 14
DEVICE: S32K344_QFN48
TESTER: UFLEX-BLR-22
PROGRAM: s32k_prod_r3.1
DATE: 2026-07-11T03:14:22
TIMESCALE: 1ns
```

`TIMESCALE` accepts `ps`, `ns`, `us`, `ms` and defines the unit of every `T=`
value. A missing key, a duplicate key, or an unparseable `TIMESCALE` is
**fatal**.

### 4.3 Pin declarations

```
PINDEF <name> <IN|OUT|IO>
```

`name` matches `[A-Za-z_][A-Za-z0-9_\[\]]*`, so both `DQ3` and `DQ[3]` are
legal. A duplicate `PINDEF` is **recoverable**: first declaration wins, warning
emitted.

### 4.4 Test blocks

```
TESTBLOCK <block_id>
  <cycle>+
  [FAILSUMMARY <count> VECTORS <v>[,<v>...]]
END TESTBLOCK
```

`block_id` matches `[a-z][a-z0-9_]*`. `FAILSUMMARY` is **optional** — since
`VECTORS` requires at least one vector, a zero-failure block simply omits the
record; there is no "zero" spelling.

`FAILSUMMARY` carries **two independently checkable claims**:

* **`<count>` is the number of failing *compare lines*** in the block —
  pin-granular. One vector with three failing pins contributes **three**, not
  one. It is cross-checked against the observed `FAIL` records.
* **The `VECTORS` list enumerates the distinct failing vectors** — one entry per
  vector having at least one `FAIL`, regardless of how many pins failed on it.
  It is cross-checked against the observed set of such vectors.

So the two numbers are related but not equal, and disagree whenever any vector
fails on more than one pin. In `sample_logs/multi_fail.atelog` the first
invocation of `mbist_march_c` declares `FAILSUMMARY 10 VECTORS
1200,1201,1202,4400,4401` — **10 failing compare lines across 5 distinct
vectors**, which is the canonical worked example of the distinction.

Both mismatches are **recoverable**: warned, never fatal, never silently
corrected. Real datalogs contain exactly these inconsistencies, and detecting
them is a feature — the declared summary and the observed records are two
independent witnesses, and when they disagree the FA engineer needs to know.

### 4.5 Cycles

```
CYCLE <vector> T=<time>
  <pin event>+
END CYCLE
```

Vector numbers and times must be **strictly increasing** within a block
invocation. Violation is **fatal**, never a warning: the waveform bisects depend
on sorted transitions, so a warning would leave the tool with a corrupt index.

### 4.6 Pin events

```
PIN <name> DRV <state>                        // drive: programmed stimulus
PIN <name> EXP <state> GOT <state> <PASS|FAIL>  // compare: comparator result
```

The distinction is load-bearing, not cosmetic (see §6). A duplicate event for
the same pin within one cycle is **recoverable**: first wins, warning emitted.
An event on an undeclared pin is **recoverable**: auto-declared as `IO`, warning
emitted.

### 4.7 End of log

```
END LOG
```

A trailing newline is optional. Absence of `END LOG` means the file is
truncated — see §7.

---

## 5. Complete example

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

This carries everything the FA use case demands: timestamps in `TIMESCALE`
units, failed pin names, failed vector numbers, expected-vs-actual states, and
per-compare PASS/FAIL flags.

---

## 6. `DRV` is not `GOT` — stimulus is not observation

A `DRV` record is **programmed tester stimulus**: continuously known *by
definition*, because the tester decided it. A `GOT` capture is a **comparator
observation**, which exists *only at its strobe instant*; between strobes the
true pin state is unknowable from any datalog.

The IR keeps three provenance-separated wave collections — `driven_waves`,
`expected_waves`, `captured_waves` — and never merges them. Carrying a captured
value through a drive interval, or presenting stimulus as a DUT observation,
would fabricate data that nobody measured.

A pin's real intra-cycle shape further depends on its timeset wave format (NRZ,
RZ, RO, SBC) and its drive-edge/strobe placement. **ADF-1 v1 renders the NRZ
idealization and says so** — in this spec and on the canvas legend. The IR
already carries the full upgrade path as *existing fields*:
`TestRun.timing_sets`, `Cycle.timeset`, `PinDef.timing`, resolved through the
chain `Cycle.timeset → TimingSet entry for the pin → PinDef.timing → NRZ`. A
future `FORMAT`/`TIMESET` extension or a STIL/VCD adapter fills them as data,
with no IR change.

Analog truth — slew, runts, mid-rail floats between strobes — never reaches a
datalog at all. Even STDF's "glitch" capture code is a comparator artifact. The
tool does not claim it.

---

## 7. Truncation

Real tester dumps truncate: the operator aborts, the disk fills, the FTP
transfer dies mid-file.

* The **strict whole-file grammar rejects truncated input by design** — the
  `document` rule requires complete blocks and `END LOG`.
* **Salvage is a chunking-layer behaviour, never a grammar behaviour.** The
  framing scanner recovers every complete cycle before the break, preserves
  block identity and the `FAILSUMMARY` cross-check, and turns the tail into a
  `TestRun.warnings` entry on a *partial* result delivered as `ParseComplete` —
  never a crash.

`sample_logs/truncated.atelog` is the fixture for both halves of that
behaviour.

---

## 8. Validation tiers

These rules are **ADF-1 rules**. The STDF path has a different ordering
guarantee and therefore a different table. See §8.1.

| Tier | Result | Rules |
|---|---|---|
| **Fatal** | `ParseFailed` | unsupported major version; missing/duplicate metadata key; unparseable `TIMESCALE`; non-strictly-increasing vector or time within a block invocation |
| **Recoverable** | `TestRun.warnings` + a deterministic rule | duplicate `PINDEF` (first wins); event on an undeclared pin (auto-declare `IO`); duplicate pin event in one cycle (first wins); reserved word used as an identifier; `FAILSUMMARY` **count** mismatch (declared vs observed failing compare lines); `FAILSUMMARY` **`VECTORS`** mismatch (declared vs observed set of vectors with ≥1 `FAIL`); `INCONSISTENT` records; non-masked `PASS` with disagreeing states |

The two `FAILSUMMARY` checks are separate warnings, not one combined check — a
log can easily get the count right and the vector list wrong, or vice versa, and
collapsing them would hide which witness disagreed.

Structural invariants of `WaveformSegment`, `WaveformSeries` and `TimingSet` are
**not** validated here. They live in the model as `__post_init__` checks raising
`ValueError`, because `assert` statements vanish under `python -O` and
builder-side checks do not guard alternate construction paths.

### 8.1 STDF validation tiers

The §8 table cannot be applied verbatim to a Tier B source, and pretending
otherwise would put the spec and the implementation in disagreement, which this
document's own preamble calls a bug.

The rule that must differ is **ordering**. In ADF-1 a non-strictly-increasing
vector is **fatal**, because the chunked framing scanner builds its index from
file order: an out-of-order vector means the frame boundaries are untrustworthy.
An STDF `STR` array carries its own cycle numbers and the reader has no such
positional dependency, so out-of-order data is recoverable.

| Tier | Result | Rules |
|---|---|---|
| **Fatal** | `ParseFailed` | first record is not `FAR`; `REC_LEN` header unreadable at offset 0; endianness indeterminate |
| **Recoverable** | `TestRun.warnings` + a deterministic rule | cycle numbers not ascending in file order (**sort, then warn**); `TOTL_CNT` < `TOTF_CNT` (fail-memory truncation); `TOTL_CNT` disagrees with Σ`LOCL_CNT`; `DATA_CNT` disagrees with `ceil(LOCL_CNT × DATA_BIT / 8)`; duplicate or missing `REC_INDX` in a continuation set (first wins / emit what exists); `DATA_CHR` character outside `0 1 X Z L H`; `DATA_CHR` absent when `DATA_BIT` > 1; `Z_VAL` ≠ 0 (captured alphabet was pre-transformed by the tester); failure logged on a pin set in `MASK_MAP`; `DATA_FLG` missing `EXP_DATA`, `PMR_INDX` or `CYCL_NUM`; `CPU_TYPE` disagreeing with the framing-derived endianness; cycle number above 2⁵³; truncated tail |

Two rules deserve their reasoning stated, because both look like candidates for
silent correction and neither is:

* **`Z_VAL` is not reversed.** The tester has *already* substituted `Z` per the
  mapping (1 = Z→L, 2 = Z→H, 3 = Z→Z, 4 = Z→X) before writing the log. Undoing
  it would fabricate a measurement nobody made. Record the flag; leave the data.
* **A failure on a masked pin is kept, not dropped.** `MASK_MAP` says the pin
  was globally masked; the `STR` payload says it failed. That is the exact
  analogue of ADF-1's `INCONSISTENT`: two independent witnesses disagreeing,
  which is a fact the FA engineer needs rather than a contradiction to resolve.

---

## 9. Grammar notes

The grammar is `ate_fa_suite/parsing/grammar/atelog.lark`, parsed with
`parser="lalr"`, `lexer="contextual"`, and **six start rules**.

* **`lalr`** — O(n) parsing, roughly an order of magnitude faster than Earley;
  mandatory for multi-hundred-MB datalogs.
* **`contextual`** — resolves deliberate terminal collisions per parse state:
  `STATE /[01XZLH]/` vs `INT` (`1` is both), `VALUE` vs everything,
  `DIRECTION "IN"` vs `PIN_NAME`. The contextual lexer only considers terminals
  *expected in the current LALR state*, so `EXP 1` lexes `1` as `STATE` while
  `CYCLE 1200` lexes as `INT`. **With the standard lexer this grammar would be
  unbuildable.**
* **Comments ride on `NEWLINE`** — the terminal
  `/((\/\/[^\r\n]*)?\r?\n[ \t]*)+/` absorbs runs of line terminators including
  comment-only lines (indented or not) and blank lines, and handles CRLF. There
  is deliberately **no `%ignore COMMENT`**: that only supports trailing
  comments, because a standalone comment line leaves an orphan `NEWLINE` token
  in a parser state that expects a construct, which LALR rejects. Consuming
  comment and newline as one token makes the orphan impossible — and removes any
  need for a `VALUE` priority.
* **Multi-start** — `document`, `prologue`, `testblock_header`, `cycle_batch`,
  `block_trailer`, `end_log` all live in one grammar file and one `Lark`
  instance. Because `document` is *composed from* the fragments rather than
  duplicated alongside them, an edit to any production changes both the strict
  and chunked parse paths at once: **strict/chunked drift is structurally
  impossible.**
* **`propagate_positions=True`** — every dataclass can carry its source line, so
  parse errors and table rows link back to the raw log line. FA engineers always
  want the raw line.
* **`cache=True`** — caches the generated LALR table for near-instant startup.
* **Loaded via `importlib.resources`**, never a `__file__`-relative path, which
  is what keeps it working from an installed wheel or the zipapp.
