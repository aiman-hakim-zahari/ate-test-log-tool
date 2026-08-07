"""Deterministic STDF V4-2007 writer for the shared synthetic run plan.

The production package intentionally contains only a reader.  This writer is a
test and benchmark tool: it encodes the exact same :class:`tools.gen_log.RunPlan`
as the ADF-1 generator, so the two independent readers can be compared through
their common IR projection.

All record payloads are emitted through the declarative schemas in
``parsing.stdf.records``.  The only local schema is MRR, which the current
reader safely ignores but a complete STDF stream should still end with.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Final, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ate_fa_suite.parsing.stdf.codec import (  # noqa: E402
    BIG_ENDIAN,
    LITTLE_ENDIAN,
)
from ate_fa_suite.parsing.stdf.records import (  # noqa: E402
    FAR,
    MIR,
    PIR,
    PMR,
    PRR,
    PSR,
    STR,
    STR_DATA_FLG_BITS,
    Field,
    Gate,
    GateRule,
    RecordSpec,
    Scalar,
)
from tools.gen_log import (  # noqa: E402
    InjectedFailure,
    PlannedBlock,
    RunPlan,
    plan_run,
)

MAX_U1: Final = 0xFF
MAX_U2: Final = 0xFFFF
MAX_U4: Final = 0xFFFFFFFF
MAX_U8: Final = 0xFFFFFFFFFFFFFFFF
MAX_RECORD_PAYLOAD: Final = MAX_U2

HEAD_NUM: Final = 1
SITE_NUM: Final = 1

WAVEFORM_CHARACTERS: Final = frozenset("01XZLH")
DATA_ALPHABETS: Final[dict[int, str]] = {
    1: "01",
    2: "01LH",
    4: "01LHXZ",
    8: "",
}

# MRR is deliberately not part of the reader's selected record subset, but it
# is the standard terminator and gives the truncation golden a harmless final
# payload to cut through.
MRR: Final = RecordSpec(
    name="MRR",
    rec_typ=1,
    rec_sub=20,
    fields=(
        Field("FINISH_T", Scalar.U4),
        Field("DISP_COD", Scalar.C1),
        Field("USR_DESC", Scalar.CN),
        Field("EXC_DESC", Scalar.CN),
    ),
)


@dataclass(frozen=True, slots=True)
class BitPayload:
    """A ``D*n`` value: its authoritative bit count and packed bytes."""

    bit_count: int
    data: bytes


@dataclass(frozen=True, slots=True)
class StdfWriteOptions:
    """Wire choices that do not change the logical generated run.

    ``cyc_base`` defaults to the plan's ``start_vector``.  The same base is
    repeated in every continuation record; the reader intentionally does not
    invent scalar inheritance for that field.
    """

    endian: str = LITTLE_ENDIAN
    data_bit: int = 4
    include_expected: bool = True
    max_failures_per_record: int = 8_000
    cyc_base: int | None = None
    mask_pmr_indices: tuple[int, ...] = ()
    extra_detected_failures: int = 0

    def __post_init__(self) -> None:
        if self.endian not in (LITTLE_ENDIAN, BIG_ENDIAN):
            raise ValueError("endian must be '<' (little) or '>' (big)")
        if self.data_bit not in DATA_ALPHABETS:
            raise ValueError("data_bit must be one of 1, 2, 4, 8")
        if self.include_expected and self.data_bit not in (4, 8):
            raise ValueError("EXP_DATA permits DATA_BIT 4 or 8 only")
        if self.max_failures_per_record < 1:
            raise ValueError("max_failures_per_record must be positive")
        if self.cyc_base is not None and not 0 <= self.cyc_base <= MAX_U8:
            raise ValueError("cyc_base must fit U*8")
        if self.extra_detected_failures < 0:
            raise ValueError("extra_detected_failures must be non-negative")
        if len(set(self.mask_pmr_indices)) != len(self.mask_pmr_indices):
            raise ValueError("mask_pmr_indices must not contain duplicates")
        if any(index < 1 for index in self.mask_pmr_indices):
            raise ValueError("MASK_MAP uses 1-based positive PMR indices")


WireValue = (
    int
    | str
    | bytes
    | BitPayload
    | tuple[int, ...]
    | tuple[str, ...]
)


class _PayloadTooLarge(ValueError):
    """Internal signal used to reduce an STR continuation chunk size."""


_INTEGER_LAYOUTS: Final[dict[Scalar, tuple[str, int, int]]] = {
    Scalar.U1: ("B", 0, MAX_U1),
    Scalar.U2: ("H", 0, MAX_U2),
    Scalar.U4: ("I", 0, MAX_U4),
    Scalar.U8: ("Q", 0, MAX_U8),
    Scalar.I2: ("h", -(2**15), 2**15 - 1),
    Scalar.I4: ("i", -(2**31), 2**31 - 1),
}

_VARIABLE_LAYOUTS: Final[dict[int, tuple[str, int]]] = {
    1: ("B", MAX_U1),
    2: ("H", MAX_U2),
    4: ("I", MAX_U4),
}


def _gate_open(gate: Gate | None, previous: Mapping[str, WireValue]) -> bool:
    if gate is None:
        return True
    raw = previous.get(gate.field)
    if not isinstance(raw, int):
        raise ValueError(f"gate field {gate.field} is not an integer")
    if gate.rule is GateRule.BIT_CLEAR:
        return not bool((raw >> gate.bit) & 1)
    if gate.rule is GateRule.BIT_SET:
        return bool((raw >> gate.bit) & 1)
    if gate.rule is GateRule.NONZERO:
        return raw != 0
    return bool((raw >> gate.bit) & 1) and not bool(
        (raw >> (gate.bit + 1)) & 1
    )


def _latin1(text: str, field_name: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} is not encodable as Latin-1") from exc


def _cn(text: str, field_name: str) -> bytes:
    raw = _latin1(text, field_name)
    if len(raw) > MAX_U1:
        raise ValueError(f"{field_name} exceeds the C*n 255-byte limit")
    return bytes((len(raw),)) + raw


def _integer_bytes(kind: Scalar, value: int, endian: str, name: str) -> bytes:
    layout = _INTEGER_LAYOUTS.get(kind)
    if layout is None:
        raise ValueError(f"{name} is not a fixed integer field")
    code, minimum, maximum = layout
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} value {value} does not fit {kind.value} "
            f"({minimum}..{maximum})"
        )
    return struct.pack(endian + code, value)


def _fixed_integer_bytes(
    value: int, width: int, endian: str, name: str
) -> bytes:
    layout = _VARIABLE_LAYOUTS.get(width)
    if layout is None:
        raise ValueError(f"{name} width {width} is not one of 1, 2, 4")
    code, maximum = layout
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} value {value} does not fit U*{width}")
    return struct.pack(endian + code, value)


def _integer_array_bytes(
    kind: Scalar,
    values: tuple[int, ...],
    endian: str,
    name: str,
) -> bytes:
    layout = _INTEGER_LAYOUTS.get(kind)
    if layout is None:
        raise ValueError(f"{name} is not a fixed integer array")
    code, minimum, maximum = layout
    if any(not minimum <= value <= maximum for value in values):
        raise ValueError(f"an element of {name} does not fit {kind.value}")
    if not values:
        return b""
    return struct.pack(f"{endian}{len(values)}{code}", *values)


def _nibbles(values: tuple[int, ...], name: str) -> bytes:
    if any(not 0 <= value <= 0x0F for value in values):
        raise ValueError(f"{name} N*1 elements must be in 0..15")
    output = bytearray((len(values) + 1) // 2)
    for index, value in enumerate(values):
        output[index // 2] |= value << (4 * (index % 2))
    return bytes(output)


def _as_int_tuple(value: WireValue, name: str) -> tuple[int, ...]:
    if isinstance(value, bytes):
        return tuple(value)
    if not isinstance(value, tuple) or any(
        not isinstance(item, int) for item in value
    ):
        raise ValueError(f"{name} must be an integer tuple or bytes")
    return tuple(int(item) for item in value)


def _as_str_tuple(value: WireValue, name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a string tuple")
    return tuple(str(item) for item in value)


def _encode_scalar(
    spec_field: Field,
    value: WireValue,
    width: int,
    endian: str,
) -> bytes:
    name = spec_field.name
    kind = spec_field.kind
    if kind in _INTEGER_LAYOUTS:
        if not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return _integer_bytes(kind, value, endian, name)
    if kind is Scalar.UF:
        if not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return _fixed_integer_bytes(value, width, endian, name)
    if kind is Scalar.C1:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a character")
        raw = _latin1(value, name)
        if len(raw) != 1:
            raise ValueError(f"{name} C*1 must encode to exactly one byte")
        return raw
    if kind is Scalar.CN:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return _cn(value, name)
    if kind is Scalar.CF:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        raw = _latin1(value, name)
        if len(raw) != width:
            raise ValueError(f"{name} must encode to exactly {width} bytes")
        return raw
    if kind is Scalar.B1:
        if not isinstance(value, int) or not 0 <= value <= MAX_U1:
            raise ValueError(f"{name} B*1 must be an integer in 0..255")
        return bytes((value,))
    if kind is Scalar.BN:
        if not isinstance(value, bytes):
            raise ValueError(f"{name} B*n must be bytes")
        if len(value) > MAX_U1:
            raise ValueError(f"{name} exceeds the B*n 255-byte limit")
        return bytes((len(value),)) + value
    if kind is Scalar.DN:
        if not isinstance(value, BitPayload):
            raise ValueError(f"{name} D*n must be a BitPayload")
        if not 0 <= value.bit_count <= MAX_U2:
            raise ValueError(f"{name} D*n bit count must fit U*2")
        wanted = (value.bit_count + 7) // 8
        if len(value.data) != wanted:
            raise ValueError(
                f"{name} declares {value.bit_count} bits but has "
                f"{len(value.data)} bytes; expected {wanted}"
            )
        if value.bit_count % 8 and value.data:
            used_mask = (1 << (value.bit_count % 8)) - 1
            if value.data[-1] & ~used_mask:
                raise ValueError(f"{name} has non-zero bits above bit_count")
        return _integer_bytes(
            Scalar.U2, value.bit_count, endian, f"{name}.bit_count"
        ) + value.data
    if kind is Scalar.N1:
        if not isinstance(value, int):
            raise ValueError(f"{name} must be a nibble")
        return _nibbles((value,), name)
    raise ValueError(f"unsupported scalar type {kind.value} for {name}")


def _encode_array(
    spec_field: Field,
    value: WireValue,
    count: int,
    width: int,
    endian: str,
) -> bytes:
    name = spec_field.name
    kind = spec_field.kind
    if kind is Scalar.CN:
        strings = _as_str_tuple(value, name)
        if len(strings) != count:
            raise ValueError(f"{name} needs {count} elements, got {len(strings)}")
        return b"".join(_cn(item, name) for item in strings)
    if kind is Scalar.CF:
        strings = _as_str_tuple(value, name)
        if len(strings) != count:
            raise ValueError(f"{name} needs {count} elements, got {len(strings)}")
        output = bytearray()
        for item in strings:
            raw = _latin1(item, name)
            if len(raw) != width:
                raise ValueError(
                    f"each {name} element must encode to exactly {width} bytes"
                )
            output += raw
        return bytes(output)

    integers = _as_int_tuple(value, name)
    if len(integers) != count:
        raise ValueError(f"{name} needs {count} elements, got {len(integers)}")
    if kind is Scalar.N1:
        return _nibbles(integers, name)
    if kind is Scalar.UF:
        return b"".join(
            _fixed_integer_bytes(item, width, endian, name) for item in integers
        )
    return _integer_array_bytes(kind, integers, endian, name)


def _record(
    spec: RecordSpec, values: Mapping[str, WireValue], endian: str
) -> bytes:
    """Encode one record in schema order, rejecting omissions and drift."""
    previous: dict[str, WireValue] = {}
    consumed: set[str] = set()
    payload = bytearray()

    for spec_field in spec.fields:
        present = _gate_open(spec_field.gate, previous)
        if not present:
            if spec_field.name in values:
                raise ValueError(
                    f"{spec.name}.{spec_field.name} was supplied while gated absent"
                )
            continue
        if spec_field.name not in values:
            raise ValueError(f"missing {spec.name}.{spec_field.name}")

        value = values[spec_field.name]
        consumed.add(spec_field.name)
        width = 0
        if spec_field.width_from is not None:
            raw_width = previous.get(spec_field.width_from)
            if not isinstance(raw_width, int):
                raise ValueError(
                    f"{spec.name}.{spec_field.width_from} is not an integer width"
                )
            width = raw_width

        if spec_field.count_from is None:
            payload += _encode_scalar(spec_field, value, width, endian)
        else:
            raw_count = previous.get(spec_field.count_from)
            if not isinstance(raw_count, int):
                raise ValueError(
                    f"{spec.name}.{spec_field.count_from} is not an integer count"
                )
            payload += _encode_array(
                spec_field, value, raw_count, width, endian
            )
        previous[spec_field.name] = value

    extras = set(values) - consumed
    if extras:
        names = ", ".join(sorted(extras))
        raise ValueError(f"unknown or gated fields for {spec.name}: {names}")
    if len(payload) > MAX_RECORD_PAYLOAD:
        raise _PayloadTooLarge(
            f"{spec.name} payload is {len(payload)} bytes; REC_LEN permits "
            f"at most {MAX_RECORD_PAYLOAD}"
        )
    header = struct.pack(endian + "HBB", len(payload), spec.rec_typ, spec.rec_sub)
    return header + payload


def _timestamp(date: str) -> int:
    normalized = date[:-1] + "+00:00" if date.endswith("Z") else date
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"plan header date {date!r} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    stamp = int(parsed.timestamp())
    if not 0 <= stamp <= MAX_U4:
        raise ValueError("plan header date does not fit STDF U*4 epoch time")
    return stamp


def _validate_plan(plan: RunPlan, options: StdfWriteOptions) -> None:
    if not plan.blocks:
        raise ValueError("RunPlan must contain at least one block")
    if sum(block.cycle_count for block in plan.blocks) != plan.total_cycles:
        raise ValueError("RunPlan total_cycles disagrees with its blocks")
    if tuple(f for block in plan.blocks for f in block.failures) != plan.failures:
        raise ValueError("RunPlan failures disagree with its block failures")
    if len(plan.pin_defs) > MAX_U2:
        raise ValueError("STDF PMR_INDX supports at most 65535 pins")
    if len(plan.blocks) > MAX_U2:
        raise ValueError("STDF PSR_INDX supports at most 65535 blocks")
    if len({name for name, _ in plan.pin_defs}) != len(plan.pin_defs):
        raise ValueError("RunPlan pin names must be unique")

    pin_names = {name for name, _ in plan.pin_defs}
    name_occurrences: dict[str, int] = {}
    for block in plan.blocks:
        name_occurrences[block.name] = name_occurrences.get(block.name, 0) + 1
        if block.occurrence != name_occurrences[block.name]:
            raise ValueError(
                "block occurrences must match document order for STDF mapping"
            )
        if block.cycle_count < 1:
            raise ValueError("each planned block must contain at least one cycle")
        seen: set[tuple[int, str]] = set()
        for failure in block.failures:
            if (failure.block, failure.occurrence) != (
                block.name,
                block.occurrence,
            ):
                raise ValueError("failure block identity disagrees with its block")
            if failure.pin not in pin_names:
                raise ValueError(f"failure pin {failure.pin!r} has no PMR")
            if not (
                plan.start_vector
                <= failure.vector
                < plan.start_vector + block.cycle_count
            ):
                raise ValueError("failure vector is outside its planned block")
            if failure.time != (failure.vector - plan.start_vector) * plan.period:
                raise ValueError("failure time disagrees with vector and period")
            if failure.expected not in WAVEFORM_CHARACTERS:
                raise ValueError(f"unsupported expected state {failure.expected!r}")
            if failure.actual not in WAVEFORM_CHARACTERS:
                raise ValueError(f"unsupported captured state {failure.actual!r}")
            identity = (failure.vector, failure.pin)
            if identity in seen:
                raise ValueError("duplicate failure for one pin and vector")
            seen.add(identity)

    if any(index > len(plan.pin_defs) for index in options.mask_pmr_indices):
        raise ValueError("MASK_MAP names a PMR index outside plan.pin_defs")
    base = plan.start_vector if options.cyc_base is None else options.cyc_base
    if base < 0:
        raise ValueError("CYC_BASE must be non-negative")
    if plan.failures and min(f.vector for f in plan.failures) < base:
        raise ValueError("CYC_BASE cannot exceed a logged failure vector")
    if plan.failures and max(f.vector - base for f in plan.failures) > MAX_U4:
        raise ValueError("CYCL_NUM offset does not fit U*4")


def _mir_values(plan: RunPlan, stamp: int) -> dict[str, WireValue]:
    values: dict[str, WireValue] = {
        spec_field.name: ""
        for spec_field in MIR.fields
        if spec_field.kind is Scalar.CN
    }
    values.update(
        {
            "SETUP_T": stamp,
            "START_T": stamp,
            "STAT_NUM": 1,
            "MODE_COD": "P",
            "RTST_COD": " ",
            "PROT_COD": " ",
            "BURN_TIM": MAX_U2,
            "CMOD_COD": " ",
            "LOT_ID": plan.header.lot,
            "PART_TYP": plan.header.device,
            "NODE_NAM": plan.header.tester,
            "TSTR_TYP": plan.header.tester,
            "JOB_NAM": plan.header.program,
            "SBLOT_ID": plan.header.wafer,
        }
    )
    return values


def _pmr_values(index: int, pin_name: str) -> dict[str, WireValue]:
    return {
        "PMR_INDX": index,
        "CHAN_TYP": 0,
        "CHAN_NAM": f"ch{index}",
        "PHY_NAM": pin_name,
        "LOG_NAM": pin_name,
        "HEAD_NUM": HEAD_NUM,
        "SITE_NUM": SITE_NUM,
    }


def _psr_values(
    index: int, block: PlannedBlock, start_vector: int
) -> dict[str, WireValue]:
    return {
        "REC_INDX": 1,
        "REC_TOT": 1,
        "PSR_INDX": index,
        "PSR_NAM": block.name,
        "OPT_FLG": 0x0F,
        "TOTP_CNT": 1,
        "LOCP_CNT": 1,
        "PAT_BGN": (start_vector,),
        "PAT_END": (start_vector + block.cycle_count - 1,),
        "PAT_FILE": (f"{block.name}.stil",),
    }


def _bitmap(bit_count: int, one_based_indices: Sequence[int]) -> BitPayload:
    output = bytearray((bit_count + 7) // 8)
    for one_based in one_based_indices:
        zero_based = one_based - 1
        output[zero_based // 8] |= 1 << (zero_based % 8)
    return BitPayload(bit_count=bit_count, data=bytes(output))


def _data_flag(present: set[str]) -> int:
    absent = set(STR_DATA_FLG_BITS) - present
    return sum(1 << STR_DATA_FLG_BITS[name] for name in absent)


def _expected_data_bytes(count: int, data_bit: int) -> int:
    return (count * data_bit + 7) // 8


def _pack_states(characters: Sequence[str], data_bit: int) -> bytes:
    alphabet = DATA_ALPHABETS[data_bit]
    if data_bit == 8:
        if any(character not in WAVEFORM_CHARACTERS for character in characters):
            raise ValueError("DATA_BIT 8 received an unsupported waveform state")
        return "".join(characters).encode("ascii")

    output = bytearray(_expected_data_bytes(len(characters), data_bit))
    per_byte = 8 // data_bit
    for index, character in enumerate(characters):
        try:
            value = alphabet.index(character)
        except ValueError as exc:
            raise ValueError(
                f"waveform state {character!r} is not representable at "
                f"DATA_BIT {data_bit} with DATA_CHR {alphabet!r}"
            ) from exc
        byte_index, slot = divmod(index, per_byte)
        output[byte_index] |= value << (slot * data_bit)
    return bytes(output)


def _str_values(
    *,
    plan: RunPlan,
    block: PlannedBlock,
    psr_index: int,
    failures: tuple[InjectedFailure, ...],
    rec_index: int,
    rec_total: int,
    options: StdfWriteOptions,
    pin_indices: Mapping[str, int],
) -> dict[str, WireValue]:
    base = plan.start_vector if options.cyc_base is None else options.cyc_base
    cycles = tuple(failure.vector - base for failure in failures)
    pins = tuple(pin_indices[failure.pin] for failure in failures)
    captured = _pack_states(
        tuple(failure.actual for failure in failures), options.data_bit
    )
    expected = (
        _pack_states(
            tuple(failure.expected for failure in failures), options.data_bit
        )
        if options.include_expected
        else None
    )
    data_count = _expected_data_bytes(len(failures), options.data_bit)
    if len(captured) != data_count or (
        expected is not None and len(expected) != data_count
    ):
        raise ValueError("internal packed-state length disagreement")

    present = {"CYCL_NUM", "PMR_INDX", "CAP_DATA"}
    if options.include_expected:
        present.add("EXP_DATA")

    has_mask = rec_index == 1 and bool(options.mask_pmr_indices)
    values: dict[str, WireValue] = {
        "REC_INDX": rec_index,
        "REC_TOT": rec_total,
        "TEST_NUM": psr_index,
        "HEAD_NUM": HEAD_NUM,
        "SITE_NUM": SITE_NUM,
        "PSR_REF": psr_index,
        "TEST_FLG": 0x80 if block.failures else 0,
        "LOG_TYP": "",
        "TEST_TXT": block.name,
        "ALARM_ID": "",
        "PROG_TXT": "",
        "RSLT_TXT": "",
        "Z_VAL": 0,
        "FMU_FLG": 0x04 if has_mask else 0,
        "CYC_CNT": block.cycle_count,
        "TOTF_CNT": len(block.failures) + options.extra_detected_failures,
        "TOTL_CNT": len(block.failures),
        "CYC_BASE": base,
        "BIT_BASE": 0,
        "DATA_FLG": _data_flag(present),
        "COND_CNT": 0,
        "LOCL_CNT": len(failures),
        "LIM_CNT": 0,
        "DATA_BIT": options.data_bit,
        "DATA_CHR": DATA_ALPHABETS[options.data_bit],
        "DATA_CNT": data_count,
        "USR1_LEN": 0,
        "USR2_LEN": 0,
        "USR3_LEN": 0,
        "TXT_LEN": 0,
        "LIM_INDX": (),
        "LIM_SPEC": (),
        "COND_NAM": (),
        "COND_VAL": (),
        "CYCL_NUM": cycles,
        "PMR_INDX": pins,
        "CAP_DATA": tuple(captured),
    }
    if has_mask:
        values["MASK_MAP"] = _bitmap(
            len(plan.pin_defs), sorted(options.mask_pmr_indices)
        )
    if expected is not None:
        values["EXP_DATA"] = tuple(expected)
    return values


def _chunks(
    failures: tuple[InjectedFailure, ...], size: int
) -> tuple[tuple[InjectedFailure, ...], ...]:
    if not failures:
        return ((),)
    return tuple(
        failures[start : start + size]
        for start in range(0, len(failures), size)
    )


def _str_set(
    plan: RunPlan,
    block: PlannedBlock,
    psr_index: int,
    options: StdfWriteOptions,
    pin_indices: Mapping[str, int],
) -> bytes:
    # DATA_CNT is U*2 and counts packed bytes.  Cap before encoding so an
    # intentionally huge caller limit is reduced rather than failing before
    # the record-size retry below gets a chance to split it.
    max_by_data_count = (MAX_U2 * 8) // options.data_bit
    chunk_size = min(
        options.max_failures_per_record,
        max_by_data_count,
        max(len(block.failures), 1),
    )

    while True:
        chunks = _chunks(block.failures, chunk_size)
        if len(chunks) > MAX_U1:
            raise ValueError(
                f"block {block.name!r} needs {len(chunks)} STR records; "
                "REC_TOT permits at most 255"
            )
        try:
            return b"".join(
                _record(
                    STR,
                    _str_values(
                        plan=plan,
                        block=block,
                        psr_index=psr_index,
                        failures=chunk,
                        rec_index=index,
                        rec_total=len(chunks),
                        options=options,
                        pin_indices=pin_indices,
                    ),
                    options.endian,
                )
                for index, chunk in enumerate(chunks, 1)
            )
        except _PayloadTooLarge:
            if chunk_size == 1:
                raise
            chunk_size = max(1, chunk_size // 2)


def encode_stdf(
    plan: RunPlan, options: StdfWriteOptions | None = None
) -> bytes:
    """Encode ``plan`` as one complete STDF V4-2007 byte stream."""
    selected = StdfWriteOptions() if options is None else options
    _validate_plan(plan, selected)
    stamp = _timestamp(plan.header.date)
    pin_indices = {
        pin_name: index
        for index, (pin_name, _) in enumerate(plan.pin_defs, start=1)
    }

    records: list[bytes] = [
        _record(
            FAR,
            {
                "CPU_TYPE": 2 if selected.endian == LITTLE_ENDIAN else 1,
                "STDF_VER": 4,
            },
            selected.endian,
        ),
        _record(MIR, _mir_values(plan, stamp), selected.endian),
    ]
    records.extend(
        _record(
            PMR,
            _pmr_values(index, pin_name),
            selected.endian,
        )
        for index, (pin_name, _) in enumerate(plan.pin_defs, start=1)
    )
    records.extend(
        _record(
            PSR,
            _psr_values(index, block, plan.start_vector),
            selected.endian,
        )
        for index, block in enumerate(plan.blocks, start=1)
    )
    records.append(
        _record(
            PIR,
            {"HEAD_NUM": HEAD_NUM, "SITE_NUM": SITE_NUM},
            selected.endian,
        )
    )
    records.extend(
        _str_set(plan, block, index, selected, pin_indices)
        for index, block in enumerate(plan.blocks, start=1)
    )
    records.append(
        _record(
            PRR,
            {
                "HEAD_NUM": HEAD_NUM,
                "SITE_NUM": SITE_NUM,
                "PART_FLG": 0x08,
                "NUM_TEST": len(plan.blocks),
                "HARD_BIN": 5 if plan.failures else 1,
                "SOFT_BIN": 5 if plan.failures else 1,
                "X_COORD": -(2**15),
                "Y_COORD": -(2**15),
                "TEST_T": 0,
                "PART_ID": f"SYNTH-{plan.seed}",
                "PART_TXT": "",
                "PART_FIX": b"",
            },
            selected.endian,
        )
    )
    records.append(
        _record(
            MRR,
            {
                "FINISH_T": stamp,
                "DISP_COD": " ",
                "USR_DESC": "generated by tools/stdf_writer.py",
                "EXC_DESC": "",
            },
            selected.endian,
        )
    )
    return b"".join(records)


def write_stdf(
    destination: Path | str | BinaryIO,
    plan: RunPlan,
    options: StdfWriteOptions | None = None,
) -> int:
    """Write STDF bytes to a path or binary sink and return the byte count."""
    data = encode_stdf(plan, options)
    if isinstance(destination, (str, Path)):
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return len(data)

    written = destination.write(data)
    if written != len(data):
        raise OSError(
            f"binary sink accepted {written} of {len(data)} STDF bytes"
        )
    return written


def write_golden_corpus(directory: Path | str) -> tuple[Path, ...]:
    """Write the deterministic feature corpus required by Phase 5 M8."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def emit(name: str, plan: RunPlan, options: StdfWriteOptions) -> None:
        path = target / name
        write_stdf(path, plan, options)
        written.append(path)

    pair_plan = plan_run(
        cycles=24,
        pins=9,
        fail_rate=0.25,
        seed=42,
        blocks=3,
        start_vector=1_000,
    )
    emit("golden_le.stdf", pair_plan, StdfWriteOptions())
    emit(
        "golden_be.stdf",
        pair_plan,
        StdfWriteOptions(endian=BIG_ENDIAN),
    )

    continuation_plan = plan_run(
        cycles=3,
        pins=3,
        fail_rate=1.0,
        seed=7,
        start_vector=100,
    )
    emit(
        "continuation.stdf",
        continuation_plan,
        StdfWriteOptions(max_failures_per_record=1),
    )

    variants_plan = plan_run(
        cycles=4,
        pins=3,
        fail_rate=1.0,
        seed=7,
        start_vector=20,
    )
    for data_bit in (1, 2, 4, 8):
        emit(
            f"data_bit_{data_bit}.stdf",
            variants_plan,
            StdfWriteOptions(
                data_bit=data_bit,
                include_expected=data_bit in (4, 8),
            ),
        )

    base_plan = plan_run(
        cycles=6,
        pins=5,
        fail_rate=0.7,
        seed=11,
        start_vector=1_000_000,
    )
    emit(
        "nonzero_cyc_base.stdf",
        base_plan,
        StdfWriteOptions(cyc_base=1_000_000),
    )

    mask_plan = plan_run(
        cycles=2,
        pins=9,
        fail_rate=1.0,
        seed=7,
        start_vector=50,
    )
    emit(
        "mask_map_9pin.stdf",
        mask_plan,
        StdfWriteOptions(mask_pmr_indices=(9,)),
    )
    emit(
        "missing_exp_data.stdf",
        variants_plan,
        StdfWriteOptions(include_expected=False),
    )
    emit(
        "fail_memory_truncated.stdf",
        variants_plan,
        StdfWriteOptions(extra_detected_failures=17),
    )

    complete = encode_stdf(pair_plan)
    if not complete:
        raise AssertionError("the complete golden unexpectedly encoded empty")
    truncated_path = target / "truncated.stdf"
    truncated_path.write_bytes(complete[:-1])
    written.append(truncated_path)
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stdf_writer",
        description="Generate deterministic STDF V4-2007 STR fixtures.",
    )
    parser.add_argument("--cycles", type=int, default=5_000)
    parser.add_argument("--pins", type=int, default=8)
    parser.add_argument("--fail-rate", type=float, default=0.001)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--blocks", type=int, default=1)
    parser.add_argument("--period", type=int, default=1_000)
    parser.add_argument("--start-vector", type=int, default=1)
    parser.add_argument("--endian", choices=("little", "big"), default="little")
    parser.add_argument("--data-bit", choices=(1, 2, 4, 8), type=int, default=4)
    parser.add_argument("--omit-expected", action="store_true")
    parser.add_argument("--max-failures-per-record", type=int, default=8_000)
    parser.add_argument("--cyc-base", type=int, default=None)
    parser.add_argument("--mask-pmr", type=int, nargs="*", default=[])
    parser.add_argument("--extra-detected-failures", type=int, default=0)
    parser.add_argument("--golden-corpus", type=Path, default=None)
    parser.add_argument("-o", "--output", type=Path, default=Path("generated.stdf"))
    args = parser.parse_args(argv)

    if args.golden_corpus is not None:
        paths = write_golden_corpus(args.golden_corpus)
        for path in paths:
            print(path)
        return 0

    plan = plan_run(
        cycles=args.cycles,
        pins=args.pins,
        fail_rate=args.fail_rate,
        seed=args.seed,
        blocks=args.blocks,
        period=args.period,
        start_vector=args.start_vector,
    )
    options = StdfWriteOptions(
        endian=LITTLE_ENDIAN if args.endian == "little" else BIG_ENDIAN,
        data_bit=args.data_bit,
        include_expected=not args.omit_expected,
        max_failures_per_record=args.max_failures_per_record,
        cyc_base=args.cyc_base,
        mask_pmr_indices=tuple(args.mask_pmr),
        extra_detected_failures=args.extra_detected_failures,
    )
    byte_count = write_stdf(args.output, plan, options)
    print(
        f"{args.output}: {byte_count} bytes, {len(plan.failures)} logged failures, "
        f"{args.endian}-endian, DATA_BIT {args.data_bit}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BIG_ENDIAN",
    "LITTLE_ENDIAN",
    "MRR",
    "BitPayload",
    "StdfWriteOptions",
    "encode_stdf",
    "main",
    "write_golden_corpus",
    "write_stdf",
]
