"""Endian-bound decoding of STDF scalars, arrays, and whole records.

Four things here are the classic STDF decoder bugs, so each is spelled out:

* ``D*n`` carries a **2-byte count of bits** and consumes ``ceil(bits / 8)``
  bytes.  ``B*n`` carries a **1-byte count of bytes**.  ``B*1`` is a single
  fixed byte with no prefix at all.  ``MASK_MAP`` and ``FAL_MAP`` are ``D*n``;
  ``DATA_FLG``, ``TEST_FLG`` and ``FMU_FLG`` are ``B*1``.
* Trailing-field truncation is legal: a record may stop after any field.  Every
  accessor returns ``None`` past the end and never raises, and
  :func:`decode_record` records where it stopped instead of failing.
* Arrays are read with one batched :meth:`struct.Struct.unpack_from` call, not
  ``k`` scalar calls.  With ``LOCL_CNT`` in the thousands that is the
  difference between usable and not.
* ``C*n`` decodes as ``latin-1``, which cannot raise.  A record containing a
  byte above ``0x7F`` is flagged once, not once per field.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final, Mapping

from ate_fa_suite.parsing.stdf.records import (
    Field,
    Gate,
    GateRule,
    RecordSpec,
    Scalar,
)

#: ``struct`` byte-order prefixes.
LITTLE_ENDIAN: Final = "<"
BIG_ENDIAN: Final = ">"

#: Bit order inside ``B*n`` and ``D*n`` maps.  The spec is explicit: "First
#: data item in least significant bit of the third byte of the array".
BITMAP_LSB_FIRST: Final = True

_CODES: Final[dict[Scalar, str]] = {
    Scalar.U1: "B",
    Scalar.U2: "H",
    Scalar.U4: "I",
    Scalar.U8: "Q",
    Scalar.I2: "h",
    Scalar.I4: "i",
}

_WIDTHS: Final[dict[Scalar, int]] = {
    Scalar.U1: 1,
    Scalar.U2: 2,
    Scalar.U4: 4,
    Scalar.U8: 8,
    Scalar.I2: 2,
    Scalar.I4: 4,
}

#: ``U*f`` widths the spec allows (Table 9), mapped to their ``struct`` code.
_VARIABLE_WIDTH_CODES: Final[dict[int, str]] = {1: "B", 2: "H", 4: "I"}


@lru_cache(maxsize=1024)
def packed(fmt: str) -> struct.Struct:
    """Return a compiled ``struct.Struct``, reused across records."""
    return struct.Struct(fmt)


@dataclass(frozen=True, slots=True)
class BitMap:
    """A ``B*n`` or ``D*n`` payload, kept as bits rather than an int.

    ``bit_count`` is authoritative: for ``D*n`` it comes from the wire and can
    be smaller than ``8 * len(data)``.
    """

    bit_count: int
    data: bytes

    def is_set(self, index: int) -> bool:
        """Test one 0-based bit, least-significant bit of byte 0 first."""
        if not 0 <= index < self.bit_count:
            return False
        byte, bit = divmod(index, 8)
        if byte >= len(self.data):
            return False
        return bool((self.data[byte] >> bit) & 1)

    def set_bits(self) -> tuple[int, ...]:
        """Every set bit's 0-based index, ascending."""
        return tuple(i for i in range(self.bit_count) if self.is_set(i))


#: Everything a decoded field can hold.
FieldValue = (
    int | str | bytes | BitMap | tuple[int, ...] | tuple[str, ...]
)


@dataclass(frozen=True, slots=True)
class DecodedRecord:
    """One record's fields, plus what the decoder noticed while reading it."""

    spec: RecordSpec
    values: Mapping[str, FieldValue] = field(default_factory=dict)
    #: The first field that did not fit, or ``None`` if the record was whole.
    truncated_at: str | None = None
    #: Set when any ``C*n`` in this record held a byte above ``0x7F``.
    non_ascii: bool = False
    #: Set when bytes remained after the last schema field was read.
    trailing_bytes: int = 0

    def get_int(self, name: str) -> int | None:
        value = self.values.get(name)
        return value if isinstance(value, int) else None

    def get_str(self, name: str) -> str | None:
        value = self.values.get(name)
        return value if isinstance(value, str) else None

    def get_map(self, name: str) -> BitMap | None:
        value = self.values.get(name)
        return value if isinstance(value, BitMap) else None

    def get_ints(self, name: str) -> tuple[int, ...] | None:
        value = self.values.get(name)
        if isinstance(value, tuple) and all(isinstance(v, int) for v in value):
            # An empty tuple is a legitimately empty array, not a miss.
            return tuple(int(v) for v in value)
        return None

    def get_strs(self, name: str) -> tuple[str, ...] | None:
        value = self.values.get(name)
        if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
            return tuple(str(v) for v in value)
        return None

    def get_bytes(self, name: str) -> bytes | None:
        value = self.values.get(name)
        return value if isinstance(value, bytes) else None


class Cursor:
    """A non-raising read head over one record's payload."""

    __slots__ = ("_data", "_endian", "_pos", "non_ascii")

    def __init__(self, data: bytes, endian: str) -> None:
        self._data = data
        self._endian = endian
        self._pos = 0
        self.non_ascii = False

    @property
    def position(self) -> int:
        return self._pos

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def _take(self, count: int) -> bytes | None:
        """Consume ``count`` bytes, or nothing at all if they are not there."""
        if count < 0 or self.remaining < count:
            return None
        start = self._pos
        self._pos += count
        return self._data[start : self._pos]

    # -- scalars --

    def integer(self, kind: Scalar) -> int | None:
        code = _CODES.get(kind)
        if code is None:
            return None
        raw = self._take(_WIDTHS[kind])
        if raw is None:
            return None
        return int(packed(self._endian + code).unpack(raw)[0])

    def variable_integer(self, width: int) -> int | None:
        """A ``U*f`` scalar whose width another field supplied."""
        code = _VARIABLE_WIDTH_CODES.get(width)
        if code is None:
            return None
        raw = self._take(width)
        if raw is None:
            return None
        return int(packed(self._endian + code).unpack(raw)[0])

    def fixed_char(self) -> str | None:
        """``C*1``: exactly one character, no prefix."""
        raw = self._take(1)
        if raw is None:
            return None
        return self._decode(raw)

    def string(self) -> str | None:
        """``C*n``: a 1-byte length prefix, then that many bytes."""
        length = self._take(1)
        if length is None:
            return None
        raw = self._take(length[0])
        if raw is None:
            return None
        return self._decode(raw)

    def fixed_string(self, width: int) -> str | None:
        """``C*f``: a fixed width another field supplied."""
        raw = self._take(width)
        if raw is None:
            return None
        return self._decode(raw)

    def flag_byte(self) -> int | None:
        """``B*1``: one fixed byte of flags, with no length prefix."""
        raw = self._take(1)
        if raw is None:
            return None
        return raw[0]

    def byte_map(self) -> BitMap | None:
        """``B*n``: a 1-byte prefix counting BYTES."""
        length = self._take(1)
        if length is None:
            return None
        raw = self._take(length[0])
        if raw is None:
            return None
        return BitMap(bit_count=8 * len(raw), data=raw)

    def bit_map(self) -> BitMap | None:
        """``D*n``: a 2-byte prefix counting BITS, consuming ceil(bits / 8)."""
        bits = self.integer(Scalar.U2)
        if bits is None:
            return None
        raw = self._take(-(-bits // 8))  # ceil without importing math
        if raw is None:
            return None
        return BitMap(bit_count=bits, data=raw)

    # -- arrays, each in one unpack_from call --

    def integer_array(self, kind: Scalar, count: int) -> tuple[int, ...] | None:
        code = _CODES.get(kind)
        if code is None or count < 0:
            return None
        return self._unpack_many(code, _WIDTHS[kind], count)

    def variable_integer_array(
        self, width: int, count: int
    ) -> tuple[int, ...] | None:
        code = _VARIABLE_WIDTH_CODES.get(width)
        if code is None or count < 0:
            return None
        return self._unpack_many(code, width, count)

    def _unpack_many(
        self, code: str, width: int, count: int
    ) -> tuple[int, ...] | None:
        raw = self._take(width * count)
        if raw is None:
            return None
        if count == 0:
            return ()
        layout = packed(f"{self._endian}{count}{code}")
        return tuple(int(v) for v in layout.unpack(raw))

    def nibble_array(self, count: int) -> tuple[int, ...] | None:
        """``N*1``: two values per byte, low nibble first."""
        if count < 0:
            return None
        raw = self._take(-(-count // 2))
        if raw is None:
            return None
        values: list[int] = []
        for byte in raw:
            values.append(byte & 0x0F)
            values.append(byte >> 4)
        return tuple(values[:count])

    def string_array(self, count: int) -> tuple[str, ...] | None:
        """``kxC*n``: each element carries its own length prefix."""
        if count < 0:
            return None
        values: list[str] = []
        for _ in range(count):
            value = self.string()
            if value is None:
                return None
            values.append(value)
        return tuple(values)

    def fixed_string_array(
        self, width: int, count: int
    ) -> tuple[str, ...] | None:
        """``kxC*f``: fixed-width elements, no per-element prefix."""
        if count < 0 or width < 0:
            return None
        raw = self._take(width * count)
        if raw is None:
            return None
        return tuple(
            self._decode(raw[i * width : (i + 1) * width])
            for i in range(count)
        )

    def _decode(self, raw: bytes) -> str:
        if any(byte > 0x7F for byte in raw):
            self.non_ascii = True
        return raw.decode("latin-1")


# --- schema-driven record decoding -------------------------------------------


def gate_open(gate: Gate | None, values: Mapping[str, FieldValue]) -> bool:
    """Decide whether a gated field is present, from earlier field values."""
    if gate is None:
        return True
    raw = values.get(gate.field)
    if not isinstance(raw, int):
        return False  # the gating field is missing, so the gated one is too
    if gate.rule is GateRule.BIT_CLEAR:
        return not (raw >> gate.bit) & 1
    if gate.rule is GateRule.BIT_SET:
        return bool((raw >> gate.bit) & 1)
    if gate.rule is GateRule.NONZERO:
        return raw != 0
    # MAP_PRESENT, per FMU_FLG Tables 6 and 7: the pair must read (1, 0).
    return bool((raw >> gate.bit) & 1) and not (raw >> (gate.bit + 1)) & 1


def _read_scalar(cursor: Cursor, spec: Field, width: int) -> FieldValue | None:
    if spec.kind is Scalar.CN:
        return cursor.string()
    if spec.kind is Scalar.C1:
        return cursor.fixed_char()
    if spec.kind is Scalar.CF:
        return cursor.fixed_string(width)
    if spec.kind is Scalar.B1:
        return cursor.flag_byte()
    if spec.kind is Scalar.BN:
        return cursor.byte_map()
    if spec.kind is Scalar.DN:
        return cursor.bit_map()
    if spec.kind is Scalar.UF:
        return cursor.variable_integer(width)
    if spec.kind is Scalar.N1:
        return cursor.nibble_array(1)
    return cursor.integer(spec.kind)


def _read_array(
    cursor: Cursor, spec: Field, count: int, width: int
) -> FieldValue | None:
    if spec.kind is Scalar.CN:
        return cursor.string_array(count)
    if spec.kind is Scalar.CF:
        return cursor.fixed_string_array(width, count)
    if spec.kind is Scalar.N1:
        return cursor.nibble_array(count)
    if spec.kind is Scalar.UF:
        return cursor.variable_integer_array(width, count)
    return cursor.integer_array(spec.kind, count)


def decode_record(
    spec: RecordSpec, payload: bytes, endian: str
) -> DecodedRecord:
    """Decode one record payload against its schema.

    Stops at the first field that does not fit and reports it in
    ``truncated_at``.  That is the legal trailing-truncation case, so it is
    never an error here; the caller decides whether the missing field mattered.
    """
    cursor = Cursor(payload, endian)
    values: dict[str, FieldValue] = {}
    truncated_at: str | None = None

    for spec_field in spec.fields:
        if not gate_open(spec_field.gate, values):
            continue

        width = 0
        if spec_field.width_from is not None:
            named = values.get(spec_field.width_from)
            width = named if isinstance(named, int) else 0

        if spec_field.count_from is None:
            value = _read_scalar(cursor, spec_field, width)
        else:
            named_count = values.get(spec_field.count_from)
            if not isinstance(named_count, int):
                truncated_at = spec_field.name
                break
            value = _read_array(cursor, spec_field, named_count, width)

        if value is None:
            truncated_at = spec_field.name
            break
        values[spec_field.name] = value

    return DecodedRecord(
        spec=spec,
        values=values,
        truncated_at=truncated_at,
        non_ascii=cursor.non_ascii,
        trailing_bytes=cursor.remaining if truncated_at is None else 0,
    )


__all__ = [
    "BIG_ENDIAN",
    "BITMAP_LSB_FIRST",
    "LITTLE_ENDIAN",
    "BitMap",
    "Cursor",
    "DecodedRecord",
    "FieldValue",
    "decode_record",
    "gate_open",
    "packed",
]
