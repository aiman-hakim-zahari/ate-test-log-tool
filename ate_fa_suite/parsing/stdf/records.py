"""Declarative STDF V4-2007 record schemas: data, not code.

This is the binary analogue of ``atelog.lark``.  Every field order here is
pinned against the SEMI STDF V4-2007 specification text, so correcting a
record is an edit to :data:`SPECS` rather than a change to the decoder.

Three points in the specification are self-contradictory.  Each is resolved
below in favour of the reading the spec's own worked examples support, and the
reasoning is recorded so nobody has to re-derive it:

1. ``DATA_FLG`` is an **omission** mask.  Page 50: "If a bit in DATA_FLG is
   set to a 1 then the corresponding field is absent".  The examples agree
   exactly: ``DATA_FLG = 252`` (``0b11111100``, bits 0 and 1 clear) is
   annotated "Use the CYCL_OFST & PMR_INDX arrays", and ``DATA_FLG = 236``
   (``0b11101100``, bits 0, 1 and 4 clear) is annotated "Use CYCL_OFST,
   PMR_INDX, and EXP_DATA arrays".  An array is present when its bit is
   **clear**.
2. ``FMU_FLG`` bits 0 and 1 gate ``FAL_MAP``; bits 2 and 3 gate ``MASK_MAP``.
   The field table's missing-data column has the two pairs transposed, but the
   narrative ("Bits 0 and 1 indicate the status of the FAL_MAP and the Bit 2
   and 3 described the status of Mask map"), Table 6, Table 7 and the STR #1
   example all agree on this pairing.  The field *order* is unaffected:
   ``MASK_MAP`` still precedes ``FAL_MAP``.
3. The cycle-number array is called ``CYCL_NUM`` in the normative Data Fields
   table, ``CYC_NUM`` in Table 8, and ``CYCL_OFST`` in the examples.  The Data
   Fields table wins; see :data:`STR_CYCLE_ARRAY_ALIASES`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Scalar(Enum):
    """STDF data type codes, spelled as the specification spells them."""

    U1 = "U*1"
    U2 = "U*2"
    U4 = "U*4"
    U8 = "U*8"
    I2 = "I*2"
    I4 = "I*4"
    C1 = "C*1"  # one fixed character
    CN = "C*n"  # 1-byte length prefix
    B1 = "B*1"  # one fixed byte of flags, no prefix
    BN = "B*n"  # 1-byte prefix counting BYTES
    DN = "D*n"  # 2-byte prefix counting BITS
    N1 = "N*1"  # two values per byte, low nibble first
    UF = "U*f"  # width named by another field (1, 2 or 4)
    CF = "C*f"  # length named by another field


class GateRule(Enum):
    """How a field's presence is decided from an earlier field's value."""

    BIT_CLEAR = "bit clear"  # present when the named bit is 0
    BIT_SET = "bit set"  # present when the named bit is 1
    NONZERO = "nonzero"  # present when the named field is not 0
    MAP_PRESENT = "map present"  # FMU_FLG Tables 6 and 7: (low, high) == (1, 0)


@dataclass(frozen=True, slots=True)
class Gate:
    """A presence condition expressed against an earlier field."""

    field: str
    rule: GateRule
    bit: int = 0


@dataclass(frozen=True, slots=True)
class Field:
    """One record field.

    ``count_from`` names the field holding the element count, which is what
    makes this an array; ``width_from`` names the field holding the per-element
    byte width for the ``U*f`` and ``C*f`` types.
    """

    name: str
    kind: Scalar
    count_from: str | None = None
    width_from: str | None = None
    gate: Gate | None = None

    @property
    def is_array(self) -> bool:
        return self.count_from is not None


@dataclass(frozen=True, slots=True)
class RecordSpec:
    """A record's identity and its fields, in wire order."""

    name: str
    rec_typ: int
    rec_sub: int
    fields: tuple[Field, ...]

    @property
    def key(self) -> tuple[int, int]:
        return (self.rec_typ, self.rec_sub)


# --- flag bit assignments ----------------------------------------------------

#: FAR ``CPU_TYPE``.  0 is DEC, and this reader does not decode DEC floats or
#: word order, so only the two modern values are recognized.
CPU_TYPE_BIG_ENDIAN: Final = 1
CPU_TYPE_LITTLE_ENDIAN: Final = 2

#: STR ``DATA_FLG``, Table 8.  A **set** bit means the array is absent.
STR_DATA_FLG_BITS: Final[dict[str, int]] = {
    "CYCL_NUM": 0,
    "PMR_INDX": 1,
    "CHN_NUM": 2,
    "CAP_DATA": 3,
    "EXP_DATA": 4,
    "NEW_DATA": 5,
    "PAT_NUM": 6,
    "BIT_POS": 7,
}

#: The spec names the cycle array three different ways.  Kept so a reader of
#: this file recognizes all three, and so the writer in Step 8 cannot drift.
STR_CYCLE_ARRAY_ALIASES: Final[tuple[str, ...]] = (
    "CYCL_NUM",
    "CYC_NUM",
    "CYCL_OFST",
)

#: STR ``FMU_FLG``: the low bit of each pair, per Tables 6 and 7.
FMU_FLG_FAL_MAP_BIT: Final = 0
FMU_FLG_MASK_MAP_BIT: Final = 2
FMU_FLG_PATTERN_CHANGED_BIT: Final = 4

#: STR ``Z_VAL``, Table 5.  The tester has already applied the substitution,
#: so the reader records the flag and leaves the captured data alone.
Z_VAL_MEANINGS: Final[dict[int, str]] = {
    0: "not handled",
    1: "Z mapped to L",
    2: "Z mapped to H",
    3: "Z mapped to Z",
    4: "Z mapped to X",
}

#: STR ``DATA_BIT``: bits per logged fail in CAP_DATA/EXP_DATA/NEW_DATA.
#: CAP_DATA may use any of these; EXP_DATA and NEW_DATA only 4 or 8.
DATA_BIT_VALUES: Final[frozenset[int]] = frozenset({1, 2, 4, 8})
DATA_BIT_EXPECTED_VALUES: Final[frozenset[int]] = frozenset({4, 8})

#: At DATA_BIT 8 the byte *is* the ASCII waveform character, so DATA_CHR is
#: not consulted.
DATA_BIT_IS_ASCII: Final = 8


def _gate_data_flg(name: str) -> Gate:
    """Present when the named array's DATA_FLG bit is clear."""
    return Gate("DATA_FLG", GateRule.BIT_CLEAR, STR_DATA_FLG_BITS[name])


# --- record schemas ----------------------------------------------------------

FAR: Final = RecordSpec(
    name="FAR",
    rec_typ=0,
    rec_sub=10,
    fields=(
        Field("CPU_TYPE", Scalar.U1),
        Field("STDF_VER", Scalar.U1),
    ),
)

MIR: Final = RecordSpec(
    name="MIR",
    rec_typ=1,
    rec_sub=10,
    fields=(
        Field("SETUP_T", Scalar.U4),
        Field("START_T", Scalar.U4),
        Field("STAT_NUM", Scalar.U1),
        Field("MODE_COD", Scalar.C1),
        Field("RTST_COD", Scalar.C1),
        Field("PROT_COD", Scalar.C1),
        Field("BURN_TIM", Scalar.U2),
        Field("CMOD_COD", Scalar.C1),
        Field("LOT_ID", Scalar.CN),
        Field("PART_TYP", Scalar.CN),
        Field("NODE_NAM", Scalar.CN),
        Field("TSTR_TYP", Scalar.CN),
        Field("JOB_NAM", Scalar.CN),
        Field("JOB_REV", Scalar.CN),
        Field("SBLOT_ID", Scalar.CN),
        Field("OPER_NAM", Scalar.CN),
        Field("EXEC_TYP", Scalar.CN),
        Field("EXEC_VER", Scalar.CN),
        Field("TEST_COD", Scalar.CN),
        Field("TST_TEMP", Scalar.CN),
        Field("USER_TXT", Scalar.CN),
        Field("AUX_FILE", Scalar.CN),
        Field("PKG_TYP", Scalar.CN),
        Field("FAMLY_ID", Scalar.CN),
        Field("DATE_COD", Scalar.CN),
        Field("FACIL_ID", Scalar.CN),
        Field("FLOOR_ID", Scalar.CN),
        Field("PROC_ID", Scalar.CN),
        Field("OPER_FRQ", Scalar.CN),
        Field("SPEC_NAM", Scalar.CN),
        Field("SPEC_VER", Scalar.CN),
        Field("FLOW_ID", Scalar.CN),
        Field("SETUP_ID", Scalar.CN),
        Field("DSGN_REV", Scalar.CN),
        Field("ENG_ID", Scalar.CN),
        Field("ROM_COD", Scalar.CN),
        Field("SERL_NUM", Scalar.CN),
        Field("SUPR_NAM", Scalar.CN),
    ),
)

PMR: Final = RecordSpec(
    name="PMR",
    rec_typ=1,
    rec_sub=60,
    fields=(
        Field("PMR_INDX", Scalar.U2),
        Field("CHAN_TYP", Scalar.U2),
        Field("CHAN_NAM", Scalar.CN),
        Field("PHY_NAM", Scalar.CN),
        Field("LOG_NAM", Scalar.CN),
        Field("HEAD_NUM", Scalar.U1),
        Field("SITE_NUM", Scalar.U1),
    ),
)

PSR: Final = RecordSpec(
    name="PSR",
    rec_typ=1,
    rec_sub=90,
    fields=(
        Field("REC_INDX", Scalar.U1),
        Field("REC_TOT", Scalar.U1),
        Field("PSR_INDX", Scalar.U2),
        Field("PSR_NAM", Scalar.CN),
        Field("OPT_FLG", Scalar.B1),
        Field("TOTP_CNT", Scalar.U2),
        Field("LOCP_CNT", Scalar.U2),
        Field("PAT_BGN", Scalar.U8, count_from="LOCP_CNT"),
        Field("PAT_END", Scalar.U8, count_from="LOCP_CNT"),
        Field("PAT_FILE", Scalar.CN, count_from="LOCP_CNT"),
        # OPT_FLG marks these absent when set, same polarity as DATA_FLG.
        Field(
            "PAT_LBL",
            Scalar.CN,
            count_from="LOCP_CNT",
            gate=Gate("OPT_FLG", GateRule.BIT_CLEAR, 0),
        ),
        Field(
            "FILE_UID",
            Scalar.CN,
            count_from="LOCP_CNT",
            gate=Gate("OPT_FLG", GateRule.BIT_CLEAR, 1),
        ),
        Field(
            "ATPG_DSC",
            Scalar.CN,
            count_from="LOCP_CNT",
            gate=Gate("OPT_FLG", GateRule.BIT_CLEAR, 2),
        ),
        Field(
            "SRC_ID",
            Scalar.CN,
            count_from="LOCP_CNT",
            gate=Gate("OPT_FLG", GateRule.BIT_CLEAR, 3),
        ),
    ),
)

PIR: Final = RecordSpec(
    name="PIR",
    rec_typ=5,
    rec_sub=10,
    fields=(
        Field("HEAD_NUM", Scalar.U1),
        Field("SITE_NUM", Scalar.U1),
    ),
)

PRR: Final = RecordSpec(
    name="PRR",
    rec_typ=5,
    rec_sub=20,
    fields=(
        Field("HEAD_NUM", Scalar.U1),
        Field("SITE_NUM", Scalar.U1),
        Field("PART_FLG", Scalar.B1),
        Field("NUM_TEST", Scalar.U2),
        Field("HARD_BIN", Scalar.U2),
        Field("SOFT_BIN", Scalar.U2),
        Field("X_COORD", Scalar.I2),
        Field("Y_COORD", Scalar.I2),
        Field("TEST_T", Scalar.U4),
        Field("PART_ID", Scalar.CN),
        Field("PART_TXT", Scalar.CN),
        Field("PART_FIX", Scalar.BN),
    ),
)

#: FTR's OPT_FLAG marks fields *invalid*, not absent: every field is
#: physically present.  That is the opposite of STR's DATA_FLG, so there are
#: no gates here.
FTR: Final = RecordSpec(
    name="FTR",
    rec_typ=15,
    rec_sub=20,
    fields=(
        Field("TEST_NUM", Scalar.U4),
        Field("HEAD_NUM", Scalar.U1),
        Field("SITE_NUM", Scalar.U1),
        Field("TEST_FLG", Scalar.B1),
        Field("OPT_FLAG", Scalar.B1),
        Field("CYCL_CNT", Scalar.U4),
        Field("REL_VADR", Scalar.U4),
        Field("REPT_CNT", Scalar.U4),
        Field("NUM_FAIL", Scalar.U4),
        Field("XFAIL_AD", Scalar.I4),
        Field("YFAIL_AD", Scalar.I4),
        Field("VECT_OFF", Scalar.I2),
        Field("RTN_ICNT", Scalar.U2),
        Field("PGM_ICNT", Scalar.U2),
        Field("RTN_INDX", Scalar.U2, count_from="RTN_ICNT"),
        Field("RTN_STAT", Scalar.N1, count_from="RTN_ICNT"),
        Field("PGM_INDX", Scalar.U2, count_from="PGM_ICNT"),
        Field("PGM_STAT", Scalar.N1, count_from="PGM_ICNT"),
        Field("FAIL_PIN", Scalar.DN),
        Field("VECT_NAM", Scalar.CN),
        Field("TIME_SET", Scalar.CN),
        Field("OP_CODE", Scalar.CN),
        Field("TEST_TXT", Scalar.CN),
        Field("ALARM_ID", Scalar.CN),
        Field("PROG_TXT", Scalar.CN),
        Field("RSLT_TXT", Scalar.CN),
        Field("PATG_NUM", Scalar.U1),
        Field("SPIN_MAP", Scalar.DN),
    ),
)

STR: Final = RecordSpec(
    name="STR",
    rec_typ=15,
    rec_sub=30,
    fields=(
        # -- continuation fields --
        Field("REC_INDX", Scalar.U1),
        Field("REC_TOT", Scalar.U1),
        # -- test setup, carried over from FTR --
        Field("TEST_NUM", Scalar.U4),
        Field("HEAD_NUM", Scalar.U1),
        Field("SITE_NUM", Scalar.U1),
        Field("PSR_REF", Scalar.U2),
        Field("TEST_FLG", Scalar.B1),
        Field("LOG_TYP", Scalar.CN),
        Field("TEST_TXT", Scalar.CN),
        Field("ALARM_ID", Scalar.CN),
        Field("PROG_TXT", Scalar.CN),
        Field("RSLT_TXT", Scalar.CN),
        # -- Z handling and the two pin bit maps --
        Field("Z_VAL", Scalar.U1),
        Field("FMU_FLG", Scalar.B1),
        Field(
            "MASK_MAP",
            Scalar.DN,
            gate=Gate("FMU_FLG", GateRule.MAP_PRESENT, FMU_FLG_MASK_MAP_BIT),
        ),
        Field(
            "FAL_MAP",
            Scalar.DN,
            gate=Gate("FMU_FLG", GateRule.MAP_PRESENT, FMU_FLG_FAL_MAP_BIT),
        ),
        # -- validation and synchronization --
        Field("CYC_CNT", Scalar.U8),
        Field("TOTF_CNT", Scalar.U4),
        Field("TOTL_CNT", Scalar.U4),
        Field("CYC_BASE", Scalar.U8),
        Field("BIT_BASE", Scalar.U2),
        # -- datalog format --
        Field("DATA_FLG", Scalar.B1),
        Field("COND_CNT", Scalar.U2),
        Field("LOCL_CNT", Scalar.U4),
        Field("LIM_CNT", Scalar.U2),
        Field("DATA_BIT", Scalar.U1),
        Field("DATA_CHR", Scalar.CN),
        Field("DATA_CNT", Scalar.U2),
        Field("USR1_LEN", Scalar.U1),
        Field("USR2_LEN", Scalar.U1),
        Field("USR3_LEN", Scalar.U1),
        Field("TXT_LEN", Scalar.U1),
        # -- fail-logging limits --
        Field("LIM_INDX", Scalar.U2, count_from="LIM_CNT"),
        Field("LIM_SPEC", Scalar.U4, count_from="LIM_CNT"),
        # -- test conditions, the Phase 6 shmoo source --
        Field("COND_NAM", Scalar.CN, count_from="COND_CNT"),
        Field("COND_VAL", Scalar.CN, count_from="COND_CNT"),
        # -- the datalog itself; LOCL_CNT is k, DATA_CNT is m --
        Field(
            "CYCL_NUM",
            Scalar.U4,
            count_from="LOCL_CNT",
            gate=_gate_data_flg("CYCL_NUM"),
        ),
        Field(
            "PMR_INDX",
            Scalar.U2,
            count_from="LOCL_CNT",
            gate=_gate_data_flg("PMR_INDX"),
        ),
        Field(
            "CHN_NUM",
            Scalar.U2,
            count_from="LOCL_CNT",
            gate=_gate_data_flg("CHN_NUM"),
        ),
        Field(
            "CAP_DATA",
            Scalar.U1,
            count_from="DATA_CNT",
            gate=_gate_data_flg("CAP_DATA"),
        ),
        Field(
            "EXP_DATA",
            Scalar.U1,
            count_from="DATA_CNT",
            gate=_gate_data_flg("EXP_DATA"),
        ),
        Field(
            "NEW_DATA",
            Scalar.U1,
            count_from="DATA_CNT",
            gate=_gate_data_flg("NEW_DATA"),
        ),
        Field(
            "PAT_NUM",
            Scalar.U4,
            count_from="LOCL_CNT",
            gate=_gate_data_flg("PAT_NUM"),
        ),
        Field(
            "BIT_POS",
            Scalar.U4,
            count_from="LOCL_CNT",
            gate=_gate_data_flg("BIT_POS"),
        ),
        # -- user-defined per-fail arrays; width comes from the *_LEN field --
        Field(
            "USR1",
            Scalar.UF,
            count_from="LOCL_CNT",
            width_from="USR1_LEN",
            gate=Gate("USR1_LEN", GateRule.NONZERO),
        ),
        Field(
            "USR2",
            Scalar.UF,
            count_from="LOCL_CNT",
            width_from="USR2_LEN",
            gate=Gate("USR2_LEN", GateRule.NONZERO),
        ),
        Field(
            "USR3",
            Scalar.UF,
            count_from="LOCL_CNT",
            width_from="USR3_LEN",
            gate=Gate("USR3_LEN", GateRule.NONZERO),
        ),
        Field(
            "USER_TXT",
            Scalar.CF,
            count_from="LOCL_CNT",
            width_from="TXT_LEN",
            gate=Gate("TXT_LEN", GateRule.NONZERO),
        ),
    ),
)

#: Every record this reader decodes, keyed by ``(REC_TYP, REC_SUB)``.
SPECS: Final[dict[tuple[int, int], RecordSpec]] = {
    spec.key: spec for spec in (FAR, MIR, PMR, PSR, PIR, PRR, FTR, STR)
}


__all__ = [
    "CPU_TYPE_BIG_ENDIAN",
    "CPU_TYPE_LITTLE_ENDIAN",
    "DATA_BIT_EXPECTED_VALUES",
    "DATA_BIT_IS_ASCII",
    "DATA_BIT_VALUES",
    "FAR",
    "FMU_FLG_FAL_MAP_BIT",
    "FMU_FLG_MASK_MAP_BIT",
    "FMU_FLG_PATTERN_CHANGED_BIT",
    "FTR",
    "MIR",
    "PIR",
    "PMR",
    "PRR",
    "PSR",
    "SPECS",
    "STR",
    "STR_CYCLE_ARRAY_ALIASES",
    "STR_DATA_FLG_BITS",
    "Z_VAL_MEANINGS",
    "Field",
    "Gate",
    "GateRule",
    "RecordSpec",
    "Scalar",
]
