"""Tests for grammar packaging, fragments, and sample logs."""

from __future__ import annotations

import pytest
from lark import Lark
from lark.exceptions import UnexpectedEOF, UnexpectedInput, UnexpectedToken

from ate_fa_suite.parsing.parser import (
    GRAMMAR_FILENAME,
    GRAMMAR_PACKAGE,
    STARTS,
    build_parser,
    load_grammar_text,
)


@pytest.fixture(scope="module")
def parser() -> Lark:
    return build_parser()


# --- packaging ---------------------------------------------------------------


def test_grammar_loads_as_package_data() -> None:
    """The installed package must provide the grammar."""
    text = load_grammar_text()
    assert "cycle_batch" in text
    assert "%ignore" in text
    assert GRAMMAR_FILENAME.endswith(".lark")
    assert GRAMMAR_PACKAGE == "ate_fa_suite.parsing.grammar"


def test_grammar_has_no_ignored_comment_terminal() -> None:
    """Standalone comments are handled by NEWLINE, not a global ignore."""
    text = load_grammar_text()
    ignores = [
        line for line in text.splitlines() if line.strip().startswith("%ignore")
    ]
    assert ignores == ["%ignore /[ \\t]+/"], ignores


def test_grammar_document_is_composed_from_fragment_rules() -> None:
    """Strict and framed parsing use the same grammar rules."""
    text = load_grammar_text()
    assert "document: prologue testblock+ end_log" in text
    assert "testblock: testblock_header cycle_batch block_trailer" in text


# --- construction ------------------------------------------------------------


# Smallest valid input for each fragment entry point.
MINIMAL_FRAGMENTS: dict[str, str] = {
    "prologue": (
        "#ATELOG v1.0\n"
        "LOT: L\nWAFER: 1\nDEVICE: D\nTESTER: T\nPROGRAM: P\n"
        "DATE: 2026-07-11T00:00:00\nTIMESCALE: 1ns\n"
        "PINDEF CLK IN\n"
    ),
    "testblock_header": "TESTBLOCK blk\n",
    "cycle_batch": "CYCLE 1 T=0\nPIN CLK DRV 1\nEND CYCLE\n",
    "block_trailer": "END TESTBLOCK\n",
    "end_log": "END LOG\n",
}


def test_parser_exposes_exactly_the_six_start_rules(parser: Lark) -> None:
    """Every configured entry point accepts its smallest valid input."""
    assert set(STARTS) == set(MINIMAL_FRAGMENTS) | {"document"}
    for start, snippet in MINIMAL_FRAGMENTS.items():
        assert parser.parse(snippet, start=start).data == start


def test_document_is_a_start_rule_too(parser: Lark, clean_pass_text: str) -> None:
    assert parser.parse(clean_pass_text, start="document").data == "document"


def test_unknown_start_rule_is_rejected(parser: Lark) -> None:
    with pytest.raises(Exception):
        parser.parse("END LOG\n", start="not_a_rule")


def test_build_parser_is_cached() -> None:
    assert build_parser() is build_parser()


# --- well-formed goldens -----------------------------------------------------


def test_clean_pass_parses(parser: Lark, clean_pass_text: str) -> None:
    tree = parser.parse(clean_pass_text, start="document")
    assert tree.data == "document"


def test_multi_fail_parses(parser: Lark, multi_fail_text: str) -> None:
    tree = parser.parse(multi_fail_text, start="document")
    assert len(list(tree.find_data("testblock"))) == 3


def test_multi_fail_has_a_repeated_block_name(multi_fail_text: str) -> None:
    """The golden covers repeated block names."""
    assert multi_fail_text.count("TESTBLOCK mbist_march_c") == 2


def test_multi_fail_failsummary_arithmetic_is_self_consistent(
    parser: Lark, multi_fail_text: str
) -> None:
    """Summary counts use failed lines; vector lists use unique vectors."""
    tree = parser.parse(multi_fail_text, start="document")
    seen: list[tuple[int, tuple[int, ...]]] = []

    for block in tree.find_data("testblock"):
        fail_lines = 0
        fail_vectors: list[int] = []
        for cycle in block.find_data("cycle"):
            vector = int(str(cycle.children[0]))
            failing = [
                c
                for c in cycle.find_data("compare")
                if str(c.children[3]) == "FAIL"
            ]
            fail_lines += len(failing)
            if failing:
                fail_vectors.append(vector)

        declared = list(block.find_data("failsummary"))
        if not declared:
            assert fail_lines == 0, "a failing block must declare FAILSUMMARY"
            continue

        count = int(str(declared[0].children[0]))
        vectors = tuple(
            int(str(v)) for v in declared[0].children[1].children  # type: ignore[union-attr]
        )
        assert count == fail_lines, f"declared {count}, observed {fail_lines}"
        assert vectors == tuple(fail_vectors)
        seen.append((count, vectors))

    # Block 1 is the worked example: count != len(vectors), deliberately.
    assert (10, (1200, 1201, 1202, 4400, 4401)) in seen
    assert any(count != len(vectors) for count, vectors in seen), (
        "the golden must contain a block where the pin-granular count differs "
        "from the vector count, or it stops demonstrating the distinction"
    )


def test_generated_corpus_parses(parser: Lark, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """PROJECT_PLAN Verification item 3, as a test rather than a manual step."""
    from tools.gen_log import write_log

    out = tmp_path / "generated.atelog"
    injected = write_log(
        out, cycles=500, pins=8, fail_rate=0.01, seed=42
    )
    parser.parse(out.read_text(encoding="utf-8"), start="document")
    assert injected, "generator should have injected at least one failure"


def test_generator_is_deterministic(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tools.gen_log import write_log

    a, b = tmp_path / "a.atelog", tmp_path / "b.atelog"
    fa = write_log(a, cycles=300, pins=6, fail_rate=0.02, seed=7)
    fb = write_log(b, cycles=300, pins=6, fail_rate=0.02, seed=7)
    assert a.read_bytes() == b.read_bytes()
    assert fa == fb


# --- edge cases the NEWLINE terminal has to absorb ---------------------------


@pytest.mark.parametrize("golden", ["clean_pass", "multi_fail"])
def test_crlf_line_endings_parse(parser: Lark, sample_logs, golden: str) -> None:  # type: ignore[no-untyped-def]
    """Windows tester dumps are CRLF; the NEWLINE terminal handles both."""
    text = (sample_logs / f"{golden}.atelog").read_text(encoding="utf-8")
    parser.parse(text.replace("\n", "\r\n"), start="document")


def test_comments_in_every_legal_position(
    parser: Lark, multi_fail_text: str
) -> None:
    """The multi_fail golden carries comments in every position the spec
    allows: trailing, standalone, indented, between records, inside a cycle."""
    assert "      // indented standalone comment" in multi_fail_text
    parser.parse(multi_fail_text, start="document")


def test_double_slash_is_literal_on_metadata_lines(
    parser: Lark, multi_fail_text: str
) -> None:
    """On metadata lines the contextual lexer expects only VALUE, so `//` there
    is literal value text — which is why no VALUE priority is needed."""
    tree = parser.parse(multi_fail_text, start="document")
    lot = next(
        str(m.children[1]) for m in tree.find_data("meta")
        if str(m.children[0]).startswith("LOT")
    )
    assert "//" in lot


def test_comment_cannot_fake_a_frame_boundary(parser: Lark) -> None:
    """A trailing comment containing the text `END CYCLE` must not terminate
    the cycle — the framing scanner classifies on the LEADING token only."""
    text = (
        "CYCLE 5 T=5\n"
        "PIN CLK DRV 1   // watch out: END CYCLE inside a comment\n"
        "END CYCLE\n"
    )
    tree = parser.parse(text, start="cycle_batch")
    assert len(list(tree.find_data("cycle"))) == 1


def test_end_log_without_trailing_newline(parser: Lark, clean_pass_text: str) -> None:
    parser.parse(clean_pass_text.rstrip("\n"), start="document")


def test_state_and_int_terminals_coexist(parser: Lark) -> None:
    """The contextual lexer distinguishes numeric states from integers."""
    tree = parser.parse(
        "CYCLE 1 T=0\nPIN DQ0 EXP 1 GOT 1 PASS\nEND CYCLE\n",
        start="cycle_batch",
    )
    cycle = next(iter(tree.find_data("cycle")))
    assert str(cycle.children[0]) == "1"


# --- fragment start rules (the chunked path shares these productions) --------


def test_prologue_fragment(parser: Lark, clean_pass_text: str) -> None:
    prologue = clean_pass_text.split("// A fully passing")[0]
    parser.parse(prologue, start="prologue")


def test_testblock_header_fragment(parser: Lark) -> None:
    parser.parse("TESTBLOCK mbist_march_c\n", start="testblock_header")


def test_cycle_batch_fragment(parser: Lark) -> None:
    text = (
        "CYCLE 1200 T=1200000\nPIN CLK DRV 1\nEND CYCLE\n"
        "CYCLE 1201 T=1201000\nPIN CLK DRV 0\nEND CYCLE\n"
    )
    tree = parser.parse(text, start="cycle_batch")
    assert len(list(tree.find_data("cycle"))) == 2


def test_cycle_batch_absorbs_trailing_trivia(parser: Lark) -> None:
    """Frame-boundary ownership: blank/comment lines attach to the PRECEDING
    frame, because a fragment must start with its marker token."""
    text = (
        "CYCLE 1 T=0\nPIN CLK DRV 1\nEND CYCLE\n"
        "// trivia belonging to this frame\n\n"
    )
    parser.parse(text, start="cycle_batch")


@pytest.mark.parametrize(
    "text",
    ["FAILSUMMARY 2 VECTORS 1200,1201\nEND TESTBLOCK\n", "END TESTBLOCK\n"],
)
def test_block_trailer_fragment(parser: Lark, text: str) -> None:
    parser.parse(text, start="block_trailer")


@pytest.mark.parametrize("text", ["END LOG\n", "END LOG"])
def test_end_log_fragment(parser: Lark, text: str) -> None:
    parser.parse(text, start="end_log")


# --- the truncated golden is an EXPECTED-FAILURE fixture ---------------------


def test_truncated_golden_is_rejected_by_strict_grammar(
    parser: Lark, truncated_text: str
) -> None:
    """Strict parsing rejects incomplete blocks; framing handles salvage."""
    with pytest.raises((UnexpectedToken, UnexpectedEOF, UnexpectedInput)) as exc:
        parser.parse(truncated_text, start="document")

    error = exc.value
    assert isinstance(error, UnexpectedInput)
    # The error should identify the broken line.
    assert error.line == truncated_text.rstrip("\n").count("\n") + 1
    assert "GOT" in truncated_text.splitlines()[error.line - 1]


def test_truncated_golden_prefix_is_otherwise_well_formed(
    parser: Lark, truncated_text: str
) -> None:
    """Exactly three complete cycles precede the break — the number Phase 1 M6's
    salvage test will assert it recovered."""
    body = truncated_text.split("TESTBLOCK mbist_march_c\n", 1)[1]
    complete = body.rsplit("END CYCLE\n", 1)[0] + "END CYCLE\n"
    tree = parser.parse(complete, start="cycle_batch")
    assert len(list(tree.find_data("cycle"))) == 3
