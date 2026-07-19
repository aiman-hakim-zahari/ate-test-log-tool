# ATE Test Log Visualizer & Diagnostics Suite

**Turn a semiconductor ATE datalog into a waveform you can debug — and a fail
signature you can paste into an 8D report.**

Click a failing vector; the canvas reconstructs the digital waveform of the
failing pins around it, with *expected* ghosted behind *actual* and a red band
at exactly the strobe where the comparator disagreed. Then it tells you, in one
sentence, what 83% of the failures have in common.

Parsed from a **text datalog** with a formal grammar. Runs on an offline,
admin-locked lab PC with one pure-Python dependency.

> **Status: Step 0 — scaffold.** The grammar, the domain model, the golden
> logs, the generator and the release tooling are complete and tested. The
> transformer, domain logic and GUI are built out across Phases 1–4 — see
> [docs/ROADMAP.md](docs/ROADMAP.md). `python -m ate_fa_suite` currently prints a
> placeholder banner, deliberately, rather than a traceback.

---

## Why this exists

FA engineers debugging a failing device today `grep` the datalog, paste vector
numbers into Excel, and eyeball them. The tooling landscape doesn't help:

| Domain | Owned by | What's missing |
|---|---|---|
| STDF **records** | PySTDF, Semi-ATE, STDF-Viewer | Functional fail payload is flattened to pass/fail; no waveforms, no expected-vs-actual, no clustering |
| **Waveforms** | GTKWave | No concept of test *failure* — shows what a signal did, never what it should have done. No datalog ingestion |
| **Yield** | wafermap, Examinator, Exensio | Lot/wafer statistics altitude — useless for single-device FA |

Among the tools audited (July 2026), **none bridges textual ATE datalogs →
per-pin waveform reconstruction around the failing vector → automated
fail-signature clustering.** That bridge is this project. Full audit and the
scope of that claim: [docs/ARCHITECTURE.md §1](docs/ARCHITECTURE.md).

## What makes it different

1. **Click-to-Waveform "Vector Replay"** — click a failure row, get the waveform
   ±N cycles around it, reconstructed from a text log.
2. **Differential lanes** — actual (solid), expected (ghosted), and a
   strobe-anchored mismatch strip. *GTKWave draws one truth; this draws the
   disagreement.*
3. **Automated fail-signature clustering** — *"83% of failures: SA0-candidate on
   DQ[7:0], vectors 1200–1299."* Generated, not typed.
4. **Grammar-as-data ingestion** — a Lark EBNF grammar, not regex soup. Another
   tester dialect is a grammar file, not a rewrite.
5. **Fab-floor deployable** — stdlib Tkinter + `lark`. Offline wheelhouse or a
   single-file `.pyz`. No browser, no GPU, no Electron, **no network**.

## Screenshots

Reserved, and deliberately not faked — there is no GUI to screenshot yet. These
land with their phases (see [docs/ROADMAP.md](docs/ROADMAP.md)):

| Planned | Lands in | Will show |
|---|---|---|
| `docs/img/screenshot-table.png` | Phase 3 | Failure table + ranked fail-signature panel, status bar with parse time and warnings |
| `docs/img/screenshot-waveform.png` | Phase 4 | Differential lanes — actual solid, expected ghosted, red mismatch bands at failing strobes, no-data hatch across a retention gap |
| `docs/img/screenshot-zoom.png` | Phase 4 | Cursor-anchored zoom into a busy column, activity block visible |

---

## Quick start

```bash
pip install -e ".[dev]"
pytest
python -m ate_fa_suite
```

Generate a synthetic corpus:

```bash
python tools/gen_log.py --cycles 5000 --pins 8 --fail-rate 0.001 --seed 42 \
    -o sample_logs/generated.atelog
```

### Offline deployment (the fab-floor path)

```bash
python tools/build_release.py          # -> dist/wheelhouse/ and dist/ate_fa_suite.pyz
```

Then on the target machine, either:

```bash
pip install --no-index --find-links dist/wheelhouse ate-fa-suite
python tools/smoke_offline.py          # proves the grammar shipped + Tk works
```

or, with no pip at all:

```bash
python dist/ate_fa_suite.pyz
python tools/check_zipapp.py dist/ate_fa_suite.pyz   # proves Lark + grammar ship inside the archive
```

`--no-index` is the point: any dependency missing from the wheelhouse fails
*loudly* instead of being silently downloaded. Both paths are exercised in CI on
`windows-latest`.

---

## The log format

ADF-1 (`.atelog`) is a line-oriented hybrid of **ATDF** (header/metadata) and
**VCD** (timescale + per-pin state changes):

```
#ATELOG v1.0
LOT: K78842A-07B
TIMESCALE: 1ns

PINDEF DQ1 IO

TESTBLOCK mbist_march_c
CYCLE 1200 T=1200000
PIN CLK DRV 1
PIN DQ1 EXP 0 GOT 1 FAIL      // SA1 candidate
END CYCLE
FAILSUMMARY 1 VECTORS 1200
END TESTBLOCK
END LOG
```

Full spec: [docs/LOG_FORMAT_SPEC.md](docs/LOG_FORMAT_SPEC.md).

### Honesty about real tester data

ADF-1 models the **FA re-test (capture-all) datalog**, not a production one.
Production STDF/ATDF is failure-sparse — drive and expected states live in the
pattern file, not the log — so **no tool can draw a full waveform from a
production log alone**. The suite defines three fidelity tiers and degrades
gracefully across them (Tier A now; B and C are adapter work). Likewise, a
tester observes an output pin *only at the strobe instant*: held states between
strobes render dimmed, retention gaps render as no-data hatch, and the mismatch
strip is derived from recorded comparator results rather than inferred from
waveform XOR. The tool does not draw measurements nobody made.

---

## Architecture at a glance

```
VIEW        tkinter widgets            (imports tkinter, NEVER lark)
VIEWMODEL   observable state + pub-sub (pure Python, testable headless)
SERVICES    ParserWorker thread ── queue.Queue + root.after(50) pump
MODEL       entities · signature · waveform
PARSING     chunked_reader → Lark(atelog.lark) → transformer
                                     (imports lark, NEVER tkinter)
```

That firewall is enforced by an **AST-walking test**, not by convention — it
catches conditional and function-local imports and is immune to test-ordering
contamination. Details: [docs/ARCHITECTURE.md §2](docs/ARCHITECTURE.md).

## Development

```bash
pytest                          # phase stubs report as skipped, not failed
mypy --strict ate_fa_suite
```

Requires Python ≥ 3.10. Runtime dependency: `lark`. GUI: stdlib `tkinter`.

## License

MIT.
