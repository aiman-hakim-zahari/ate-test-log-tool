"""``StdfParser``: a path in, a ``TestRun`` or a typed failure out.

``parse`` matches ``LogParser.parse`` exactly, including progress reporting,
cancellation and the ``job_id`` generation carried through every message, so
``services/background.py`` needs no change to run either reader.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Final

from ate_fa_suite.model.entities import (
    ParseComplete,
    ParseFailed,
    ParseProgress,
)
from ate_fa_suite.parsing.stdf.codec import DecodedRecord, decode_record
from ate_fa_suite.parsing.stdf.mapping import StdfContext, build_run
from ate_fa_suite.parsing.stdf.reader import (
    HEADER_SIZE,
    RawRecord,
    cpu_type_endianness,
    detect_endianness,
    iter_records,
)
from ate_fa_suite.parsing.stdf.records import (
    FAR,
    FTR,
    MIR,
    PIR,
    PMR,
    PRR,
    PSR,
    SPECS,
    STR,
)
from ate_fa_suite.parsing.stdf.scan import ScanCollector, ScanDataSet, warning

ProgressCallback = Callable[[ParseProgress], None]

#: Report progress at most this often, matching the ADF-1 reader's cadence.
PROGRESS_STRIDE_BYTES: Final = 1_048_576

#: How many leading bytes a "this is not an STDF file" message shows.
PREVIEW_BYTES: Final = 8


@dataclass(frozen=True, slots=True)
class _PartKey:
    """One tested device: a PIR/PRR pair, identified as the spec identifies it.

    Deliberately **not** mapped onto ``BlockId.occurrence``.  Merging distinct
    devices into one waveform namespace is exactly what ``BlockId`` exists to
    prevent, and a real part axis in the IR is future work.
    """

    head: int
    site: int
    occurrence: int  # 1-based, per (head, site)

    def label(self) -> str:
        return (
            f"head {self.head}, site {self.site}, part {self.occurrence}"
        )


class StdfParser:
    """Read one STDF V4-2007 file into the shared ``TestRun``.

    One part per parse.  With no selection given, the first part carrying scan
    data wins, and the choice is always reported as a warning.
    """

    def __init__(
        self,
        *,
        head: int | None = None,
        site: int | None = None,
        part: int | None = None,
    ) -> None:
        self._head = head
        self._site = site
        self._part = part

    def parse(
        self,
        path: Path,
        job_id: int,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ParseComplete | ParseFailed:
        """Parse an STDF file, salvaging a truncated tail rather than raising."""
        started = time.perf_counter()
        try:
            total_bytes = path.stat().st_size
            source = path.open("rb")
        except OSError as error:
            return ParseFailed(
                job_id=job_id,
                line=0,
                column=0,
                message=f"cannot read {path}: {error.strerror or error}",
                context="",
            )

        try:
            with source:
                header = source.read(HEADER_SIZE)
                endian = detect_endianness(header)
                if endian is None:
                    return ParseFailed(
                        job_id=job_id,
                        line=1,
                        column=0,
                        message=(
                            f"{path.name} is not an STDF file: the first "
                            "record is not FAR"
                        ),
                        context=_preview(header),
                    )
                source.seek(0)
                return self._read(
                    source,
                    path,
                    endian,
                    job_id,
                    total_bytes,
                    started,
                    on_progress,
                    is_cancelled,
                )
        except OSError as error:
            return ParseFailed(
                job_id=job_id,
                line=0,
                column=0,
                message=f"cannot read {path}: {error.strerror or error}",
                context="",
            )

    def _read(
        self,
        source: BinaryIO,
        path: Path,
        endian: str,
        job_id: int,
        total_bytes: int,
        started: float,
        on_progress: ProgressCallback | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> ParseComplete | ParseFailed:
        context = StdfContext()
        collector = ScanCollector()
        by_part: dict[_PartKey, list[ScanDataSet]] = {}
        part_counts: dict[tuple[int, int], int] = {}
        open_parts: dict[tuple[int, int], _PartKey] = {}
        current: _PartKey | None = None

        cycle_total = 0
        fail_total = 0
        ftr_total = 0
        progress_byte = 0
        record: RawRecord | None = None

        def place(sets: list[ScanDataSet]) -> None:
            """File completed data sets under the part they were logged in."""
            for data_set in sets:
                site = (data_set.key.head_num, data_set.key.site_num)
                key = open_parts.get(site)
                if key is None:
                    key = current
                if key is None:
                    # No PIR seen; the file is either headless or single-part.
                    key = _PartKey(*site, 1)
                by_part.setdefault(key, []).append(data_set)

        for record in iter_records(source, endian):
            if is_cancelled is not None and is_cancelled():
                return ParseFailed(
                    job_id=job_id,
                    line=record.ordinal,
                    column=0,
                    message="parse cancelled",
                    context="",
                )

            spec = SPECS.get(record.key)
            if spec is not None:
                decoded = decode_record(spec, record.payload, endian)

                if spec is FAR:
                    _check_cpu_type(context, decoded, endian, record.ordinal)
                elif spec is FTR:
                    ftr_total += 1
                elif spec is MIR:
                    if context.mir is None:
                        context.mir = decoded
                elif spec is PMR:
                    _add_pin(context, decoded)
                elif spec is PSR:
                    _add_block(context, decoded)
                elif spec is PIR:
                    site = _site_of(decoded)
                    part_counts[site] = part_counts.get(site, 0) + 1
                    current = _PartKey(*site, part_counts[site])
                    open_parts[site] = current
                elif spec is PRR:
                    head, site_num = _site_of(decoded)
                    place(collector.close_site(head, site_num))
                    open_parts.pop((head, site_num), None)
                    current = None
                elif spec is STR:
                    place(collector.offer(decoded, record.ordinal))
                    cycle_total = max(
                        cycle_total, decoded.get_int("CYC_CNT") or 0
                    )
                    fail_total += decoded.get_int("LOCL_CNT") or 0

            if record.truncated:
                context.warnings.append(
                    warning(
                        record.ordinal,
                        "file ends mid-record; kept every complete record "
                        "before it",
                    )
                )

            if on_progress is not None and (
                record.end_byte - progress_byte >= PROGRESS_STRIDE_BYTES
                or record.end_byte >= total_bytes
            ):
                on_progress(
                    ParseProgress(
                        job_id=job_id,
                        bytes_done=min(record.end_byte, total_bytes),
                        bytes_total=total_bytes,
                        cycles=cycle_total,
                        fails=fail_total,
                    )
                )
                progress_byte = record.end_byte

        place(collector.close())

        if ftr_total:
            context.warnings.append(
                f"{ftr_total} FTR records were read but not rendered: FTR "
                "carries captured states only, so it is a Tier C source and "
                "this reader draws the Tier B STR differential"
            )

        context.data_sets.extend(self._select_part(by_part, context))
        run = build_run(context)
        return ParseComplete(
            job_id=job_id,
            run=run,
            elapsed_s=time.perf_counter() - started,
        )

    def _select_part(
        self,
        by_part: dict[_PartKey, list[ScanDataSet]],
        context: StdfContext,
    ) -> list[ScanDataSet]:
        """Choose exactly one part, and always say which one and why."""
        if not by_part:
            return []

        ordered = sorted(
            by_part, key=lambda k: (k.occurrence, k.head, k.site)
        )
        wanted = [
            key
            for key in ordered
            if (self._head is None or key.head == self._head)
            and (self._site is None or key.site == self._site)
            and (self._part is None or key.occurrence == self._part)
        ]
        if not wanted:
            asked = (self._head, self._site, self._part)
            context.warnings.append(
                f"no part matches the requested selection {asked}; using "
                f"{ordered[0].label()} instead"
            )
            wanted = ordered[:1]

        chosen = wanted[0]
        if len(ordered) > 1:
            context.warnings.append(
                f"file contains {len(ordered)} tested parts; this run shows "
                f"{chosen.label()} only, because the model has one part axis"
            )
        else:
            context.warnings.append(f"showing {chosen.label()}")
        return by_part[chosen]


def _preview(header: bytes) -> str:
    """A short hex preview, so a non-STDF file is diagnosable but not dumped."""
    if not header:
        return "file is empty"
    shown = header[:PREVIEW_BYTES]
    return "first bytes: " + " ".join(f"{byte:02x}" for byte in shown)


def _site_of(record: DecodedRecord) -> tuple[int, int]:
    return (record.get_int("HEAD_NUM") or 0, record.get_int("SITE_NUM") or 0)


def _add_pin(context: StdfContext, record: DecodedRecord) -> None:
    from ate_fa_suite.parsing.stdf.mapping import pmr_pin_name

    index = record.get_int("PMR_INDX")
    if index is None:
        return
    context.pins.setdefault(index, pmr_pin_name(record, index))


def _add_block(context: StdfContext, record: DecodedRecord) -> None:
    from ate_fa_suite.parsing.stdf.mapping import psr_block_name

    index = record.get_int("PSR_INDX")
    if index is None:
        return
    name = psr_block_name(record, index)
    # A PSR continuation repeats PSR_INDX; the first record carries the name.
    if index not in context.psr_names or not context.psr_names[index]:
        context.psr_names[index] = name


def _check_cpu_type(
    context: StdfContext,
    record: DecodedRecord,
    endian: str,
    ordinal: int,
) -> None:
    """Cross-check FAR's declared CPU type against the framing-derived order."""
    cpu_type = record.get_int("CPU_TYPE")
    if cpu_type is None:
        return
    declared = cpu_type_endianness(cpu_type)
    if declared is None:
        context.warnings.append(
            warning(
                ordinal,
                f"FAR CPU_TYPE {cpu_type} is not a byte order this reader "
                "recognizes; using the order implied by the FAR header",
            )
        )
    elif declared != endian:
        context.warnings.append(
            warning(
                ordinal,
                f"FAR CPU_TYPE {cpu_type} disagrees with the byte order the "
                "FAR header implies; using the header",
            )
        )


__all__ = ["PROGRESS_STRIDE_BYTES", "ProgressCallback", "StdfParser"]
