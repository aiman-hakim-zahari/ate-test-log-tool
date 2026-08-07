# ATE Test Log Tool

A small Python learning project for parsing semiconductor ATE test logs and, eventually, visualizing failures around a test vector.

I started this project to learn how to turn an engineering idea into a well-tested application. The main things I am practicing are formal parsing, streaming large files, data modelling, validation, packaging, and keeping a GUI separate from the core logic.

> Current status: the ADF-1 parser, semantic validation, and STDF V4-2007 `STR` reader are working. The waveform builder, failure-signature analysis, exports, and GUI are still on the roadmap.

## Why I am building it

When debugging a failing device, an engineer may need to search a text log for failed vectors and compare expected and captured pin states manually. My goal is to make that workflow easier:

1. Parse the log into typed Python data.
2. Show failures with their block, vector, pin, expected state, and actual state.
3. Reconstruct a small waveform window around a selected failure.
4. Group similar failures into a short diagnostic summary.

Only the first two parts are substantially implemented today.

## What works today

- A Lark grammar for the project's text-based ADF-1 log format.
- A default chunked parser that reads large logs in bounded batches.
- A strict whole-file parser for audits and comparison tests.
- Source-aware syntax errors with absolute line numbers and offending text.
- Semantic checks for metadata, timescales, cycle ordering, pin declarations, duplicate events, and failure summaries.
- Recovery of complete cycles when a file ends with an incomplete tail.
- Stable identity for repeated test-block names.
- Deterministic sample-log generation and malformed test fixtures.
- A stdlib-only STDF V4-2007 reader for `STR` expected-versus-captured data.
- A schema-driven STDF writer with little-endian, big-endian, continuation,
  packed-data, mask-map, missing-data, and truncation goldens.
- A same-plan ADF-1/STDF round-trip property test proving both readers produce
  the same format-neutral failure facts.
- A recorded, repeated-batch STDF parser baseline in
  `stdf_perf_baseline.json`.
- Offline wheelhouse and zipapp build tooling.

The chunked path still sends every complete frame through Lark and validates every cycle. The retention window only controls what is stored after validation. An opt-in test exercises this path with a generated one-million cycle log.

## What is not finished

- Waveform construction and retention-gap rendering.
- Failure-signature clustering.
- Filtering and CSV/text exports.
- The Tkinter desktop interface.
- ATDF, STIL, and tester-vendor format adapters.

Running `python -m ate_fa_suite` currently prints a project-status message; it does not open a GUI yet. Run `python -m ate_fa_suite --stdf sample_logs/stdf/golden_le.stdf` for the working headless STDF summary.

## A note about the input format

ADF-1 (`.atelog`) is a bench and reference format defined by this project. It represents a verbose FA re-test log where drive, expected, and captured states are all available, which is the highest-fidelity case (Tier A) and a convenient one to develop against.

It is not the only intended input, and an earlier version of these docs overstated why it existed. The claim was that production datalogs cannot supply expected-vs-captured data per pin per cycle. That is true of the STDF `FTR` record and false of `STR` (Scan Test Record), added in STDF V4-2007, which carries `EXP_DATA` and `CAP_DATA` arrays keyed by pin and cycle. The real gap is that no audited open-source tool *renders* that payload as a differential waveform, and that is the gap this project aims at. See `docs/ROADMAP.md` for the correction and the Phase 5 plan.

What remains true: a production log carries no drive data and no passing cycles, so a complete waveform still needs the pattern file, and `STR` itself may log fewer failures than the tester detected. The tool will not draw signal history that was never recorded, so those gaps render as no-data rather than being interpolated.

Example:

```text
#ATELOG v1.0
LOT: K78842A-07B
WAFER: 14
DEVICE: DEMO_DUT
TESTER: DEMO_TESTER
PROGRAM: demo_program
DATE: 2026-07-11T03:14:22
TIMESCALE: 1ns

PINDEF CLK IN
PINDEF DQ1 IO

TESTBLOCK mbist
CYCLE 1200 T=1200000
PIN CLK DRV 1
PIN DQ1 EXP 0 GOT 1 FAIL
END CYCLE
FAILSUMMARY 1 VECTORS 1200
END TESTBLOCK
END LOG
```

The complete format is documented in [`docs/LOG_FORMAT_SPEC.md`](docs/LOG_FORMAT_SPEC.md).

## Setup

Requirements:

- Python 3.10 or newer
- `lark` at runtime
- `pytest` and `mypy` for development

Install the project in editable mode:

```bash
python -m pip install -e ".[dev]"
```

Run the checks:

```bash
python -m pytest
python -m mypy --strict ate_fa_suite tools/gen_log.py tools/stdf_writer.py tools/perf_stdf.py
```

Generate a sample log:

```bash
python tools/gen_log.py --cycles 5000 --pins 8 --fail-rate 0.001 --seed 42 \
  -o sample_logs/generated.atelog
```

Regenerate the deterministic STDF feature corpus in a scratch directory and
print a headless FA summary:

```bash
python tools/stdf_writer.py --golden-corpus build/manual-stdf
python -m ate_fa_suite --stdf build/manual-stdf/golden_le.stdf
```

## Using the parser

The STDF reader has the headless summary command shown above. The desktop GUI
is not finished yet, and either parser can also be used directly from Python:

```python
from pathlib import Path

from ate_fa_suite.model.entities import ParseComplete
from ate_fa_suite.parsing.parser import LogParser

result = LogParser().parse(
    Path("sample_logs/multi_fail.atelog"),
    job_id=1,
)

if isinstance(result, ParseComplete):
    print(f"blocks: {len(result.run.blocks)}")
    print(f"failures: {len(result.run.failures)}")
    for failure in result.run.failures[:5]:
        print(
            failure.location.block.label(),
            failure.location.vector,
            failure.pin,
            failure.expected.value,
            failure.actual.value,
        )
else:
    print(
        f"line {result.line}, column {result.column}: "
        f"{result.message}"
    )
    print(result.context)
```

`LogParser.parse()` uses the bounded-memory chunked path. For tests or audits, `LogParser.parse_text()` parses a complete string with the strict document grammar.

## Project structure

```text
ate_fa_suite/
  model/       immutable domain objects
  parsing/     framing, Lark grammar, transformation, validation
  services/    background-work boundary for the future GUI
  viewmodel/   presentation state and events
  view/        Tkinter components, mostly planned
sample_logs/   text fixtures plus the binary STDF feature corpus
tests/         parser, model, packaging, and architecture tests
tools/         ADF/STDF generators, performance, and release helpers
docs/          architecture, format specification, and roadmap
```

One design rule I wanted to practice is an import firewall: parsing and model code must not depend on Tkinter, and view code must not depend on Lark. An AST-based test checks this boundary.

## What I have learned so far

- A streaming scanner can find structural boundaries, but the grammar should remain the authority for syntax.
- Bounded memory is not useful if skipped data also skips validation.
- Source positions need to survive framing and transformation to produce useful errors.
- Recoverable log inconsistencies need deterministic rules, such as "first declaration wins."
- Tests for malformed inputs are as important as tests for valid files.
- Packaging resources such as a grammar file needs separate testing from an editable install.

## Roadmap

The detailed checklist is in [`docs/ROADMAP.md`](docs/ROADMAP.md). The next planned step is domain logic: waveform construction, failure signatures, filters, and exports. The GUI follows after the headless logic is tested.

## Related project

The structure of this README was partly inspired by [noonchen/STDF-Viewer](https://github.com/noonchen/STDF-Viewer), which gives users a direct description, setup steps, and feature-based usage guidance. STDF-Viewer is a mature STDF analysis application; this repository is a smaller learning project focused on the path from detailed functional logs to failure-oriented waveform diagnostics.

## Contributing

This is mainly a portfolio and learning project, but bug reports and review feedback are welcome. In particular, feedback from ATE, product engineering, and failure-analysis workflows would be useful.

## License

The package metadata currently declares the project as MIT licensed. A standalone `LICENSE` file still needs to be added before a formal release.
