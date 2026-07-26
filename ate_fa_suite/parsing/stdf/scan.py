"""``STR`` decoding: the waveform alphabet, packed data, and continuations.

This is where the corrected premise becomes concrete.  ``EXP_DATA`` and
``CAP_DATA`` hold the state the pattern demanded and the state the tester
captured, per failing pin and cycle, which is exactly the differential the
canvas draws.

Three decisions here are deliberate and must not be "fixed" into silent
corrections:

* **``Z_VAL`` is not reversed.**  The tester already substituted ``Z`` per
  Table 5 before writing the log.  Undoing it would fabricate a measurement
  nobody made.  The flag is recorded as a warning; the data is left alone.
* **The alphabet does not grow.**  ``LogicState`` has exactly six members and
  the Phase 2 truth table is sized against that.  A waveform character outside
  ``0 1 X Z L H`` maps to ``UNKNOWN`` plus a warning naming it.
* **Cycle numbers sort and warn**, rather than being fatal.  ADF-1 makes
  non-monotonic vectors fatal because its framing index depends on file order;
  ``STR`` carries its own cycle numbers, so there is no such dependency.  See
  ``LOG_FORMAT_SPEC`` §8.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from ate_fa_suite.model.entities import LogicState
from ate_fa_suite.parsing.stdf.codec import BitMap, DecodedRecord
from ate_fa_suite.parsing.stdf.records import (
    DATA_BIT_EXPECTED_VALUES,
    DATA_BIT_IS_ASCII,
    DATA_BIT_VALUES,
    Z_VAL_MEANINGS,
)

#: Waveform character to logic state.  The keys are the six characters
#: ``LOG_FORMAT_SPEC`` §8.1 admits; anything else warns and reads as UNKNOWN.
WAVEFORM_STATES: Final[dict[str, LogicState]] = {
    "0": LogicState.LOW,
    "1": LogicState.HIGH,
    "X": LogicState.UNKNOWN,
    "Z": LogicState.HIGH_Z,
    "L": LogicState.WEAK_LOW,
    "H": LogicState.WEAK_HIGH,
}

#: Sub-byte packing order for CAP_DATA / EXP_DATA / NEW_DATA.
#:
#: The spec does not state this for these arrays, but it states it for every
#: other sub-byte layout it defines, and always the same way: ``N*1`` puts the
#: "first item in low 4 bits", ``B*n`` and ``D*n`` put the "first data item in
#: least significant bit".  Low-order-first is therefore the reading the format
#: family supports.  It lives here as one constant so a disagreement with a
#: real vendor file is a one-line flip rather than a decoder rewrite.
LSB_FIRST: Final = True

#: Above this, an integer cycle number stops being exactly representable as a
#: float, which the §6.2 viewport uses.  Better named here than met as jitter.
MAX_EXACT_CYCLE: Final = 2**53


def warning(ordinal: int, message: str) -> str:
    """Format a warning against a record ordinal.

    The binary analogue of the ADF-1 validator's ``line N:`` prefix: in a
    binary file the record ordinal is the only citable position.
    """
    return f"record #{ordinal}: {message}"


def expected_data_bytes(local_count: int, data_bit: int) -> int:
    """``DATA_CNT`` per the spec: ``ceiling((LOCL_CNT * DATA_BIT) / 8)``."""
    return -(-(local_count * data_bit) // 8)


def unpack_characters(
    data: bytes, count: int, data_bit: int, alphabet: str
) -> tuple[str, ...]:
    """Unpack ``count`` waveform characters from a packed data array.

    At ``DATA_BIT`` 8 the byte *is* the ASCII waveform character and the
    ``DATA_CHR`` alphabet is not consulted.  At 1, 2 and 4 bits the packed
    value indexes into ``DATA_CHR``.
    """
    if data_bit == DATA_BIT_IS_ASCII:
        return tuple(chr(byte) for byte in data[:count])

    per_byte = 8 // data_bit
    mask = (1 << data_bit) - 1
    characters: list[str] = []
    for index in range(count):
        byte_index, slot = divmod(index, per_byte)
        if byte_index >= len(data):
            break
        shift = slot * data_bit if LSB_FIRST else (per_byte - 1 - slot) * data_bit
        value = (data[byte_index] >> shift) & mask
        characters.append(alphabet[value] if value < len(alphabet) else "?")
    return tuple(characters)


@dataclass(frozen=True, slots=True)
class StrKey:
    """The identity a continuation set shares.

    Continuation records legally zero these fields ("Ignored, inherited from
    STR 2A" in the spec's own example), so this key identifies a *set*, taken
    from its first record only.
    """

    head_num: int
    site_num: int
    test_num: int
    psr_ref: int


@dataclass(frozen=True, slots=True)
class ScanFail:
    """One logged pin-and-cycle failure.

    ``expected`` is ``None`` when ``EXP_DATA`` was not logged, which is a
    different thing from a masked compare and is why the mapping bypasses
    ``FailCategory.classify`` in that case.
    """

    cycle: int  # absolute: CYC_BASE + CYCL_NUM[i]
    pmr_indx: int | None
    chain: int | None
    expected: LogicState | None
    captured: LogicState | None


@dataclass(frozen=True, slots=True)
class ScanDataSet:
    """One complete ``STR`` data set, joined across its continuations."""

    key: StrKey
    ordinal: int  # ordinal of the set's first record
    test_txt: str
    fails: tuple[ScanFail, ...]
    cyc_cnt: int
    totf_cnt: int
    totl_cnt: int
    z_val: int
    mask_map: BitMap | None = None
    fal_map: BitMap | None = None
    conditions: tuple[tuple[str, str], ...] = ()  # the Phase 6 shmoo source
    warnings: tuple[str, ...] = ()


class _Accumulator:
    """Collects one ``STR`` data set across its continuation records."""

    def __init__(self, record: DecodedRecord, ordinal: int) -> None:
        self.ordinal = ordinal
        self.key = StrKey(
            head_num=record.get_int("HEAD_NUM") or 0,
            site_num=record.get_int("SITE_NUM") or 0,
            test_num=record.get_int("TEST_NUM") or 0,
            psr_ref=record.get_int("PSR_REF") or 0,
        )
        self.rec_tot = max(record.get_int("REC_TOT") or 1, 1)
        self.test_txt = record.get_str("TEST_TXT") or ""
        self.cyc_cnt = record.get_int("CYC_CNT") or 0
        self.totf_cnt = record.get_int("TOTF_CNT") or 0
        self.totl_cnt = record.get_int("TOTL_CNT") or 0
        self.z_val = record.get_int("Z_VAL") or 0
        self.mask_map = record.get_map("MASK_MAP")
        self.fal_map = record.get_map("FAL_MAP")
        self.conditions = _conditions(record)

        self.seen: dict[int, int] = {}  # REC_INDX -> ordinal
        self.local_total = 0
        self.fails: list[ScanFail] = []
        self.warnings: list[str] = []
        self.add(record, ordinal)

    @property
    def complete(self) -> bool:
        return len(self.seen) >= self.rec_tot

    def add(self, record: DecodedRecord, ordinal: int) -> None:
        """Fold one record of the set in, in whatever order it arrives."""
        index = record.get_int("REC_INDX")
        if index is None:
            index = len(self.seen) + 1
        if index in self.seen:
            self.warnings.append(
                warning(
                    ordinal,
                    f"duplicate REC_INDX {index} in the STR set opened at "
                    f"record #{self.ordinal}; keeping the first",
                )
            )
            return
        self.seen[index] = ordinal

        # Continuations may legally zero the scalar fields, so only a differing
        # non-zero value is a disagreement worth reporting.
        self._cross_check(record, ordinal)
        if record.truncated_at is not None:
            self.warnings.append(
                warning(
                    ordinal,
                    "STR record ends early, at field "
                    f"{record.truncated_at}; kept the fields before it",
                )
            )
        if not self.conditions:
            self.conditions = _conditions(record)
        if self.mask_map is None:
            self.mask_map = record.get_map("MASK_MAP")
        if self.fal_map is None:
            self.fal_map = record.get_map("FAL_MAP")

        self.fails.extend(self._decode_fails(record, ordinal))

    def _cross_check(self, record: DecodedRecord, ordinal: int) -> None:
        for name, first in (
            ("TEST_NUM", self.key.test_num),
            ("PSR_REF", self.key.psr_ref),
            ("TOTF_CNT", self.totf_cnt),
            ("TOTL_CNT", self.totl_cnt),
        ):
            later = record.get_int(name)
            if later and later != first:
                self.warnings.append(
                    warning(
                        ordinal,
                        f"{name} is {later} here but {first} in the first "
                        "record of the STR set; keeping the first",
                    )
                )

    def _decode_fails(
        self, record: DecodedRecord, ordinal: int
    ) -> list[ScanFail]:
        local_count = record.get_int("LOCL_CNT") or 0
        self.local_total += local_count
        cycles = record.get_ints("CYCL_NUM")
        if cycles is None:
            self.warnings.append(
                warning(
                    ordinal,
                    "STR has no CYCL_NUM array (DATA_FLG bit 0 set), so its "
                    "failures have no addressable cycle; skipped",
                )
            )
            return []

        cyc_base = record.get_int("CYC_BASE") or 0
        pins = record.get_ints("PMR_INDX")
        chains = record.get_ints("CHN_NUM")
        if pins is None and chains is None:
            self.warnings.append(
                warning(
                    ordinal,
                    "STR has neither PMR_INDX nor CHN_NUM, so its failures "
                    "cannot be attributed to a pin; skipped",
                )
            )
            return []

        self._check_data_cnt(record, ordinal, local_count)
        expected = self._states(record, ordinal, "EXP_DATA", local_count)
        captured = self._states(record, ordinal, "CAP_DATA", local_count)
        if expected is None:
            self.warnings.append(
                warning(
                    ordinal,
                    "STR has no EXP_DATA array (DATA_FLG bit 4 set); "
                    "failures are reported without an expected state",
                )
            )
        if self.z_val:
            self.warnings.append(
                warning(
                    ordinal,
                    f"Z_VAL is {self.z_val} "
                    f"({Z_VAL_MEANINGS.get(self.z_val, 'unrecognized')}); "
                    "the captured alphabet was substituted by the tester and "
                    "is reported as logged",
                )
            )

        available = len(cycles)
        if local_count and available != local_count:
            self.warnings.append(
                warning(
                    ordinal,
                    f"LOCL_CNT is {local_count} but the CYCL_NUM array holds "
                    f"{available} entries; using the array",
                )
            )

        fails: list[ScanFail] = []
        for i in range(available):
            cycle = cyc_base + cycles[i]
            if cycle > MAX_EXACT_CYCLE:
                self.warnings.append(
                    warning(
                        ordinal,
                        f"cycle number {cycle} exceeds 2**53 and cannot be "
                        "represented exactly on the float-valued time axis",
                    )
                )
            fails.append(
                ScanFail(
                    cycle=cycle,
                    pmr_indx=pins[i] if pins is not None and i < len(pins) else None,
                    chain=(
                        chains[i]
                        if chains is not None and i < len(chains)
                        else None
                    ),
                    expected=(
                        expected[i]
                        if expected is not None and i < len(expected)
                        else None
                    ),
                    captured=(
                        captured[i]
                        if captured is not None and i < len(captured)
                        else None
                    ),
                )
            )
        return fails

    def _check_data_cnt(
        self, record: DecodedRecord, ordinal: int, local_count: int
    ) -> None:
        """Cross-check ``DATA_CNT`` against ``ceil(LOCL_CNT * DATA_BIT / 8)``.

        This is the earliest and best signal that the field order is wrong, so
        it runs on the scalars alone, once per record.  Doing it inside the
        per-array decode would miss the very case a bad field order produces: a
        garbage ``DATA_CNT`` large enough that the array read fails outright.
        """
        declared = record.get_int("DATA_CNT")
        data_bit = record.get_int("DATA_BIT") or 0
        if declared is None or data_bit not in DATA_BIT_VALUES:
            return
        wanted = expected_data_bytes(local_count, data_bit)
        if declared != wanted:
            self.warnings.append(
                warning(
                    ordinal,
                    f"DATA_CNT is {declared} but LOCL_CNT {local_count} at "
                    f"{data_bit} bits per fail needs {wanted}",
                )
            )

    def _states(
        self,
        record: DecodedRecord,
        ordinal: int,
        name: str,
        local_count: int,
    ) -> tuple[LogicState, ...] | None:
        """Decode one packed data array into logic states."""
        raw = record.get_ints(name)
        if raw is None:
            return None

        data = bytes(raw)
        data_bit = record.get_int("DATA_BIT") or 0
        if data_bit not in DATA_BIT_VALUES:
            self.warnings.append(
                warning(
                    ordinal,
                    f"DATA_BIT is {data_bit}, not one of "
                    f"{sorted(DATA_BIT_VALUES)}; {name} cannot be unpacked",
                )
            )
            return None
        if name != "CAP_DATA" and data_bit not in DATA_BIT_EXPECTED_VALUES:
            self.warnings.append(
                warning(
                    ordinal,
                    f"DATA_BIT is {data_bit}, but {name} allows only "
                    f"{sorted(DATA_BIT_EXPECTED_VALUES)}; unpacked anyway",
                )
            )

        wanted = expected_data_bytes(local_count, data_bit)
        if len(data) != wanted:
            self.warnings.append(
                warning(
                    ordinal,
                    f"{name} holds {len(data)} bytes but LOCL_CNT "
                    f"{local_count} at {data_bit} bits per fail needs "
                    f"{wanted}",
                )
            )

        alphabet = record.get_str("DATA_CHR") or ""
        if data_bit != DATA_BIT_IS_ASCII and not alphabet:
            self.warnings.append(
                warning(
                    ordinal,
                    f"DATA_CHR is absent but DATA_BIT is {data_bit}, so "
                    f"{name} values have no waveform characters",
                )
            )

        characters = unpack_characters(data, local_count, data_bit, alphabet)
        unknown: dict[str, int] = {}
        states: list[LogicState] = []
        for character in characters:
            state = WAVEFORM_STATES.get(character.upper())
            if state is None:
                unknown[character] = unknown.get(character, 0) + 1
                state = LogicState.UNKNOWN
            states.append(state)
        for character, count in sorted(unknown.items()):
            self.warnings.append(
                warning(
                    ordinal,
                    f"{name} waveform character {character!r} is outside "
                    f"'0 1 X Z L H' ({count} occurrences); read as UNKNOWN",
                )
            )
        return tuple(states)

    def result(self) -> ScanDataSet:
        """Finish the set: check the totals, sort by cycle, and warn."""
        warnings = list(self.warnings)

        missing = [i for i in range(1, self.rec_tot + 1) if i not in self.seen]
        if missing and self.rec_tot > 1:
            warnings.append(
                warning(
                    self.ordinal,
                    f"STR set declares REC_TOT {self.rec_tot} but records "
                    f"{missing} never arrived; emitting what was read",
                )
            )
        if self.totl_cnt and self.totl_cnt != self.local_total:
            warnings.append(
                warning(
                    self.ordinal,
                    f"TOTL_CNT {self.totl_cnt} does not match the sum of "
                    f"LOCL_CNT {self.local_total}",
                )
            )
        if self.totf_cnt and self.totl_cnt and self.totl_cnt < self.totf_cnt:
            warnings.append(
                warning(
                    self.ordinal,
                    f"fail memory truncated: {self.totf_cnt} failures "
                    f"detected but only {self.totl_cnt} logged",
                )
            )

        cycles = [failure.cycle for failure in self.fails]
        if any(a > b for a, b in zip(cycles, cycles[1:])):
            warnings.append(
                warning(
                    self.ordinal,
                    "cycle numbers are not ascending in file order; sorted "
                    "by cycle (see LOG_FORMAT_SPEC §8.1)",
                )
            )
        ordered = sorted(
            self.fails, key=lambda f: (f.cycle, f.pmr_indx or 0, f.chain or 0)
        )

        return ScanDataSet(
            key=self.key,
            ordinal=self.ordinal,
            test_txt=self.test_txt,
            fails=tuple(ordered),
            cyc_cnt=self.cyc_cnt,
            totf_cnt=self.totf_cnt,
            totl_cnt=self.totl_cnt,
            z_val=self.z_val,
            mask_map=self.mask_map,
            fal_map=self.fal_map,
            conditions=self.conditions,
            warnings=tuple(warnings),
        )


def _conditions(record: DecodedRecord) -> tuple[tuple[str, str], ...]:
    names = record.get_strs("COND_NAM") or ()
    values = record.get_strs("COND_VAL") or ()
    return tuple(zip(names, values))


def _starts_a_set(record: DecodedRecord) -> bool:
    """Whether this record opens a data set rather than continuing one."""
    total = record.get_int("REC_TOT")
    if total is None or total <= 1:
        return True
    index = record.get_int("REC_INDX")
    return index is None or index <= 1


@dataclass
class ScanCollector:
    """Joins ``STR`` records into data sets as they stream past.

    At most one set is held open per ``(HEAD_NUM, SITE_NUM)``, so memory stays
    bounded on a multi-site file.  ``_recent`` exists because a continuation
    record may legally zero its identity fields, in which case the only
    attachment the spec permits is "the set most recently opened".
    """

    _open: dict[tuple[int, int], _Accumulator] = field(default_factory=dict)
    _recent: _Accumulator | None = None

    def offer(self, record: DecodedRecord, ordinal: int) -> list[ScanDataSet]:
        """Fold one decoded ``STR`` in; return any sets it completed."""
        done: list[ScanDataSet] = []

        if _starts_a_set(record):
            accumulator = _Accumulator(record, ordinal)
            site = (accumulator.key.head_num, accumulator.key.site_num)
            previous = self._open.pop(site, None)
            if previous is not None:
                done.append(previous.result())
            if accumulator.complete:
                done.append(accumulator.result())
                if self._recent is previous:
                    self._recent = None
            else:
                self._open[site] = accumulator
                self._recent = accumulator
            return done

        target = self._continuation_target(record)
        if target is None:
            # An orphan continuation still carries real failures, so keep it.
            accumulator = _Accumulator(record, ordinal)
            accumulator.warnings.append(
                warning(
                    ordinal,
                    "STR continuation record has no open set to join; "
                    "treated as a set of its own",
                )
            )
            done.append(accumulator.result())
            return done

        target.add(record, ordinal)
        if target.complete:
            site = (target.key.head_num, target.key.site_num)
            self._open.pop(site, None)
            if self._recent is target:
                self._recent = None
            done.append(target.result())
        return done

    def _continuation_target(self, record: DecodedRecord) -> _Accumulator | None:
        head = record.get_int("HEAD_NUM") or 0
        site = record.get_int("SITE_NUM") or 0
        if (head, site) in self._open:
            return self._open[(head, site)]
        return self._recent  # identity fields were zeroed, per the spec

    def close_site(self, head: int, site: int) -> list[ScanDataSet]:
        """Flush the set open for one ``(head, site)``, at its ``PRR``.

        Per-site rather than wholesale, so a ``PRR`` for one site cannot cut
        short a set still being filled for another on an interleaved file.
        """
        accumulator = self._open.pop((head, site), None)
        if accumulator is None:
            return []
        if self._recent is accumulator:
            self._recent = None
        return [accumulator.result()]

    def close(self) -> list[ScanDataSet]:
        """Flush every still-open set, at end of file."""
        done = [
            accumulator.result()
            for _, accumulator in sorted(
                self._open.items(), key=lambda item: item[1].ordinal
            )
        ]
        self._open.clear()
        self._recent = None
        return done


__all__ = [
    "LSB_FIRST",
    "MAX_EXACT_CYCLE",
    "WAVEFORM_STATES",
    "ScanCollector",
    "ScanDataSet",
    "ScanFail",
    "StrKey",
    "expected_data_bytes",
    "unpack_characters",
    "warning",
]
