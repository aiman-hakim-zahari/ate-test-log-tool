"""Record framing and endianness detection for STDF streams.

Framing mirrors the ADF-1 chunked reader's policy from ``LOG_FORMAT_SPEC`` §7:
a short read is **truncation**, not corruption.  Iteration stops and the
caller salvages what came before, so a cut file loads partially with a warning
rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Final, Iterator

from ate_fa_suite.parsing.stdf.codec import BIG_ENDIAN, LITTLE_ENDIAN, packed
from ate_fa_suite.parsing.stdf.records import (
    CPU_TYPE_BIG_ENDIAN,
    CPU_TYPE_LITTLE_ENDIAN,
    FAR,
)

#: Every record starts with REC_LEN (U*2), REC_TYP (U*1), REC_SUB (U*1).
HEADER_SIZE: Final = 4

#: FAR is always ``REC_LEN=2, REC_TYP=0, REC_SUB=10``, so its first four bytes
#: differ between the two byte orders and settle the question outright.
FAR_HEADER_LITTLE: Final = b"\x02\x00\x00\x0a"
FAR_HEADER_BIG: Final = b"\x00\x02\x00\x0a"

FAR_KEY: Final = FAR.key


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One framed record, still undecoded.

    ``ordinal`` is 1-based and stands in for a source line number: in a binary
    file "record #N" is the only addressable position a reader can cite.
    """

    ordinal: int
    rec_typ: int
    rec_sub: int
    payload: bytes
    start_byte: int
    #: True when the payload is shorter than ``REC_LEN`` promised.
    truncated: bool = False

    @property
    def key(self) -> tuple[int, int]:
        return (self.rec_typ, self.rec_sub)

    @property
    def end_byte(self) -> int:
        return self.start_byte + HEADER_SIZE + len(self.payload)


def detect_endianness(header: bytes) -> str | None:
    """Return the ``struct`` prefix implied by a FAR header, or ``None``."""
    if header[:HEADER_SIZE] == FAR_HEADER_LITTLE:
        return LITTLE_ENDIAN
    if header[:HEADER_SIZE] == FAR_HEADER_BIG:
        return BIG_ENDIAN
    return None


def cpu_type_endianness(cpu_type: int) -> str | None:
    """Map FAR's ``CPU_TYPE`` to a byte order, or ``None`` if unrecognized."""
    if cpu_type == CPU_TYPE_BIG_ENDIAN:
        return BIG_ENDIAN
    if cpu_type == CPU_TYPE_LITTLE_ENDIAN:
        return LITTLE_ENDIAN
    return None  # 0 is DEC; this reader does not decode DEC word order


def iter_records(source: BinaryIO, endian: str) -> Iterator[RawRecord]:
    """Yield every framed record from ``source``, starting at its position.

    A truncated final record is yielded with ``truncated=True`` so its intact
    leading fields are still available, then iteration stops.
    """
    header_layout = packed(endian + "HBB")
    ordinal = 0
    offset = source.tell()

    while True:
        header = source.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            return  # clean end of file, or a header cut in half
        rec_len, rec_typ, rec_sub = header_layout.unpack(header)
        payload = source.read(rec_len)
        ordinal += 1
        record = RawRecord(
            ordinal=ordinal,
            rec_typ=int(rec_typ),
            rec_sub=int(rec_sub),
            payload=payload,
            start_byte=offset,
            truncated=len(payload) < rec_len,
        )
        yield record
        if record.truncated:
            return
        offset = record.end_byte


__all__ = [
    "FAR_HEADER_BIG",
    "FAR_HEADER_LITTLE",
    "FAR_KEY",
    "HEADER_SIZE",
    "RawRecord",
    "cpu_type_endianness",
    "detect_endianness",
    "iter_records",
]
