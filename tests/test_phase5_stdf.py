"""Phase 5 Step 7: the STDF V4-2007 reader, M1 to M6.

Fixtures are encoded here rather than in ``tools/``.  The shared writer that
drives both encodings from one plan is Step 8 M7/M8; until it exists these
tests need bytes they control field by field, which is also what makes the
byte-level assertions below meaningful.

The two ``DATA_FLG`` values asserted against the specification's own worked
examples (252 and 236) are the load-bearing check in this file: they pin the
flag's polarity, which is the one thing published field tables disagree on.
"""

from __future__ import annotations

import dataclasses
import struct
from pathlib import Path
from typing import Iterable

import pytest

from ate_fa_suite.model.entities import (
    FailCategory,
    LogicState,
    ParseComplete,
    ParseFailed,
    TestRun,
)
from ate_fa_suite.parsing.ingest import looks_like_stdf, parser_for
from ate_fa_suite.parsing.parser import LogParser
from ate_fa_suite.parsing.stdf import StdfParser
from ate_fa_suite.parsing.stdf.codec import (
    BIG_ENDIAN,
    LITTLE_ENDIAN,
    BitMap,
    Cursor,
    decode_record,
)
from ate_fa_suite.parsing.stdf.mapping import CYCLE_PERIOD, SOURCE_FORMAT
from ate_fa_suite.parsing.stdf.reader import (
    detect_endianness,
    iter_records,
)
from ate_fa_suite.parsing.stdf.records import (
    FAR,
    FTR,
    MIR,
    PMR,
    PSR,
    SPECS,
    STR,
    STR_DATA_FLG_BITS,
    Field,
    Scalar,
)
from ate_fa_suite.parsing.stdf.scan import (
    WAVEFORM_STATES,
    expected_data_bytes,
    unpack_characters,
)

pytestmark = pytest.mark.phase5


# --- a minimal encoder, field by field ---------------------------------------


class Payload:
    """Builds one record payload in a chosen byte order."""

    def __init__(self, endian: str = LITTLE_ENDIAN) -> None:
        self.endian = endian
        self.data = bytearray()

    def _pack(self, code: str, value: int) -> "Payload":
        self.data += struct.pack(self.endian + code, value)
        return self

    def u1(self, value: int) -> "Payload":
        return self._pack("B", value)

    def u2(self, value: int) -> "Payload":
        return self._pack("H", value)

    def u4(self, value: int) -> "Payload":
        return self._pack("I", value)

    def u8(self, value: int) -> "Payload":
        return self._pack("Q", value)

    def i2(self, value: int) -> "Payload":
        return self._pack("h", value)

    def b1(self, value: int) -> "Payload":
        self.data.append(value)
        return self

    def cn(self, text: str) -> "Payload":
        raw = text.encode("latin-1")
        self.data.append(len(raw))
        self.data += raw
        return self

    def c1(self, text: str) -> "Payload":
        self.data += text.encode("latin-1")[:1] or b" "
        return self

    def bn(self, raw: bytes) -> "Payload":
        """``B*n``: a 1-byte count of BYTES."""
        self.data.append(len(raw))
        self.data += raw
        return self

    def dn(self, bit_count: int, raw: bytes) -> "Payload":
        """``D*n``: a 2-byte count of BITS."""
        self.u2(bit_count)
        self.data += raw
        return self

    def raw(self, raw: bytes) -> "Payload":
        self.data += raw
        return self


def framed(rec_typ: int, rec_sub: int, payload: Payload) -> bytes:
    """Prepend REC_LEN / REC_TYP / REC_SUB."""
    body = bytes(payload.data)
    return (
        struct.pack(payload.endian + "HBB", len(body), rec_typ, rec_sub) + body
    )


def data_flg(present: Iterable[str]) -> int:
    """DATA_FLG for a set of present arrays: a SET bit means ABSENT."""
    absent = set(STR_DATA_FLG_BITS) - set(present)
    return sum(1 << STR_DATA_FLG_BITS[name] for name in absent)


def pack_states(characters: str, data_bit: int, alphabet: str) -> bytes:
    """Pack waveform characters low-order-first, mirroring the decoder."""
    if data_bit == 8:
        return characters.encode("latin-1")
    per_byte = 8 // data_bit
    out = bytearray(expected_data_bytes(len(characters), data_bit))
    for index, character in enumerate(characters):
        byte_index, slot = divmod(index, per_byte)
        out[byte_index] |= alphabet.index(character) << (slot * data_bit)
    return bytes(out)


def bitmap_for(indices: Iterable[int], bit_count: int) -> bytes:
    """Least-significant-bit-first bytes for a D*n payload."""
    out = bytearray(-(-bit_count // 8))
    for index in indices:
        out[index // 8] |= 1 << (index % 8)
    return bytes(out)


ALPHABET = "01LHXZ"


def far(endian: str = LITTLE_ENDIAN, cpu_type: int | None = None) -> bytes:
    if cpu_type is None:
        cpu_type = 2 if endian == LITTLE_ENDIAN else 1
    return framed(0, 10, Payload(endian).u1(cpu_type).u1(4))


def mir(endian: str = LITTLE_ENDIAN) -> bytes:
    payload = Payload(endian)
    payload.u4(1_700_000_000).u4(1_700_000_100).u1(1)
    payload.c1("P").c1("N").c1(" ").u2(65535).c1(" ")
    payload.cn("LOT42").cn("MPC5777C").cn("tester-07").cn("V93000")
    payload.cn("scan_prod_v3")
    return framed(1, 10, payload)


def pmr(index: int, log_nam: str, endian: str = LITTLE_ENDIAN) -> bytes:
    payload = Payload(endian)
    payload.u2(index).u2(0).cn(f"ch{index}").cn(f"phy{index}").cn(log_nam)
    payload.u1(1).u1(1)
    return framed(1, 60, payload)


def psr(index: int, name: str, endian: str = LITTLE_ENDIAN) -> bytes:
    payload = Payload(endian)
    payload.u1(1).u1(1).u2(index).cn(name)
    payload.b1(0x0F)  # all four optional arrays absent
    payload.u2(1).u2(1)
    payload.u8(1).u8(9999)
    payload.cn("stuck_at.stil")
    return framed(1, 90, payload)


def pir(head: int = 1, site: int = 1, endian: str = LITTLE_ENDIAN) -> bytes:
    return framed(5, 10, Payload(endian).u1(head).u1(site))


def prr(head: int = 1, site: int = 1, endian: str = LITTLE_ENDIAN) -> bytes:
    payload = Payload(endian)
    payload.u1(head).u1(site).b1(0x08).u2(2).u2(5).u2(5)
    payload.i2(-32768).i2(-32768).u4(120).cn("part1").cn("").bn(b"")
    return framed(5, 20, payload)


def str_record(
    *,
    endian: str = LITTLE_ENDIAN,
    rec_indx: int = 1,
    rec_tot: int = 1,
    test_num: int = 1,
    head_num: int = 1,
    site_num: int = 1,
    psr_ref: int = 1,
    test_txt: str = "scan_stuck_at",
    z_val: int = 0,
    mask_pins: Iterable[int] | None = None,
    mask_bit_count: int = 0,
    cyc_cnt: int = 9999,
    totf_cnt: int = 5,
    totl_cnt: int = 5,
    cyc_base: int = 1_000_000,
    cycles: Iterable[int] = (),
    pins: Iterable[int] | None = None,
    chains: Iterable[int] | None = None,
    expected: str | None = None,
    captured: str | None = None,
    data_bit: int = 4,
    alphabet: str = ALPHABET,
    conditions: Iterable[tuple[str, str]] = (),
    data_cnt_override: int | None = None,
    locl_cnt_override: int | None = None,
) -> bytes:
    """Encode one STR record in the field order ``records.STR`` declares."""
    cycles = tuple(cycles)
    local_count = (
        locl_cnt_override if locl_cnt_override is not None else len(cycles)
    )
    conditions = tuple(conditions)

    present = ["CYCL_NUM"]
    if pins is not None:
        present.append("PMR_INDX")
    if chains is not None:
        present.append("CHN_NUM")
    if captured is not None:
        present.append("CAP_DATA")
    if expected is not None:
        present.append("EXP_DATA")

    payload = Payload(endian)
    payload.u1(rec_indx).u1(rec_tot).u4(test_num).u1(head_num).u1(site_num)
    payload.u2(psr_ref).b1(0x80)  # TEST_FLG: test failed
    payload.cn("").cn(test_txt).cn("").cn("").cn("")
    payload.u1(z_val)
    # FMU_FLG: bits 2 and 3 gate MASK_MAP, so (1, 0) means "present here".
    payload.b1(0x04 if mask_pins is not None else 0x00)
    if mask_pins is not None:
        payload.dn(mask_bit_count, bitmap_for(mask_pins, mask_bit_count))
    payload.u8(cyc_cnt).u4(totf_cnt).u4(totl_cnt).u8(cyc_base).u2(0)
    payload.b1(data_flg(present))
    payload.u2(len(conditions)).u4(local_count).u2(0)
    payload.u1(data_bit).cn(alphabet if data_bit != 8 else "")
    declared = (
        data_cnt_override
        if data_cnt_override is not None
        else expected_data_bytes(local_count, data_bit)
    )
    payload.u2(declared)
    payload.u1(0).u1(0).u1(0).u1(0)  # USR1_LEN, USR2_LEN, USR3_LEN, TXT_LEN
    for name, _ in conditions:
        payload.cn(name)
    for _, value in conditions:
        payload.cn(value)
    for cycle in cycles:
        payload.u4(cycle)
    if pins is not None:
        for pin in pins:
            payload.u2(pin)
    if chains is not None:
        for chain in chains:
            payload.u2(chain)
    if captured is not None:
        payload.raw(pack_states(captured, data_bit, alphabet))
    if expected is not None:
        payload.raw(pack_states(expected, data_bit, alphabet))
    return framed(15, 30, payload)


#: The reference fixture: three pins, a consecutive pair, and a gap.
GOLDEN_CYCLES = (10, 11, 13, 20, 30)
GOLDEN_PINS = (1, 1, 1, 2, 3)
GOLDEN_EXPECTED = "11101"
#: The trailing X exercises a non-binary captured state end to end.
GOLDEN_CAPTURED = "0001X"


def golden_file(endian: str = LITTLE_ENDIAN) -> bytes:
    return b"".join(
        (
            far(endian),
            mir(endian),
            pmr(1, "DQ0", endian),
            pmr(2, "DQ1", endian),
            pmr(3, "DQ2", endian),
            psr(1, "scan_stuck_at", endian),
            pir(endian=endian),
            str_record(
                endian=endian,
                cycles=GOLDEN_CYCLES,
                pins=GOLDEN_PINS,
                expected=GOLDEN_EXPECTED,
                captured=GOLDEN_CAPTURED,
                conditions=(("VCC1", "1.1V"), ("SHIFT_FREQ", "50MHz")),
            ),
            prr(endian=endian),
        )
    )


def parse_bytes(tmp_path: Path, data: bytes, name: str = "fix.stdf") -> TestRun:
    path = tmp_path / name
    path.write_bytes(data)
    result = StdfParser().parse(path, job_id=7)
    assert isinstance(result, ParseComplete), getattr(result, "message", result)
    assert result.job_id == 7
    return result.run


# --- M1: the field-schema table ----------------------------------------------


def test_str_field_order_matches_the_spec_data_fields_table() -> None:
    """The one real risk in this feature: STR's field order."""
    assert [f.name for f in STR.fields] == [
        "REC_INDX",
        "REC_TOT",
        "TEST_NUM",
        "HEAD_NUM",
        "SITE_NUM",
        "PSR_REF",
        "TEST_FLG",
        "LOG_TYP",
        "TEST_TXT",
        "ALARM_ID",
        "PROG_TXT",
        "RSLT_TXT",
        "Z_VAL",
        "FMU_FLG",
        "MASK_MAP",
        "FAL_MAP",
        "CYC_CNT",
        "TOTF_CNT",
        "TOTL_CNT",
        "CYC_BASE",
        "BIT_BASE",
        "DATA_FLG",
        "COND_CNT",
        "LOCL_CNT",
        "LIM_CNT",
        "DATA_BIT",
        "DATA_CHR",
        "DATA_CNT",
        "USR1_LEN",
        "USR2_LEN",
        "USR3_LEN",
        "TXT_LEN",
        "LIM_INDX",
        "LIM_SPEC",
        "COND_NAM",
        "COND_VAL",
        "CYCL_NUM",
        "PMR_INDX",
        "CHN_NUM",
        "CAP_DATA",
        "EXP_DATA",
        "NEW_DATA",
        "PAT_NUM",
        "BIT_POS",
        "USR1",
        "USR2",
        "USR3",
        "USER_TXT",
    ]


def test_str_field_types_match_the_spec() -> None:
    kinds = {f.name: f.kind for f in STR.fields}
    assert kinds["MASK_MAP"] is Scalar.DN  # bit-counted, not byte-counted
    assert kinds["FAL_MAP"] is Scalar.DN
    assert kinds["DATA_FLG"] is Scalar.B1  # fixed, no length prefix
    assert kinds["TEST_FLG"] is Scalar.B1
    assert kinds["FMU_FLG"] is Scalar.B1
    assert kinds["CYC_CNT"] is Scalar.U8
    assert kinds["CYC_BASE"] is Scalar.U8
    assert kinds["BIT_BASE"] is Scalar.U2  # U*2, not U*4
    assert kinds["LOCL_CNT"] is Scalar.U4
    assert kinds["DATA_CNT"] is Scalar.U2
    assert kinds["CYCL_NUM"] is Scalar.U4
    assert kinds["PMR_INDX"] is Scalar.U2


def test_str_array_counts_come_from_the_right_fields() -> None:
    counts = {f.name: f.count_from for f in STR.fields if f.is_array}
    assert counts["CYCL_NUM"] == "LOCL_CNT"
    assert counts["PMR_INDX"] == "LOCL_CNT"
    # The packed data arrays are sized in BYTES by DATA_CNT, not in fails.
    assert counts["CAP_DATA"] == "DATA_CNT"
    assert counts["EXP_DATA"] == "DATA_CNT"
    assert counts["NEW_DATA"] == "DATA_CNT"
    assert counts["COND_NAM"] == "COND_CNT"
    assert counts["LIM_INDX"] == "LIM_CNT"


def test_str_has_no_per_array_count_prefixes() -> None:
    """Published tables invent CYCO_CNT / PMR_CNT / EXP_CNT.  The spec does not."""
    names = {f.name for f in STR.fields}
    assert not names & {
        "CONT_FLG",
        "CYCO_CNT",
        "PMR_CNT",
        "CHN_CNT",
        "EXP_CNT",
        "CAP_CNT",
        "NEW_CNT",
        "CYC_SIZE",
        "PMR_SIZE",
    }


def test_data_flg_polarity_matches_the_spec_worked_examples() -> None:
    """A SET bit means the array is ABSENT.

    The spec annotates DATA_FLG = 252 as "Use the CYCL_OFST & PMR_INDX arrays"
    and DATA_FLG = 236 as "Use CYCL_OFST, PMR_INDX, and EXP_DATA arrays".
    Reproducing both numbers is what proves the polarity.
    """
    assert data_flg(["CYCL_NUM", "PMR_INDX"]) == 252
    assert data_flg(["CYCL_NUM", "PMR_INDX", "EXP_DATA"]) == 236


def test_data_flg_bit_assignments_match_table_8() -> None:
    assert STR_DATA_FLG_BITS == {
        "CYCL_NUM": 0,
        "PMR_INDX": 1,
        "CHN_NUM": 2,
        "CAP_DATA": 3,
        "EXP_DATA": 4,
        "NEW_DATA": 5,
        "PAT_NUM": 6,
        "BIT_POS": 7,
    }


def test_ftr_fields_are_never_gated() -> None:
    """FTR's OPT_FLAG marks fields invalid, not absent, unlike STR's DATA_FLG."""
    assert all(f.gate is None for f in FTR.fields)
    assert [f.name for f in FTR.fields][:6] == [
        "TEST_NUM",
        "HEAD_NUM",
        "SITE_NUM",
        "TEST_FLG",
        "OPT_FLAG",
        "CYCL_CNT",
    ]


def test_record_subset_and_identities() -> None:
    assert {spec.name for spec in SPECS.values()} == {
        "FAR",
        "MIR",
        "PMR",
        "PSR",
        "PIR",
        "FTR",
        "STR",
        "PRR",
    }
    assert STR.key == (15, 30)
    assert FTR.key == (15, 20)
    assert FAR.key == (0, 10)
    assert PSR.key == (1, 90)
    assert PMR.key == (1, 60)
    assert MIR.key == (1, 10)


def test_every_array_field_names_a_count_field_that_precedes_it() -> None:
    """A count that came later would be unreadable, so this is structural."""
    for spec in SPECS.values():
        seen: set[str] = set()
        for spec_field in spec.fields:
            if spec_field.count_from is not None:
                assert spec_field.count_from in seen, (
                    f"{spec.name}.{spec_field.name} counts from "
                    f"{spec_field.count_from}, which does not precede it"
                )
            if spec_field.width_from is not None:
                assert spec_field.width_from in seen
            if spec_field.gate is not None:
                assert spec_field.gate.field in seen
            seen.add(spec_field.name)


# --- M2: the codec -----------------------------------------------------------


def test_bit_map_is_bit_counted_and_byte_map_is_byte_counted() -> None:
    """The classic STDF decoder bug, in one test."""
    payload = Payload().dn(9, bitmap_for([8], 9)).u1(0xAB)
    cursor = Cursor(bytes(payload.data), LITTLE_ENDIAN)
    bit_map = cursor.bit_map()
    assert bit_map is not None
    assert bit_map.bit_count == 9
    assert len(bit_map.data) == 2  # ceil(9 / 8), not 9
    assert bit_map.set_bits() == (8,)
    # The next field must land exactly after the map.
    assert cursor.integer(Scalar.U1) == 0xAB

    payload = Payload().bn(b"\x01\x02").u1(0xCD)
    cursor = Cursor(bytes(payload.data), LITTLE_ENDIAN)
    byte_map = cursor.byte_map()
    assert byte_map is not None
    assert byte_map.bit_count == 16  # 2 bytes
    assert cursor.integer(Scalar.U1) == 0xCD


def test_nine_pin_mask_map_consumes_two_bytes() -> None:
    """The highest-value byte-level check: ceil(9 / 8) == 2."""
    data = str_record(
        cycles=(1,),
        pins=(1,),
        expected="1",
        captured="0",
        mask_pins=[8],  # bit 8 => PMR index 9
        mask_bit_count=9,
    )
    for record in iter_records(_stream(data), LITTLE_ENDIAN):
        decoded = decode_record(STR, record.payload, LITTLE_ENDIAN)
        assert decoded.truncated_at is None
        mask = decoded.get_map("MASK_MAP")
        assert mask is not None
        assert mask.bit_count == 9
        assert len(mask.data) == 2
        assert mask.set_bits() == (8,)
        # Everything after the map decoded, which is the real proof.
        assert decoded.get_ints("CYCL_NUM") == (1,)


def test_b1_has_no_length_prefix() -> None:
    cursor = Cursor(b"\xf0\x0f", LITTLE_ENDIAN)
    assert cursor.flag_byte() == 0xF0
    assert cursor.flag_byte() == 0x0F
    assert cursor.flag_byte() is None  # past the end, never raises


def test_every_accessor_returns_none_past_the_end() -> None:
    cursor = Cursor(b"", LITTLE_ENDIAN)
    assert cursor.integer(Scalar.U4) is None
    assert cursor.string() is None
    assert cursor.fixed_char() is None
    assert cursor.flag_byte() is None
    assert cursor.byte_map() is None
    assert cursor.bit_map() is None
    assert cursor.integer_array(Scalar.U2, 3) is None
    assert cursor.string_array(2) is None
    assert cursor.nibble_array(4) is None
    assert cursor.variable_integer(2) is None


def test_a_failed_read_does_not_advance_the_cursor() -> None:
    cursor = Cursor(b"\x01", LITTLE_ENDIAN)
    assert cursor.integer(Scalar.U4) is None
    assert cursor.position == 0
    assert cursor.integer(Scalar.U1) == 1


def test_trailing_field_truncation_is_legal() -> None:
    """A record may stop after any field; the rest is simply absent."""
    payload = Payload().u1(2).u1(4)  # a FAR, then nothing
    decoded = decode_record(FAR, bytes(payload.data), LITTLE_ENDIAN)
    assert decoded.truncated_at is None
    assert decoded.get_int("CPU_TYPE") == 2

    short = decode_record(FAR, b"\x02", LITTLE_ENDIAN)
    assert short.truncated_at == "STDF_VER"
    assert short.get_int("CPU_TYPE") == 2
    assert short.get_int("STDF_VER") is None


def test_arrays_decode_in_both_byte_orders() -> None:
    for endian in (LITTLE_ENDIAN, BIG_ENDIAN):
        payload = Payload(endian).u2(3).u2(1).u2(258).u2(65535)
        cursor = Cursor(bytes(payload.data), endian)
        assert cursor.integer(Scalar.U2) == 3
        assert cursor.integer_array(Scalar.U2, 3) == (1, 258, 65535)


def test_zero_length_array_is_empty_not_missing() -> None:
    cursor = Cursor(b"", LITTLE_ENDIAN)
    assert cursor.integer_array(Scalar.U2, 0) == ()
    assert cursor.string_array(0) == ()


def test_nibble_array_puts_the_first_item_in_the_low_nibble() -> None:
    cursor = Cursor(b"\x21\x03", LITTLE_ENDIAN)
    assert cursor.nibble_array(3) == (1, 2, 3)


def test_cn_decodes_latin1_and_flags_non_ascii() -> None:
    payload = Payload().cn("caf\xe9")
    cursor = Cursor(bytes(payload.data), LITTLE_ENDIAN)
    assert cursor.string() == "caf\xe9"
    assert cursor.non_ascii is True


def test_bitmap_ignores_bits_past_its_declared_count() -> None:
    # A writer that leaves high-order junk must not produce phantom pins.
    assert BitMap(bit_count=3, data=b"\xff").set_bits() == (0, 1, 2)


# --- M3: framing and endianness ----------------------------------------------


def test_far_header_settles_endianness_unambiguously() -> None:
    assert detect_endianness(b"\x02\x00\x00\x0a") == LITTLE_ENDIAN
    assert detect_endianness(b"\x00\x02\x00\x0a") == BIG_ENDIAN
    assert detect_endianness(b"MZ\x90\x00") is None
    assert detect_endianness(b"") is None


def test_record_ordinals_are_one_based(tmp_path: Path) -> None:
    records = list(_stream_records(golden_file()))
    assert [r.ordinal for r in records] == list(range(1, len(records) + 1))
    assert records[0].key == FAR.key


def test_short_read_is_truncation_not_a_crash() -> None:
    data = golden_file()
    records = list(_stream_records(data[:-4]))
    assert records[-1].truncated is True
    # Everything before the cut still framed cleanly.
    assert [r.truncated for r in records[:-1]] == [False] * (len(records) - 1)


def test_a_header_cut_in_half_just_ends_iteration() -> None:
    data = far() + b"\x02\x00"
    records = list(_stream_records(data))
    assert len(records) == 1
    assert records[0].truncated is False


# --- M4: STR decode ----------------------------------------------------------


def test_the_logic_alphabet_does_not_grow() -> None:
    """LogicState has exactly six members and the Phase 2 truth table
    (itertools.product over 6 x 6 x 2 = 72 cases) is sized against that."""
    assert len(LogicState) == 6
    assert set(WAVEFORM_STATES) == {"0", "1", "X", "Z", "L", "H"}
    assert set(WAVEFORM_STATES.values()) == set(LogicState)
    assert len(FailCategory) == 7


def test_data_cnt_is_a_ceiling() -> None:
    assert expected_data_bytes(200, 1) == 25
    assert expected_data_bytes(3, 4) == 2  # ceil(12 / 8), not 1
    assert expected_data_bytes(5, 4) == 3
    assert expected_data_bytes(5, 8) == 5
    assert expected_data_bytes(0, 4) == 0


@pytest.mark.parametrize(
    ("data_bit", "alphabet", "characters"),
    [
        # DATA_BIT caps the addressable alphabet at 2**DATA_BIT entries.
        (1, "01", "0101"),
        (2, "01LH", "01LH10"),
        (4, ALPHABET, "01LHXZ"),
        (8, ALPHABET, "01LHXZ"),
    ],
)
def test_packed_states_round_trip_at_every_data_bit(
    data_bit: int, alphabet: str, characters: str
) -> None:
    packed = pack_states(characters, data_bit, alphabet)
    assert len(packed) == expected_data_bytes(len(characters), data_bit)
    assert (
        unpack_characters(packed, len(characters), data_bit, alphabet)
        == tuple(characters)
    )


def test_data_bit_4_and_8_produce_the_same_states(tmp_path: Path) -> None:
    runs = [
        parse_bytes(
            tmp_path,
            b"".join(
                (
                    far(),
                    pmr(1, "DQ0"),
                    str_record(
                        cycles=(4,),
                        pins=(1,),
                        expected="1",
                        captured="0",
                        data_bit=bits,
                    ),
                )
            ),
            name=f"bits{bits}.stdf",
        )
        for bits in (4, 8)
    ]
    assert runs[0].failures == runs[1].failures


def test_unknown_waveform_character_maps_to_unknown_with_a_warning(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(4,),
                    pins=(1,),
                    expected="1",
                    captured="M",
                    alphabet="01LHXZM",  # M is a real STDF character, not ours
                ),
            )
        ),
    )
    assert run.failures[0].actual is LogicState.UNKNOWN
    assert any("'M'" in text and "UNKNOWN" in text for text in run.warnings)
    # The alphabet must not have grown to accommodate it.
    assert len(LogicState) == 6


def test_z_val_is_recorded_and_never_reversed(tmp_path: Path) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(4,), pins=(1,), expected="1", captured="L", z_val=1
                ),
            )
        ),
    )
    # Z_VAL 1 means the tester already mapped Z to L.  L is what was logged,
    # so L is what is reported; reversing it would fabricate a measurement.
    assert run.failures[0].actual is LogicState.WEAK_LOW
    assert any("Z_VAL is 1" in text for text in run.warnings)


def test_cyc_base_offsets_every_cycle(tmp_path: Path) -> None:
    run = parse_bytes(tmp_path, golden_file())
    assert [f.location.vector for f in run.failures] == [
        1_000_000 + offset for offset in GOLDEN_CYCLES
    ]


def test_data_cnt_mismatch_warns(tmp_path: Path) -> None:
    """The earliest and best signal that the field order is wrong."""
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(1, 2, 3),
                    pins=(1, 1, 1),
                    expected="111",
                    captured="000",
                    data_cnt_override=99,
                ),
            )
        ),
    )
    assert any("DATA_CNT is 99" in text for text in run.warnings)


def test_fail_memory_truncation_warns(tmp_path: Path) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(4,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    totf_cnt=500,
                    totl_cnt=1,
                ),
            )
        ),
    )
    assert any(
        "fail memory truncated" in text and "500" in text
        for text in run.warnings
    )


def test_totl_cnt_disagreeing_with_sum_of_locl_cnt_warns(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(4,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    totf_cnt=9,
                    totl_cnt=9,
                ),
            )
        ),
    )
    assert any("does not match the sum of LOCL_CNT" in t for t in run.warnings)


def test_non_ascending_cycles_sort_and_warn_rather_than_being_fatal(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(30, 10, 20),
                    pins=(1, 1, 1),
                    expected="111",
                    captured="000",
                    cyc_base=0,
                ),
            )
        ),
    )
    assert [f.location.vector for f in run.failures] == [10, 20, 30]
    assert any("not ascending" in text for text in run.warnings)


def test_continuation_records_join_into_one_data_set(tmp_path: Path) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                psr(1, "scan_a"),
                str_record(
                    rec_indx=1,
                    rec_tot=3,
                    cycles=(1,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                    totl_cnt=3,
                ),
                _continuation(rec_indx=2, cycle=2),
                _continuation(rec_indx=3, cycle=3),
            )
        ),
    )
    assert len(run.blocks) == 1
    assert [f.location.vector for f in run.failures] == [1, 2, 3]
    assert run.blocks[0].id.label() == "scan_a"


def test_continuations_join_when_they_arrive_out_of_order(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    rec_indx=1,
                    rec_tot=3,
                    cycles=(1,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                    totl_cnt=3,
                ),
                _continuation(rec_indx=3, cycle=3),
                _continuation(rec_indx=2, cycle=2),
            )
        ),
    )
    assert [f.location.vector for f in run.failures] == [1, 2, 3]


def test_duplicate_rec_indx_keeps_the_first_and_warns(tmp_path: Path) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    rec_indx=1,
                    rec_tot=3,
                    cycles=(1,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                    totl_cnt=3,
                ),
                _continuation(rec_indx=2, cycle=2),
                _continuation(rec_indx=2, cycle=99),
            )
        ),
    )
    assert [f.location.vector for f in run.failures] == [1, 2]
    assert any("duplicate REC_INDX 2" in text for text in run.warnings)


def test_a_missing_continuation_emits_what_exists_and_warns(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    rec_indx=1,
                    rec_tot=3,
                    cycles=(1,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                    totl_cnt=3,
                ),
                _continuation(rec_indx=2, cycle=2),
            )
        ),
    )
    assert [f.location.vector for f in run.failures] == [1, 2]
    assert any("never arrived" in text for text in run.warnings)


def test_continuation_may_zero_its_identity_fields(tmp_path: Path) -> None:
    """The spec's own example does exactly this: "Ignored, inherited from
    STR 2A"."""
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    rec_indx=1,
                    rec_tot=2,
                    test_num=2,
                    cycles=(1,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                    totl_cnt=2,
                ),
                str_record(
                    rec_indx=2,
                    rec_tot=2,
                    test_num=0,
                    head_num=0,
                    site_num=0,
                    psr_ref=0,
                    test_txt="",
                    cyc_cnt=0,
                    totf_cnt=0,
                    totl_cnt=0,
                    cyc_base=0,
                    cycles=(2,),
                    pins=(1,),
                    expected="1",
                    captured="0",
                ),
            )
        ),
    )
    assert len(run.blocks) == 1
    assert [f.location.vector for f in run.failures] == [1, 2]


def _continuation(*, rec_indx: int, cycle: int) -> bytes:
    return str_record(
        rec_indx=rec_indx,
        rec_tot=3,
        cycles=(cycle,),
        pins=(1,),
        expected="1",
        captured="0",
        cyc_base=0,
        totl_cnt=3,
    )


# --- M5: mapping onto the IR -------------------------------------------------


def test_driven_waves_are_always_empty(tmp_path: Path) -> None:
    """The honesty guarantee: a production log carries no programmed stimulus,
    so no drive lane is ever synthesized."""
    run = parse_bytes(tmp_path, golden_file())
    assert run.driven_waves == ()
    assert run.expected_waves != ()
    assert run.captured_waves != ()


def test_pin_names_come_from_the_pmr_records(tmp_path: Path) -> None:
    run = parse_bytes(tmp_path, golden_file())
    assert [f.pin for f in run.failures] == ["DQ0", "DQ0", "DQ0", "DQ1", "DQ2"]


def test_categories_come_from_the_expected_versus_captured_pair(
    tmp_path: Path,
) -> None:
    """FailCategory.classify is reused verbatim; this is the wiring check."""
    run = parse_bytes(tmp_path, golden_file())
    assert [(f.expected, f.actual, f.category) for f in run.failures] == [
        (LogicState.HIGH, LogicState.LOW, FailCategory.SA0_CANDIDATE),
        (LogicState.HIGH, LogicState.LOW, FailCategory.SA0_CANDIDATE),
        (LogicState.HIGH, LogicState.LOW, FailCategory.SA0_CANDIDATE),
        (LogicState.LOW, LogicState.HIGH, FailCategory.SA1_CANDIDATE),
        (LogicState.HIGH, LogicState.UNKNOWN, FailCategory.FLOATING),
    ]


def test_a_pin_with_no_pmr_record_falls_back_to_its_index(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                str_record(
                    cycles=(1,),
                    pins=(77,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                ),
            )
        ),
    )
    assert run.failures[0].pin == "PMR77"


def test_block_name_comes_from_the_psr(tmp_path: Path) -> None:
    run = parse_bytes(tmp_path, golden_file())
    assert [b.id.label() for b in run.blocks] == ["scan_stuck_at"]


def test_repeated_block_names_get_occurrence_suffixes(tmp_path: Path) -> None:
    """Reuses the existing assign_block_ids(), so this is a wiring check."""
    one = str_record(cycles=(1,), pins=(1,), expected="1", captured="0")
    run = parse_bytes(
        tmp_path,
        b"".join((far(), pmr(1, "DQ0"), psr(1, "scan_a"), one, one)),
    )
    assert [b.id.label() for b in run.blocks] == ["scan_a", "scan_a#2"]


def test_segments_coalesce_across_consecutive_cycles_only(
    tmp_path: Path,
) -> None:
    """Cycles 10 and 11 were both observed, so one segment covers them.  13 is
    across a gap, so it stays a separate point and the renderer hatches
    between them."""
    run = parse_bytes(tmp_path, golden_file())
    dq0 = [w for w in run.expected_waves if w.pin == "DQ0"][0]
    assert [(s.t_start, s.t_end) for s in dq0.segments] == [
        (1_000_010, 1_000_011),
        (1_000_013, 1_000_013),
    ]
    assert dq0.state_at(1_000_010) is LogicState.HIGH
    assert dq0.state_at(1_000_011) is LogicState.HIGH
    # The gap is not retained, so it renders as no-data rather than a line.
    assert dq0.state_at(1_000_012) is None


def test_a_lone_failure_is_a_one_cycle_segment(tmp_path: Path) -> None:
    run = parse_bytes(tmp_path, golden_file())
    dq1 = [w for w in run.captured_waves if w.pin == "DQ1"][0]
    segment = dq1.segments[0]
    assert (segment.t_start, segment.t_end) == (1_000_020, 1_000_020)
    assert segment.times == (1_000_020,)
    assert dq1.state_at(1_000_019) is None
    assert dq1.state_at(1_000_021) is None


def test_consecutive_cycles_with_differing_states_stay_separate(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(1, 2),
                    pins=(1, 1),
                    expected="10",
                    captured="01",
                    cyc_base=0,
                ),
            )
        ),
    )
    dq0 = run.expected_waves[0]
    assert [(s.t_start, s.t_end) for s in dq0.segments] == [(1, 1), (2, 2)]


def test_missing_exp_data_gives_other_never_inconsistent(
    tmp_path: Path,
) -> None:
    """classify(UNKNOWN, X, failed=True) returns INCONSISTENT, which is right
    for ADF-1 and wrong here: the expectation was simply never logged."""
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(1,),
                    pins=(1,),
                    expected=None,
                    captured="0",
                    cyc_base=0,
                ),
            )
        ),
    )
    failure = run.failures[0]
    assert failure.category is FailCategory.OTHER
    assert failure.category is not FailCategory.INCONSISTENT
    assert failure.expected is LogicState.UNKNOWN
    assert failure.actual is LogicState.LOW
    # Nothing was logged, so nothing is drawn on the expected lane.
    assert run.expected_waves == ()
    assert run.captured_waves != ()
    assert any("no EXP_DATA" in text for text in run.warnings)


def test_a_minimum_data_set_still_yields_failures(tmp_path: Path) -> None:
    """The spec's STR #1 example logs only cycle and pin."""
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(1, 2),
                    pins=(1, 1),
                    expected=None,
                    captured=None,
                    cyc_base=0,
                ),
            )
        ),
    )
    assert len(run.failures) == 2
    assert all(f.category is FailCategory.OTHER for f in run.failures)
    assert run.expected_waves == ()
    assert run.captured_waves == ()


def test_failure_on_a_masked_pin_is_kept_with_a_warning(
    tmp_path: Path,
) -> None:
    """Two witnesses disagreeing is the analogue of ADF-1's INCONSISTENT: a
    fact the FA engineer needs, not a contradiction to resolve away."""
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                pmr(1, "DQ0"),
                str_record(
                    cycles=(1, 2),
                    pins=(1, 1),
                    expected="11",
                    captured="00",
                    cyc_base=0,
                    mask_pins=[0],  # bit 0 => PMR index 1 => DQ0
                    mask_bit_count=9,
                ),
            )
        ),
    )
    assert len(run.failures) == 2  # kept, not dropped
    masked = [t for t in run.warnings if "MASK_MAP" in t]
    assert len(masked) == 1  # one warning per pin, not per failure
    assert "'DQ0'" in masked[0]


def test_pmr_indx_absent_falls_back_to_the_chain_number(
    tmp_path: Path,
) -> None:
    run = parse_bytes(
        tmp_path,
        b"".join(
            (
                far(),
                str_record(
                    cycles=(1,),
                    pins=None,
                    chains=(5,),
                    expected="1",
                    captured="0",
                    cyc_base=0,
                ),
            )
        ),
    )
    assert run.failures[0].pin == "chain_5"


def test_no_addressable_cycle_skips_the_data_set(tmp_path: Path) -> None:
    payload = _str_without_cycle_array()
    run = parse_bytes(tmp_path, b"".join((far(), pmr(1, "DQ0"), payload)))
    assert run.failures == ()
    assert any("no CYCL_NUM" in text for text in run.warnings)


def test_tier_b_coordinate_convention(tmp_path: Path) -> None:
    run = parse_bytes(tmp_path, golden_file())
    for failure in run.failures:
        # Vector and time collapse into one number on a cycle axis.
        assert failure.location.time == failure.location.vector
        assert failure.strobe_time == failure.location.vector
        # 1, not 0: a zero period collapses the mismatch band to no width.
        assert failure.cycle_period == CYCLE_PERIOD == 1
        assert failure.strobe_window is None


def test_header_declares_a_cycle_axis_and_the_source_format(
    tmp_path: Path,
) -> None:
    run = parse_bytes(tmp_path, golden_file())
    assert run.header.time_domain == "cycle"
    assert run.source_format == SOURCE_FORMAT == "STDF-V4-2007"
    assert run.header.timescale_ns == 1.0
    assert run.header.lot == "LOT42"
    assert run.header.device == "MPC5777C"
    assert run.header.program == "scan_prod_v3"
    assert any("tester cycles, not time" in text for text in run.warnings)


def test_src_line_is_the_record_ordinal(tmp_path: Path) -> None:
    run = parse_bytes(tmp_path, golden_file())
    # FAR, MIR, 3x PMR, PSR, PIR, STR => the STR is record 8.
    assert {f.src_line for f in run.failures} == {8}


def test_the_additive_ir_fields_default_for_adf1(tmp_path: Path) -> None:
    """Both new fields are trailing with defaults, so ADF-1 is unaffected."""
    result = LogParser().parse(
        Path("sample_logs/multi_fail.atelog"), job_id=1
    )
    assert isinstance(result, ParseComplete)
    assert result.run.source_format == "ADF-1"
    assert result.run.header.time_domain == "time"


def test_failure_event_fields_are_all_accounted_for() -> None:
    """Adding a field forces an explicit decision about the STDF mapping."""
    keyed = {"location", "pin", "expected", "actual", "category"}
    excluded = {
        "strobe_time": "ADF-1 resolves a strobe offset; STDF has no time base",
        "cycle_period": "STDF cycles are unit-width by construction",
        "src_line": "record ordinal versus text line",
        "strobe_window": "no timeset data in STDF",
    }
    from ate_fa_suite.model.entities import FailureEvent

    assert {
        f.name for f in dataclasses.fields(FailureEvent)
    } == keyed | set(excluded)


# --- M6: the parser, dispatch, and the entry point ---------------------------


def test_little_and_big_endian_files_differ_in_bytes_but_not_in_ir(
    tmp_path: Path,
) -> None:
    little = golden_file(LITTLE_ENDIAN)
    big = golden_file(BIG_ENDIAN)
    assert little != big
    assert parse_bytes(tmp_path, little, "le.stdf") == parse_bytes(
        tmp_path, big, "be.stdf"
    )


def test_cpu_type_disagreement_warns_and_trusts_the_header(
    tmp_path: Path,
) -> None:
    data = b"".join(
        (
            far(LITTLE_ENDIAN, cpu_type=1),  # claims big endian
            pmr(1, "DQ0"),
            str_record(cycles=(1,), pins=(1,), expected="1", captured="0"),
        )
    )
    run = parse_bytes(tmp_path, data)
    assert len(run.failures) == 1  # the header won, so decoding worked
    assert any("CPU_TYPE 1 disagrees" in text for text in run.warnings)


def test_a_non_stdf_file_gives_a_clear_error_not_a_binary_dump(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not.stdf"
    path.write_bytes(b"MZ\x90\x00" + bytes(range(256)))
    result = StdfParser().parse(path, job_id=3)
    assert isinstance(result, ParseFailed)
    assert result.job_id == 3
    assert "first record is not FAR" in result.message
    assert result.context.startswith("first bytes: 4d 5a")
    assert len(result.context) < 60  # a preview, not a dump


def test_an_empty_file_fails_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "empty.stdf"
    path.write_bytes(b"")
    result = StdfParser().parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert "file is empty" in result.context


def test_a_missing_file_fails_cleanly(tmp_path: Path) -> None:
    result = StdfParser().parse(tmp_path / "nope.stdf", job_id=1)
    assert isinstance(result, ParseFailed)
    assert "cannot read" in result.message


def test_a_truncated_file_loads_partially_with_a_warning(
    tmp_path: Path,
) -> None:
    data = golden_file()
    path = tmp_path / "truncated.stdf"
    path.write_bytes(data[: len(data) - 12])
    result = StdfParser().parse(path, job_id=1)
    assert isinstance(result, ParseComplete)
    assert any("ends mid-record" in text for text in result.run.warnings)


@pytest.mark.parametrize("cut", list(range(1, 220, 7)))
def test_every_prefix_returns_a_typed_result_and_never_raises(
    tmp_path: Path, cut: int
) -> None:
    """Robustness: a strided prefix of a golden file must always come back as
    ParseComplete or ParseFailed."""
    path = tmp_path / f"prefix{cut}.stdf"
    path.write_bytes(golden_file()[:cut])
    result = StdfParser().parse(path, job_id=1)
    assert isinstance(result, (ParseComplete, ParseFailed))


def test_a_clean_pass_file_yields_an_empty_run(tmp_path: Path) -> None:
    """A passing device writes no STR at all: no failures, and no crash."""
    run = parse_bytes(
        tmp_path, b"".join((far(), mir(), pmr(1, "DQ0"), pir(), prr()))
    )
    assert run.failures == ()
    assert run.blocks == ()
    assert run.driven_waves == ()
    assert run.header.lot == "LOT42"


def test_multi_part_files_parse_one_part_and_say_which(tmp_path: Path) -> None:
    data = b"".join(
        (
            far(),
            pmr(1, "DQ0"),
            psr(1, "scan_a"),
            pir(),
            str_record(
                cycles=(1,), pins=(1,), expected="1", captured="0", cyc_base=0
            ),
            prr(),
            pir(),
            str_record(
                cycles=(2,), pins=(1,), expected="1", captured="0", cyc_base=0
            ),
            prr(),
        )
    )
    run = parse_bytes(tmp_path, data)
    assert [f.location.vector for f in run.failures] == [1]
    assert any(
        "2 tested parts" in text and "part 1" in text
        for text in run.warnings
    )
    # Parts are never folded into BlockId.occurrence.
    assert [b.id.occurrence for b in run.blocks] == [1]


def test_a_specific_part_can_be_selected(tmp_path: Path) -> None:
    data = b"".join(
        (
            far(),
            pmr(1, "DQ0"),
            pir(),
            str_record(
                cycles=(1,), pins=(1,), expected="1", captured="0", cyc_base=0
            ),
            prr(),
            pir(),
            str_record(
                cycles=(2,), pins=(1,), expected="1", captured="0", cyc_base=0
            ),
            prr(),
        )
    )
    path = tmp_path / "parts.stdf"
    path.write_bytes(data)
    result = StdfParser(part=2).parse(path, job_id=1)
    assert isinstance(result, ParseComplete)
    assert [f.location.vector for f in result.run.failures] == [2]


def test_a_prr_does_not_cut_short_another_sites_open_set(
    tmp_path: Path,
) -> None:
    """PRR flushes its own (head, site) only, so an interleaved file does not
    lose one site's continuations to the other site's part boundary."""
    open_set = str_record(
        rec_indx=1,
        rec_tot=2,
        site_num=2,
        cycles=(1,),
        pins=(1,),
        expected="1",
        captured="0",
        cyc_base=0,
        totl_cnt=2,
    )
    rest = str_record(
        rec_indx=2,
        rec_tot=2,
        site_num=2,
        cycles=(2,),
        pins=(1,),
        expected="1",
        captured="0",
        cyc_base=0,
        totl_cnt=2,
    )
    data = b"".join(
        (
            far(),
            pmr(1, "DQ0"),
            pir(site=1),
            pir(site=2),
            open_set,
            prr(site=1),  # site 1 ends while site 2's set is still filling
            rest,
            prr(site=2),
        )
    )
    path = tmp_path / "interleaved.stdf"
    path.write_bytes(data)
    result = StdfParser(site=2).parse(path, job_id=1)
    assert isinstance(result, ParseComplete)
    assert [f.location.vector for f in result.run.failures] == [1, 2]
    # Both records landed in one set, so no orphan and no missing-record warning.
    assert not any("no open set" in t for t in result.run.warnings)
    assert not any("never arrived" in t for t in result.run.warnings)


def test_ftr_records_are_read_but_reported_as_unrendered(
    tmp_path: Path,
) -> None:
    payload = Payload()
    payload.u4(1).u1(1).u1(1).b1(0x80).b1(0xC0).u4(42).u4(0).u4(0).u4(1)
    data = b"".join((far(), pmr(1, "DQ0"), framed(15, 20, payload)))
    run = parse_bytes(tmp_path, data)
    assert run.failures == ()
    assert any("1 FTR records" in text for text in run.warnings)
    assert any("Tier C" in text for text in run.warnings)


def test_progress_and_cancellation_match_the_logparser_contract(
    tmp_path: Path,
) -> None:
    path = tmp_path / "golden.stdf"
    path.write_bytes(golden_file())

    seen: list[int] = []
    result = StdfParser().parse(
        path, 11, lambda progress: seen.append(progress.job_id)
    )
    assert isinstance(result, ParseComplete)
    assert seen == [11]  # positional on_progress, job_id carried through

    cancelled = StdfParser().parse(path, 12, None, lambda: True)
    assert isinstance(cancelled, ParseFailed)
    assert cancelled.message == "parse cancelled"
    assert cancelled.job_id == 12


def test_stdf_parser_signature_matches_log_parser() -> None:
    """background.py calls one shape; both readers must offer it."""
    import inspect

    expected = inspect.signature(LogParser.parse)
    actual = inspect.signature(StdfParser.parse)
    assert list(actual.parameters) == list(expected.parameters)
    for name, parameter in expected.parameters.items():
        assert actual.parameters[name].kind == parameter.kind
        assert actual.parameters[name].default == parameter.default


def test_parser_for_dispatches_on_suffix(tmp_path: Path) -> None:
    stdf = tmp_path / "a.stdf"
    stdf.write_bytes(golden_file())
    std = tmp_path / "a.std"
    std.write_bytes(golden_file())
    atelog = Path("sample_logs/multi_fail.atelog")

    assert isinstance(parser_for(stdf), StdfParser)
    assert isinstance(parser_for(std), StdfParser)
    assert isinstance(parser_for(atelog), LogParser)


def test_parser_for_sniffs_magic_bytes_for_an_unknown_suffix(
    tmp_path: Path,
) -> None:
    misnamed = tmp_path / "mystery.dat"
    misnamed.write_bytes(golden_file(BIG_ENDIAN))
    assert looks_like_stdf(misnamed) is True
    assert isinstance(parser_for(misnamed), StdfParser)

    text = tmp_path / "mystery2.dat"
    text.write_text("#ATELOG v1.0\n", encoding="utf-8")
    assert looks_like_stdf(text) is False
    assert isinstance(parser_for(text), LogParser)


def test_the_cli_prints_an_fa_summary(tmp_path: Path) -> None:
    from ate_fa_suite.__main__ import main

    path = tmp_path / "golden.stdf"
    path.write_bytes(golden_file())
    assert main(["--stdf", str(path)]) == 0


def test_the_cli_reports_a_bad_file_with_a_nonzero_exit(tmp_path: Path) -> None:
    from ate_fa_suite.__main__ import main

    path = tmp_path / "bad.stdf"
    path.write_bytes(b"nope")
    assert main(["--stdf", str(path)]) == 1
    assert main(["--stdf"]) == 2
    assert main([]) == 0


def test_the_cli_summary_names_the_differential(tmp_path: Path) -> None:
    from ate_fa_suite.__main__ import format_summary

    path = tmp_path / "golden.stdf"
    path.write_bytes(golden_file())
    result = StdfParser().parse(path, job_id=0)
    assert isinstance(result, ParseComplete)
    text = "\n".join(format_summary(path, result))
    assert "STDF-V4-2007" in text
    assert "expected vs captured" in text
    assert "driven waves    0" in text
    assert "DQ0" in text


# --- helpers -----------------------------------------------------------------


def _stream(data: bytes) -> "_Bytes":
    return _Bytes(data)


def _stream_records(data: bytes) -> Iterable[object]:
    return list(iter_records(_Bytes(data), LITTLE_ENDIAN))


class _Bytes:
    """The minimum BinaryIO surface iter_records needs."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, count: int = -1) -> bytes:
        end = len(self._data) if count < 0 else self._pos + count
        chunk = self._data[self._pos : end]
        self._pos += len(chunk)
        return chunk

    def tell(self) -> int:
        return self._pos


def _str_without_cycle_array() -> bytes:
    """An STR whose DATA_FLG marks CYCL_NUM absent, so nothing is addressable."""
    payload = Payload()
    payload.u1(1).u1(1).u4(1).u1(1).u1(1).u2(1).b1(0x80)
    payload.cn("").cn("no_cycles").cn("").cn("").cn("")
    payload.u1(0).b1(0x00)
    payload.u8(10).u4(1).u4(1).u8(0).u2(0)
    payload.b1(data_flg(["PMR_INDX"]))  # CYCL_NUM bit set => absent
    payload.u2(0).u4(1).u2(0)
    payload.u1(4).cn(ALPHABET).u2(1)
    payload.u1(0).u1(0).u1(0).u1(0)
    payload.u2(1)  # PMR_INDX array
    return framed(15, 30, payload)
