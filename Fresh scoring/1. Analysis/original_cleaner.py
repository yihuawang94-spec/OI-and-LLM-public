# -*- coding: utf-8 -*-
"""Survey coding helper imported by 01_prepare_surveys.py.

The coding, pronoun definitions and employer-matching functions retain the
original study rules. The new preparation script calls build in all-record
mode, then Stata applies the sample exclusions. Run 01_prepare_surveys.py in
Spyder; this helper intentionally refuses direct execution.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

import numpy as np
import pandas as pd

try:
    import pyreadstat
except ImportError:  # pandas can still write a Stata 14 (release 118) file.
    pyreadstat = None


# =========================================================================
# DIRECTORY
# =========================================================================
BASE_DIR = str(Path(__file__).resolve().parent.parent / "0. Raw")
OUTPUT_DIR = str(Path(__file__).resolve().parent.parent / "2. Data")
GLASSDOOR_FILE = "Glassdoor.csv"
APPROVED_FILE = "Approved list.csv"
OUTPUT_FILE = "Glassdoor.dta"

SKIP_METADATA_ROWS = [1, 2]  # Qualtrics question text and ImportId rows.
DEFAULT_MIN_REVIEW_WORDS = 25
DEFAULT_SPEED_THRESHOLD_SEC = 180  # Flag only; never used to exclude a case.


# =========================================================================
# QUALTRICS EXPORT SCHEMA
# =========================================================================
COLS = {
    "response_id": "ResponseId",
    "firm_name": "Q24",
    "consent": "Intro2",
    "current": "Q23",
    "finished": "Finished",
    "progress": "Progress",
    "duration": "Duration (in seconds)",
    "overall_rating": "Q22_1",
    "seniority": "Q18",
    "tenure": "Q19",
    "firm_size": "Size",
    "age": "Demographics 2",
    "gender": "Demographics 1",
    "ethnicity": "Demographics 3",
    "pros": "Q25",
    "cons": "Q13",
    "advice": "Q12",
}
RATING_COLS = {
    "Q14_1": "rating_career",
    "Q14_2": "rating_comp",
    "Q14_3": "rating_culture",
    "Q14_4": "rating_diversity",
    "Q14_5": "rating_senior_mgmt",
    "Q14_6": "rating_wlb",
}
BINARY_COLS = {
    "Q15": "ceo_approval",
    "Q16": "recommend",
    "Q17": "outlook",
}

# Q22_1 is read whenever it is present in the export.
REQUIRED_SOURCE_COLUMNS = [
    COLS["response_id"],
    COLS["consent"],
    COLS["current"],
    COLS["finished"],
    COLS["progress"],
    COLS["duration"],
    COLS["seniority"],
    COLS["tenure"],
    COLS["firm_size"],
    COLS["age"],
    COLS["gender"],
    COLS["ethnicity"],
    COLS["pros"],
    COLS["cons"],
    COLS["advice"],
    *RATING_COLS,
    *BINARY_COLS,
]

# Accepted embedded/exported identity fields, in order of priority.
PID_CANDIDATES = (
    "PROLIFIC_PID",
    "Prolific_PID",
    "prolific_pid",
    "participant_id",
)
APPROVED_PID_COL = "Participant id"
APPROVED_STATUS_COL = "Status"


# =========================================================================
# CODEBOOKS
# =========================================================================
SENIORITY = {
    "Entry": 1,
    "Junior": 2,
    "Associate": 3,
    "Manager": 4,
    "Director": 5,
    "Executive": 6,
    "Senior Executive": 7,
}
STAR5 = {"Very bad": 1, "Bad": 2, "Fair": 3, "Good": 4, "Very good": 5}
STAR5_LABELS = {1: "Very bad", 2: "Bad", 3: "Fair", 4: "Good", 5: "Very good"}
OVERALL_RATING5 = {
    "Extremely bad": 1,
    "Very bad": 1,
    "Somewhat bad": 2,
    "Neither good nor bad": 3,
    "Somewhat good": 4,
    "Extremely good": 5,
    "Very good": 5,
}
BINARY = {"Good": 1, "Yes": 1, "Bad": 0, "No": 0}
FIRM_SIZE = {
    "1-50": 1,
    "51-200": 2,
    "201-500": 3,
    "501-1,000": 4,
    "1,001-5,000": 5,
    "5,001-10,000": 6,
    "More than 10,000": 7,
}
RANK_AND_FILE_MAX = 4

PROLIFIC_STATUS = {
    "APPROVED": 1,
    "AWAITING REVIEW": 2,
    "RETURNED": 3,
    "SCREENED OUT": 4,
    "TIMED-OUT": 5,
    "REJECTED": 6,
    "ACTIVE": 7,
    "OTHER / UNRECOGNISED": 8,
}
PROLIFIC_STATUS_LABELS = {
    0: "Not in Prolific list",
    1: "APPROVED",
    2: "AWAITING REVIEW",
    3: "RETURNED",
    4: "SCREENED OUT",
    5: "TIMED-OUT",
    6: "REJECTED",
    7: "ACTIVE",
    8: "Other / unrecognised status",
}

GENDER_ORDER = ["Male", "Female", "Other", "Prefer not to say"]
GENDER_SYN = {
    "man": "Male",
    "woman": "Female",
    "non-binary": "Other",
    "nonbinary": "Other",
    "non binary": "Other",
    "non-binary / third gender": "Other",
    "non-binary / other": "Other",
    "third gender": "Other",
    "genderqueer": "Other",
    "other (please specify)": "Other",
    "prefer not to answer": "Prefer not to say",
    "i prefer not to say": "Prefer not to say",
    "prefer not to disclose": "Prefer not to say",
}
ETH_ORDER = [
    "White",
    "Black or African American",
    "Hispanic or Latino",
    "Asian",
    "American Indian or Alaska Native",
    "Native Hawaiian or Pacific Islander",
    "Two or more races",
    "Other",
    "Prefer not to say",
]
ETH_SYN = {
    "caucasian": "White",
    "black": "Black or African American",
    "african american": "Black or African American",
    "hispanic": "Hispanic or Latino",
    "latino": "Hispanic or Latino",
    "hispanic/latino": "Hispanic or Latino",
    "hispanic, latino, or spanish origin": "Hispanic or Latino",
    "native american": "American Indian or Alaska Native",
    "american indian/alaska native": "American Indian or Alaska Native",
    "native hawaiian or other pacific islander": "Native Hawaiian or Pacific Islander",
    "pacific islander": "Native Hawaiian or Pacific Islander",
    "mixed": "Two or more races",
    "multiracial": "Two or more races",
    "two or more": "Two or more races",
    "other (please specify)": "Other",
    "prefer not to answer": "Prefer not to say",
}

FPP = re.compile(r"\b(?:ourselves|ours|our|us|we)\b")
FPS = re.compile(r"\b(?:myself|mine|my|me|i)\b")
TPP = re.compile(r"\b(?:themselves|theirs|their|them|they)\b")
PID24 = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{24})(?![A-Za-z0-9])")


# =========================================================================
# HELPERS
# =========================================================================
def _is_blank(value) -> bool:
    """Treat export blanks as missing without coercing a substantive No to NA."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"", "nan", "<na>"}


def _norm(value) -> str:
    """Normalise labels, including the different dash characters in firm size."""
    if _is_blank(value):
        return ""
    text = str(value).strip().lower()
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    return re.sub(r"\s+", " ", text)


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _text(value) -> str:
    return "" if _is_blank(value) else str(value).strip()


def clean_pid(value):
    """Extract a 24-character Prolific ID from a URL parameter or text field."""
    if _is_blank(value):
        return np.nan
    match = PID24.search(str(value).strip())
    return match.group(1).lower() if match else np.nan


def _map_codes(series: pd.Series, mapping: dict[str, int], varname: str) -> pd.Series:
    """Map a labelled Qualtrics response to a numeric code and report surprises."""
    lookup = {_norm(label): code for label, code in mapping.items()}
    normalised = series.map(_norm)
    result = normalised.map(lookup)
    unknown = sorted(
        {str(raw).strip() for raw, key in zip(series, normalised) if key and key not in lookup},
        key=str.lower,
    )
    if unknown:
        print(f"  [warn] {varname}: unmapped response label(s): {unknown}")
    return pd.to_numeric(result, errors="coerce")


def encode_categorical(
    series: pd.Series,
    order: list[str],
    synonyms: dict[str, str],
    varname: str,
) -> tuple[pd.Series, dict[int, str]]:
    """Encode known categories; retain unexpected categories with new labels."""
    lookup = {_norm(label): label for label in order}
    lookup.update({_norm(raw): canonical for raw, canonical in synonyms.items()})
    code_of = {label: i + 1 for i, label in enumerate(order)}
    labels = {i + 1: label for i, label in enumerate(order)}
    codes = pd.Series(np.nan, index=series.index, dtype=float)
    unknown: dict[str, str] = {}

    for index, raw in series.items():
        key = _norm(raw)
        if not key:
            continue
        canonical = lookup.get(key)
        if canonical is None:
            unknown.setdefault(key, str(raw).strip())
        else:
            codes.at[index] = code_of[canonical]

    if unknown:
        next_code = len(order) + 1
        for key, raw in sorted(unknown.items(), key=lambda item: item[1].lower()):
            labels[next_code] = raw
            codes.loc[series.map(_norm) == key] = next_code
            next_code += 1
        print(
            f"  [note] {varname}: unexpected category label(s) retained with "
            f"new codes: {sorted(unknown.values(), key=str.lower)}"
        )
    return codes, labels


def _combine_text(row: pd.Series, source_columns: tuple[str, ...]) -> str:
    return " ".join(part for part in (_text(row.get(column)) for column in source_columns) if part)


def _nis_pack(raw_text: str) -> dict[str, float]:
    """Return derived counts only; raw review text is never written to output."""
    text = raw_text.strip().lower()
    if not text:
        return {
            "wc": 0,
            "fpp": 0,
            "fps": 0,
            "tpp": 0,
            "we_ratio": np.nan,
            "we_density": np.nan,
            "nis": np.nan,
            "nis_fps": np.nan,
        }
    word_count = len(re.findall(r"\S+", text))
    fpp = len(FPP.findall(text))
    fps = len(FPS.findall(text))
    tpp = len(TPP.findall(text))
    personal_pronouns = fpp + fps
    return {
        "wc": word_count,
        "fpp": fpp,
        "fps": fps,
        "tpp": tpp,
        "we_ratio": fpp / personal_pronouns if personal_pronouns else np.nan,
        "we_density": fpp / word_count if word_count else np.nan,
        "nis": (fpp - tpp) / word_count if word_count else np.nan,
        "nis_fps": (fps - tpp) / word_count if word_count else np.nan,
    }


def _word_gate(raw: pd.Series, word_count: pd.Series, minimum: int) -> pd.Series:
    """Return NA when the review was not asked, rather than coding it as failure."""
    gate = pd.Series(np.nan, index=raw.index, dtype=float)
    answered = ~raw.map(_is_blank)
    gate.loc[answered] = (word_count.loc[answered] >= minimum).astype(float)
    return gate


def _self_test() -> None:
    text = "we love our team but they ignore us and we're tired of their excuses"
    pack = _nis_pack(text)
    assert pack["wc"] == 14
    assert pack["fpp"] == 4
    assert pack["tpp"] == 2
    assert np.isclose(pack["nis"], 2 / 14)

    fps_pack = _nis_pack(
        "I'm myself; I told my team that mine was with me, but they're elsewhere."
    )
    assert fps_pack["wc"] == 14
    assert fps_pack["fps"] == 6
    assert fps_pack["tpp"] == 1
    assert np.isclose(fps_pack["nis_fps"], 5 / 14)


def _require_columns(df: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_SOURCE_COLUMNS if column not in df.columns]
    if missing:
        available = ", ".join(df.columns)
        raise ValueError(
            "The CSV is not the expected Qualtrics export. "
            f"Missing required column(s): {missing}. Available columns: {available}"
        )


def _find_case_insensitive_column(df: pd.DataFrame, expected: str) -> str | None:
    exact = {str(column).strip().lower(): column for column in df.columns}
    return exact.get(expected.lower())


def _find_participant_ids(df: pd.DataFrame) -> tuple[pd.Series, str | None]:
    """Find the first candidate field that contains at least one valid PID."""
    for column in PID_CANDIDATES:
        if column not in df.columns:
            continue
        ids = df[column].map(clean_pid)
        count = int(ids.notna().sum())
        if count:
            print(f"  Prolific IDs read from {column!r}: {count}/{len(ids)} valid.")
            return ids, column
    print(
        "  [warn] No usable Prolific ID field was found. participant_id, "
        "prolific_status, approved, and analysis_sample are left missing. "
        "This export cannot be linked across the two survey waves until PID capture is fixed."
    )
    return pd.Series(np.nan, index=df.index, dtype=object), None


# =========================================================================
# PROLIFIC MATCHING
# =========================================================================
def match_prolific_status(
    participant_ids: pd.Series, approved_path: str | None
) -> tuple[pd.Series, pd.Series]:
    """Match known PIDs to a Prolific submissions export.

    Returns numeric status code and approved flag. A missing PID remains
    missing in both fields: it is not interpreted as a rejected/unapproved
    participant.
    """
    empty = pd.Series(np.nan, index=participant_ids.index, dtype=float)
    if not participant_ids.notna().any():
        return empty.copy(), empty.copy()
    if not approved_path:
        # This workflow deliberately uses explicit Stata criteria, not an approval list.
        return empty.copy(), empty.copy()
    if not os.path.exists(approved_path):
        print(
            f"  [warn] approved list not found at {approved_path!r}; "
            "prolific_status and approved are left missing."
        )
        return empty.copy(), empty.copy()

    approved_list = pd.read_csv(approved_path, dtype=str, keep_default_na=False)
    pid_col = _find_case_insensitive_column(approved_list, APPROVED_PID_COL)
    status_col = _find_case_insensitive_column(approved_list, APPROVED_STATUS_COL)
    if pid_col is None or status_col is None:
        raise ValueError(
            "Approved list must contain columns "
            f"{APPROVED_PID_COL!r} and {APPROVED_STATUS_COL!r}. "
            f"Found: {list(approved_list.columns)}"
        )

    approved_list = approved_list[[pid_col, status_col]].copy()
    approved_list["pid_clean"] = approved_list[pid_col].map(clean_pid)
    approved_list["status_clean"] = approved_list[status_col].map(_norm).str.upper()
    approved_list = approved_list.loc[approved_list["pid_clean"].notna()].copy()

    conflicts = (
        approved_list.groupby("pid_clean")["status_clean"].nunique().loc[lambda x: x > 1]
    )
    if not conflicts.empty:
        raise ValueError(
            "Approved list has conflicting statuses for PID(s): "
            f"{sorted(conflicts.index.tolist())}. Resolve those duplicates before matching."
        )
    duplicate_count = int(approved_list["pid_clean"].duplicated().sum())
    if duplicate_count:
        print(f"  [warn] approved list has {duplicate_count} duplicate PID row(s); keeping the last.")
        approved_list = approved_list.drop_duplicates("pid_clean", keep="last")

    status_map = approved_list.set_index("pid_clean")["status_clean"]
    status_text = participant_ids.map(status_map)
    status_text = status_text.where(status_text.notna() & status_text.ne(""))
    has_pid = participant_ids.notna()
    matched = status_text.notna()

    status_code = pd.Series(np.nan, index=participant_ids.index, dtype=float)
    status_code.loc[has_pid & ~matched] = 0
    mapped_codes = status_text.map(PROLIFIC_STATUS)
    unknown_status = sorted(status_text.loc[matched & mapped_codes.isna()].unique().tolist())
    if unknown_status:
        print(f"  [warn] unrecognised Prolific status(es) coded 8: {unknown_status}")
    status_code.loc[has_pid & matched] = mapped_codes.loc[has_pid & matched].fillna(8)

    approved = pd.Series(np.nan, index=participant_ids.index, dtype=float)
    approved.loc[has_pid & ~matched] = 0
    approved.loc[has_pid & matched] = (
        status_text.loc[has_pid & matched] == "APPROVED"
    ).astype(float)

    approved_ids = set(
        approved_list.loc[approved_list["status_clean"] == "APPROVED", "pid_clean"]
    )
    response_ids = set(participant_ids.dropna())
    approved_no_response = sorted(approved_ids - response_ids)

    print("  --- Prolific match reconciliation ---")
    print(f"    survey responses with a PID: {int(has_pid.sum())}")
    print(f"    responses not found in the list: {int((has_pid & ~matched).sum())}")
    print(
        f"    APPROVED in list: {len(approved_ids)} | "
        f"APPROVED with a response: {len(approved_ids & response_ids)} | "
        f"APPROVED with no response: {len(approved_no_response)}"
    )
    return status_code, approved


# =========================================================================
# BUILD
# =========================================================================
def build(
    csv_path: str,
    approved_path: str | None,
    min_review_words: int,
    speed_threshold: float | None,
    keep_ineligible: bool,
    require_pid: bool,
) -> tuple[pd.DataFrame, dict[str, dict[int, str]], dict[str, int]]:
    _self_test()
    raw = pd.read_csv(csv_path, skiprows=SKIP_METADATA_ROWS, dtype=str, keep_default_na=False)
    _require_columns(raw)

    out = pd.DataFrame(index=raw.index)
    out["response_id"] = raw[COLS["response_id"]]

    # Preserve employer spelling and case; trim outer whitespace only.
    # Prefer the entered name, falling back to embedded FirmName for blanks.
    out["firm_name"] = ""
    firm_sources = [
        _find_case_insensitive_column(raw, expected)
        for expected in (COLS["firm_name"], "FirmName")
    ]
    for source in firm_sources:
        if source is not None:
            out["firm_name"] = out["firm_name"].where(
                out["firm_name"].ne(""), raw[source].map(_text)
            )
    if not any(source is not None for source in firm_sources):
        print("  [warn] Neither Q24 nor FirmName was exported; firm_name is blank.")

    participant_ids, pid_source = _find_participant_ids(raw)
    out["participant_id"] = participant_ids
    out["pid_available"] = participant_ids.notna().astype(float)

    out["consent_yes"] = _map_codes(
        raw[COLS["consent"]],
        {
            "Yes": 1,
            "No": 0,
            "I agree": 1,
            "I do not agree": 0,
            "I agree to participate in this study.": 1,
            "No, I do NOT wish to participate in this study.": 0,
        },
        "consent",
    )
    out["is_current_yes"] = _map_codes(raw[COLS["current"]], {"Yes": 1, "No": 0}, "current employee")
    out["finished"] = _map_codes(
        raw[COLS["finished"]], {"True": 1, "False": 0, "Yes": 1, "No": 0}, "Finished"
    )
    out["progress"] = _num(raw[COLS["progress"]])
    out["duration_sec"] = _num(raw[COLS["duration"]])
    if speed_threshold is None:
        out["speed_flag"] = np.nan
    else:
        out["speed_flag"] = np.where(
            out["duration_sec"].notna(),
            (out["duration_sec"] < speed_threshold).astype(float),
            np.nan,
        )

    # Read the respondent's own overall-rating item when it is exported.
    out["overall_rating"] = np.nan
    if COLS["overall_rating"] in raw.columns:
        out["overall_rating"] = _map_codes(
            raw[COLS["overall_rating"]], OVERALL_RATING5, "overall rating"
        )
    else:
        print("  [note] Q22_1 (overall rating) not present; overall_rating is missing.")

    out["seniority"] = _map_codes(raw[COLS["seniority"]], SENIORITY, "seniority")
    out["rank_and_file"] = np.where(
        out["seniority"].notna(), (out["seniority"] <= RANK_AND_FILE_MAX).astype(float), np.nan
    )
    out["tenure_years"] = _num(raw[COLS["tenure"]])
    out["firm_size"] = _map_codes(raw[COLS["firm_size"]], FIRM_SIZE, "firm size")
    out["age"] = _num(raw[COLS["age"]])
    out["gender"], gender_labels = encode_categorical(
        raw[COLS["gender"]], GENDER_ORDER, GENDER_SYN, "gender"
    )
    out["ethnicity"], ethnicity_labels = encode_categorical(
        raw[COLS["ethnicity"]], ETH_ORDER, ETH_SYN, "ethnicity"
    )

    for source, destination in RATING_COLS.items():
        out[destination] = _map_codes(raw[source], STAR5, destination)
    for source, destination in BINARY_COLS.items():
        out[destination] = _map_codes(raw[source], BINARY, destination)

    out["pros_wc"] = raw[COLS["pros"]].map(lambda value: _nis_pack(_text(value))["wc"])
    out["cons_wc"] = raw[COLS["cons"]].map(lambda value: _nis_pack(_text(value))["wc"])
    out["advice_wc"] = raw[COLS["advice"]].map(lambda value: _nis_pack(_text(value))["wc"])
    out["pros_word_gate"] = _word_gate(raw[COLS["pros"]], out["pros_wc"], min_review_words)
    out["cons_word_gate"] = _word_gate(raw[COLS["cons"]], out["cons_wc"], min_review_words)
    out["review_word_gate"] = np.where(
        out["pros_word_gate"].eq(1) & out["cons_word_gate"].eq(1),
        1.0,
        np.where(
            out["pros_word_gate"].notna() & out["cons_word_gate"].notna(),
            0.0,
            np.nan,
        ),
    )

    all_text = raw.apply(
        _combine_text, axis=1, source_columns=(COLS["pros"], COLS["cons"], COLS["advice"])
    )
    all_nis = pd.DataFrame([_nis_pack(text) for text in all_text], index=raw.index)

    out["review_wc"] = all_nis["wc"]
    out["fpp_n"] = all_nis["fpp"]
    out["fps_n"] = all_nis["fps"]
    out["tpp_n"] = all_nis["tpp"]
    out["we_ratio"] = all_nis["we_ratio"]
    out["we_density"] = all_nis["we_density"]
    out["nis_all_text"] = all_nis["nis"]
    out["nis_fps"] = all_nis["nis_fps"]

    mandatory_answers = [
        COLS["pros"],
        COLS["cons"],
        COLS["seniority"],
        COLS["tenure"],
        COLS["firm_size"],
        COLS["age"],
        COLS["gender"],
        COLS["ethnicity"],
        *RATING_COLS,
        *BINARY_COLS,
    ]
    # When the overall-rating item is present, a blank answer makes the case incomplete.
    if COLS["overall_rating"] in raw.columns:
        mandatory_answers.append(COLS["overall_rating"])
    all_answers_present = pd.Series(True, index=raw.index)
    for column in mandatory_answers:
        all_answers_present &= ~raw[column].map(_is_blank)

    out["survey_complete"] = (
        out["consent_yes"].eq(1)
        & out["is_current_yes"].eq(1)
        & out["finished"].eq(1)
        & out["progress"].eq(100)
        & all_answers_present
    ).astype(float)
    out["analysis_eligible"] = (
        out["survey_complete"].eq(1) & out["review_word_gate"].eq(1)
    ).astype(float)

    if require_pid:
        eligible_without_pid = out["analysis_eligible"].eq(1) & out["participant_id"].isna()
        if eligible_without_pid.any():
            raise ValueError(
                f"{int(eligible_without_pid.sum())} analysis-eligible row(s) have no usable "
                "Prolific PID. Stop here rather than create data that cannot be merged across waves."
            )

    eligible_pids = out.loc[
        out["analysis_eligible"].eq(1) & out["participant_id"].notna(), "participant_id"
    ]
    duplicate_pids = sorted(eligible_pids.loc[eligible_pids.duplicated(keep=False)].unique())
    if duplicate_pids:
        shown = ", ".join(duplicate_pids[:10])
        suffix = " ..." if len(duplicate_pids) > 10 else ""
        raise ValueError(
            "Analysis-eligible records are not one-row-per-participant. "
            f"Duplicate Prolific PID(s): {shown}{suffix}. Resolve them before merging across waves."
        )

    out["prolific_status"], out["approved"] = match_prolific_status(
        out["participant_id"], approved_path
    )
    out["analysis_sample"] = np.nan
    approved_known = out["approved"].notna()
    out.loc[approved_known, "analysis_sample"] = (
        out.loc[approved_known, "analysis_eligible"].eq(1)
        & out.loc[approved_known, "approved"].eq(1)
    ).astype(float)

    audit = {
        "raw_rows": len(out),
        "consented": int(out["consent_yes"].eq(1).sum()),
        "current_yes": int(out["is_current_yes"].eq(1).sum()),
        "survey_complete": int(out["survey_complete"].eq(1).sum()),
        "analysis_eligible": int(out["analysis_eligible"].eq(1).sum()),
        "eligible_without_pid": int(
            (out["analysis_eligible"].eq(1) & out["participant_id"].isna()).sum()
        ),
        "speed_flagged": int(
            (out["speed_flag"].eq(1) & out["analysis_eligible"].eq(1)).sum()
        )
        if speed_threshold is not None
        else 0,
        "pid_source_found": int(pid_source is not None),
    }

    if not keep_ineligible:
        out = out.loc[out["analysis_eligible"].eq(1)].copy()
    return out, {"gender": gender_labels, "ethnicity": ethnicity_labels}, audit


# =========================================================================
# STATA OUTPUT
# =========================================================================
VAR_LABELS = {
    "response_id": "Qualtrics ResponseId (internal linkage only)",
    "firm_name": "Employer name (Wave 1)",
    "participant_id": "Prolific participant ID (cleaned)",
    "pid_available": "Usable Prolific ID present",
    "prolific_status": "Prolific submission status",
    "approved": "Prolific status is APPROVED",
    "analysis_sample": "Analysis-eligible and Prolific APPROVED",
    "consent_yes": "Consented to participate",
    "is_current_yes": "Currently employed",
    "finished": "Qualtrics Finished flag",
    "progress": "Qualtrics progress percentage",
    "duration_sec": "Qualtrics duration in seconds",
    "speed_flag": "Duration below configured speed threshold (flag only)",
    "survey_complete": "Completed required survey measures",
    "pros_word_gate": "Pros meets minimum word count",
    "cons_word_gate": "Cons meets minimum word count",
    "review_word_gate": "Both required review fields meet word count",
    "analysis_eligible": "Eligible analytic response",
    "overall_rating": "Overall experience rating (optional item)",
    "seniority": "Seniority, Entry to Senior Executive",
    "rank_and_file": "Rank-and-file (seniority 1 to 4)",
    "tenure_years": "Tenure in years",
    "firm_size": "Employer size category",
    "age": "Age in years",
    "gender": "Gender",
    "ethnicity": "Ethnicity",
    "rating_career": "Career opportunities rating, 1 to 5",
    "rating_comp": "Compensation and benefits rating, 1 to 5",
    "rating_culture": "Culture and values rating, 1 to 5",
    "rating_diversity": "Diversity and inclusion rating, 1 to 5",
    "rating_senior_mgmt": "Senior management rating, 1 to 5",
    "rating_wlb": "Work-life balance rating, 1 to 5",
    "ceo_approval": "CEO approval: Good=1, Bad=0",
    "recommend": "Would recommend to friend: Yes=1, No=0",
    "outlook": "Six-month business outlook: Good=1, Bad=0",
    "pros_wc": "Pros word count",
    "cons_wc": "Cons word count",
    "advice_wc": "Advice word count (optional field)",
    "review_wc": "Pros plus Cons plus Advice word count",
    "fpp_n": "FPP count in Pros plus Cons plus Advice",
    "fps_n": "FPS count in Pros plus Cons plus Advice",
    "tpp_n": "TPP count in Pros plus Cons plus Advice",
    "we_ratio": "FPP/(FPP+FPS) in all review text",
    "we_density": "FPP/all-review word count",
    "nis_all_text": "NIS=(FPP-TPP)/all review words",
    "nis_fps": "NIS-FPS=(FPS-TPP)/all review words",
}

VAL_LABELS_STATIC = {
    "pid_available": {0: "No", 1: "Yes"},
    "prolific_status": PROLIFIC_STATUS_LABELS,
    "approved": {0: "Not approved/not in list", 1: "Approved"},
    "analysis_sample": {0: "No", 1: "Yes"},
    "consent_yes": {0: "No", 1: "Yes"},
    "is_current_yes": {0: "No", 1: "Yes"},
    "finished": {0: "No", 1: "Yes"},
    "speed_flag": {0: "No", 1: "Yes"},
    "survey_complete": {0: "No", 1: "Yes"},
    "pros_word_gate": {0: "No", 1: "Yes"},
    "cons_word_gate": {0: "No", 1: "Yes"},
    "review_word_gate": {0: "No", 1: "Yes"},
    "analysis_eligible": {0: "No", 1: "Yes"},
    "overall_rating": {
        1: "Extremely bad",
        2: "Somewhat bad",
        3: "Neither good nor bad",
        4: "Somewhat good",
        5: "Extremely good",
    },
    "seniority": {
        1: "Entry",
        2: "Junior",
        3: "Associate",
        4: "Manager",
        5: "Director",
        6: "Executive",
        7: "Senior Executive",
    },
    "rank_and_file": {0: "Senior (5 to 7)", 1: "Rank-and-file (1 to 4)"},
    "firm_size": {
        1: "1-50",
        2: "51-200",
        3: "201-500",
        4: "501-1,000",
        5: "1,001-5,000",
        6: "5,001-10,000",
        7: "More than 10,000",
    },
    "rating_career": STAR5_LABELS,
    "rating_comp": STAR5_LABELS,
    "rating_culture": STAR5_LABELS,
    "rating_diversity": STAR5_LABELS,
    "rating_senior_mgmt": STAR5_LABELS,
    "rating_wlb": STAR5_LABELS,
    "ceo_approval": {0: "Bad", 1: "Good"},
    "recommend": {0: "No", 1: "Yes"},
    "outlook": {0: "Bad", 1: "Good"},
}


def _stata_ready(df: pd.DataFrame) -> pd.DataFrame:
    """Use ordinary strings, rather than pandas extension strings, for Stata."""
    result = df.copy()
    for column in ("response_id", "participant_id", "firm_name"):
        result[column] = result[column].where(result[column].notna(), "").astype(str)
    return result


def write_dta(
    df: pd.DataFrame,
    out_path: str,
    dynamic_value_labels: dict[str, dict[int, str]],
) -> str:
    output_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(output_dir, exist_ok=True)
    value_labels = {**VAL_LABELS_STATIC, **dynamic_value_labels}
    ready = _stata_ready(df)

    if pyreadstat is not None:
        pyreadstat.write_dta(
            ready,
            out_path,
            version=14,
            column_labels=VAR_LABELS,
            variable_value_labels=value_labels,
        )
        return "pyreadstat (Stata 14)"

    ready.to_stata(
        out_path,
        write_index=False,
        version=118,
        variable_labels=VAR_LABELS,
        value_labels=value_labels,
    )
    return "pandas (Stata release 118 / Stata 14)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a cleaned Glassdoor NIS Stata data set."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=os.path.join(BASE_DIR, GLASSDOOR_FILE),
        help=f"Qualtrics CSV (default: BASE_DIR\\{GLASSDOOR_FILE})",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=os.path.join(OUTPUT_DIR, OUTPUT_FILE),
        help=f"Output .dta (default: OUTPUT_DIR\\{OUTPUT_FILE})",
    )
    parser.add_argument(
        "--approved",
        default=os.path.join(BASE_DIR, APPROVED_FILE),
        help=f"Prolific submissions CSV (default: BASE_DIR\\{APPROVED_FILE})",
    )
    parser.add_argument(
        "--min-review-words",
        type=int,
        default=DEFAULT_MIN_REVIEW_WORDS,
        help=f"Minimum words in each required review field (default: {DEFAULT_MIN_REVIEW_WORDS})",
    )
    parser.add_argument(
        "--speed-threshold",
        type=float,
        default=DEFAULT_SPEED_THRESHOLD_SEC,
        help=(
            "Flag, but do not exclude, durations below this many seconds "
            f"(default: {DEFAULT_SPEED_THRESHOLD_SEC})"
        ),
    )
    parser.add_argument(
        "--no-speed-flag",
        action="store_true",
        help="Leave speed_flag missing instead of applying a threshold.",
    )
    parser.add_argument(
        "--keep-ineligible",
        action="store_true",
        help="Retain cleaned refusals/screen-outs instead of exporting only analytic cases.",
    )
    parser.add_argument(
        "--require-pid",
        action="store_true",
        help="Stop if any analysis-eligible row lacks a valid Prolific PID.",
    )
    parser.add_argument(
        "--wave2",
        default=os.path.join(BASE_DIR, "Glassdoor W2.csv"),
        help="Raw Wave 2 CSV (default: BASE_DIR\\Glassdoor W2.csv).",
    )
    parser.add_argument(
        "--firm-matches-output",
        default=os.path.join(OUTPUT_DIR, "Glassdoor_firm_matches.dta"),
        help="Participant-level firm-name dummies (default: OUTPUT_DIR\\Glassdoor_firm_matches.dta).",
    )
    args = parser.parse_args()

    if args.min_review_words < 1:
        parser.error("--min-review-words must be at least 1")
    if args.speed_threshold is not None and args.speed_threshold < 0:
        parser.error("--speed-threshold cannot be negative")

    speed_threshold = None if args.no_speed_flag else args.speed_threshold
    try:
        cleaned, dynamic_labels, audit = build(
            args.input,
            args.approved,
            args.min_review_words,
            speed_threshold,
            args.keep_ineligible,
            args.require_pid,
        )
    except (OSError, ValueError, pd.errors.ParserError) as error:
        parser.error(str(error))
    writer = write_dta(cleaned, args.output, dynamic_labels)

    print(f"Wrote {args.output} ({len(cleaned)} rows x {len(cleaned.columns)} variables; {writer}).")
    print(
        "  raw={raw_rows}; consented={consented}; current_yes={current_yes}; "
        "survey_complete={survey_complete}; analysis_eligible={analysis_eligible}".format(**audit)
    )
    if speed_threshold is not None:
        print(
            f"  speed-flagged among analysis-eligible rows (<{speed_threshold:g}s, not excluded): "
            f"{audit['speed_flagged']}"
        )
    if audit["eligible_without_pid"]:
        print(
            f"  [action needed] {audit['eligible_without_pid']} analysis-eligible row(s) "
            "lack a PID and cannot be linked to Wave 2."
        )


    # Run after the unchanged Wave 1 cleaning/output. Read raw Wave 2 names;
    # no combined DTA or separate reconciliation script is needed.
    try:
        firm_matches, firm_audit = build_firm_matches(cleaned, args.wave2)
        write_firm_matches(firm_matches, args.firm_matches_output)
    except (OSError, ValueError, pd.errors.ParserError) as error:
        parser.error(str(error))
    print(
        f"Wrote {args.firm_matches_output} ({len(firm_matches)} linked participants; "
        "participant_id, firm_match_strict, firm_match_lenient, firm_match_unclear)."
    )
    print(
        "  strict={strict}; lenient={lenient}; formatting={formatting}; "
        "legal_suffix={legal_suffix}; spelling={spelling}; "
        "different_names={different_names}; "
        "unclear={unclear}; unresolved_or_invalid={unresolved}; "
        "missing_names={missing_names}".format(**firm_audit)
    )


# =========================================================================
# TWO-WAVE EMPLOYER-NAME RECONCILIATION (ADDED AFTER THE ORIGINAL CLEANER)
# =========================================================================
# Raw files: BASE_DIR/Glassdoor.csv and BASE_DIR/Glassdoor W2.csv.
# Output list: OUTPUT_DIR/Glassdoor_firm_matches.dta, where OUTPUT_DIR is $data.
# Wave 1 names below come from the cleaned frame produced from the raw CSV in
# this same run. Its filters and measures are unchanged. No saved analysis or
# merged two-wave file feeds back into Python.
# Only participants present in both waves enter the list; absent-wave IDs have
# missing dummies when Stata merges it. For linked IDs each dummy is always 0/1.
# Strict/lenient: 1 = accepted match; 0 = no accepted match.
# Unclear: 1 = missing/unusable names, short abbreviations, generic descriptions,
# or a textual link/near miss that fails the spelling rule; 0 = other pairs.
# Thus lenient=0 and unclear=0 flags different reported names under these rules.
# These categories use text only; they do not verify a change of employer.
# Matching firm names alone does not establish employer identity.

import unicodedata

# Fixed rules: no pair-by-pair approvals or manually reviewed aliases.
# A spelling match requires at most one insertion, deletion, substitution,
# or adjacent-letter swap, with both compact names at least six characters.
# Similarity = 1 - edits / longer compact-name length; require at least 0.85.
# Change only one word, retaining word order/count; formatting-only matches
# may differ in word spacing. Digits must stay identical. Protect short words:
# substitutions/swaps need >=5 letters in the changed word; insertions/deletions
# need >=4 in the shorter word. These guards prevent Uber Services/User Services
# from passing merely because they share a long generic description.
FIRM_MIN_FUZZY_LENGTH = 6
FIRM_MAX_EDITS = 1
FIRM_MIN_SIMILARITY = 0.85
FIRM_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "llc", "llp", "plc",
})
# These general descriptions affect ONLY the unclear flag, never acceptance.
FIRM_GENERIC_WORDS = frozenset({
    "a", "an", "the", "and", "of", "for", "in", "company", "co",
    "organization", "organisation", "business", "professional",
    "consulting", "consultancy", "firm", "industry", "industries",
    "service", "services", "manufacturing", "engineering", "food",
    "finance", "financial", "insurance", "lab", "laboratory",
    "laboratories", "pharma", "pharmaceutical", "medical", "health",
    "healthcare", "hospital", "center", "centre", "government",
    "department", "county", "school", "schools", "university",
    "national", "technology", "technologies", "staffing", "group",
    "science", "sciences", "logistics", "real", "estate",
})


def _firm_name_key(value) -> str:
    """Strict match ignores case and outer whitespace only."""
    return _text(value).casefold()


def _firm_format_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _firm_tokens(value: str) -> list[str]:
    """Split punctuation/space and drop only whole trailing legal suffixes."""
    value = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    while tokens and tokens[-1] in FIRM_LEGAL_SUFFIXES:
        tokens.pop()
    return tokens


def _firm_edit_distance(a: str, b: str) -> int:
    """Character edit distance, including an adjacent-letter swap as one edit.

    This is optimal-string-alignment distance. Its one-edit acceptance rule
    covers insertions, deletions, substitutions and adjacent transpositions.
    """
    rows = [list(range(len(b) + 1))]
    for i, left in enumerate(a, 1):
        row = [i]
        for j, right in enumerate(b, 1):
            cost = int(left != right)
            distance = min(rows[i - 1][j] + 1,
                           row[j - 1] + 1,
                           rows[i - 1][j - 1] + cost)
            if (i > 1 and j > 1 and left == b[j - 2]
                    and a[i - 2] == right):
                distance = min(distance, rows[i - 2][j - 2] + 1)
            row.append(distance)
        rows.append(row)
    return rows[-1][-1]


def _firm_spelling_match(a: list[str], b: list[str]) -> bool:
    key_a, key_b = "".join(a), "".join(b)
    if min(len(key_a), len(key_b)) < FIRM_MIN_FUZZY_LENGTH:
        return False
    if len(a) != len(b):
        return False
    if re.findall(r"\d+", " ".join(a)) != re.findall(r"\d+", " ".join(b)):
        return False
    changed = [(left, right) for left, right in zip(a, b) if left != right]
    if len(changed) != 1:
        return False
    left, right = changed[0]
    minimum = 5 if len(left) == len(right) else 4
    if min(len(left), len(right)) < minimum:
        return False
    edits = _firm_edit_distance(left, right)
    similarity = 1 - edits / max(len(key_a), len(key_b))
    return 0 < edits <= FIRM_MAX_EDITS and similarity >= FIRM_MIN_SIMILARITY


def _firm_uncertain(a: list[str], b: list[str]) -> bool:
    """Mark ambiguous rejected text using fixed rules, without accepting it."""
    key_a, key_b = "".join(a), "".join(b)
    core_a = [word for word in a if word not in FIRM_GENERIC_WORDS]
    core_b = [word for word in b if word not in FIRM_GENERIC_WORDS]
    if not core_a or not core_b:
        return True  # A generic description does not establish identity.
    if min(len(key_a), len(key_b)) <= 3:
        return True  # A short unexplained abbreviation is inconclusive.
    if key_a == "".join(word[0] for word in b):
        return True
    if key_b == "".join(word[0] for word in a):
        return True
    distinctive_a = {word for word in core_a if len(word) >= 4}
    distinctive_b = {word for word in core_b if len(word) >= 4}
    if distinctive_a & distinctive_b:
        return True  # Shared distinctive text, but not a spelling match.
    if (min(len(key_a), len(key_b)) >= 4
            and (key_a in key_b or key_b in key_a)):
        return True
    return _firm_edit_distance(key_a, key_b) <= FIRM_MAX_EDITS + 1


def _firm_dummies(wave1_name, wave2_name) -> tuple[int, int, str]:
    a, b = _firm_name_key(wave1_name), _firm_name_key(wave2_name)
    if not a or not b:
        return 0, 0, "missing_names"
    if re.fullmatch(r"[0-9a-f]{24}", a) or re.fullmatch(r"[0-9a-f]{24}", b):
        return 0, 0, "invalid_name"
    format_a, format_b = _firm_format_key(a), _firm_format_key(b)
    if not format_a or not format_b:
        return 0, 0, "invalid_name"
    if a == b:
        return 1, 1, "exact"
    if (re.findall(r"\d+", unicodedata.normalize("NFKC", a))
            != re.findall(r"\d+", unicodedata.normalize("NFKC", b))):
        return 0, 0, "different_names"
    if format_a == format_b:
        return 0, 1, "lenient_format"
    tokens_a, tokens_b = _firm_tokens(a), _firm_tokens(b)
    if not tokens_a or not tokens_b:
        return 0, 0, "invalid_name"
    if "".join(tokens_a) == "".join(tokens_b):
        return 0, 1, "lenient_suffix"
    if _firm_spelling_match(tokens_a, tokens_b):
        return 0, 1, "lenient_spelling"
    if _firm_uncertain(tokens_a, tokens_b):
        return 0, 0, "uncertain"
    # Different reported names under the rules, not a proven employer change.
    return 0, 0, "different_names"


def _read_wave2_firm_names(csv_path: str) -> pd.DataFrame:
    """Read raw Wave 2, with or without Qualtrics' two metadata rows."""
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    # An ImportId row in the first two records identifies Qualtrics metadata.
    # Read parsed records (not physical lines), since question text can be multiline.
    last_metadata = -1
    for position in range(min(2, len(raw))):
        if any(re.match(r'^\s*\{\s*"ImportId"\s*:', str(value))
               for value in raw.iloc[position]):
            last_metadata = position
    if last_metadata >= 0:
        raw = raw.iloc[last_metadata + 1:].copy()

    pid_column = None
    for candidate in PID_CANDIDATES:
        column = _find_case_insensitive_column(raw, candidate)
        if column is not None and raw[column].map(clean_pid).notna().any():
            pid_column = column
            break
    if pid_column is None:
        raise ValueError("Raw Wave 2 CSV has no usable Prolific participant ID column.")
    # Also accept an export with only a question-label row left at the top.
    if len(raw) and _text(raw.iloc[0][pid_column]).casefold() == pid_column.casefold():
        raw = raw.iloc[1:].copy()
    firm_column = _find_case_insensitive_column(raw, "Q24")
    if firm_column is None:
        raise ValueError("Raw Wave 2 CSV has no Q24 entered-employer-name column.")
    # Embedded FirmName may repeat Wave 1; it must NOT substitute for Wave 2 Q24.
    names = pd.DataFrame({
        "participant_id": raw[pid_column].map(clean_pid),
        "w2_firm_name": raw[firm_column].map(_text),
    })
    if names.participant_id.isna().any():
        raise ValueError("Raw Wave 2 contains responses with missing/invalid participant IDs.")
    if names.participant_id.duplicated().any():
        raise ValueError("Raw Wave 2 has duplicate participant IDs; resolve them before merging.")
    return names


def build_firm_matches(
    wave1_cleaned: pd.DataFrame, wave2_csv: str
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Compare current raw-source wave names, returning IDs and three dummies."""
    wave1 = wave1_cleaned[["participant_id", "firm_name"]].copy()
    wave1["participant_id"] = wave1.participant_id.map(clean_pid)
    wave1 = wave1.loc[wave1.participant_id.notna()].copy()
    if wave1.participant_id.duplicated().any():
        raise ValueError("Cleaned Wave 1 has duplicate participant IDs; resolve them before merging.")
    wave2 = _read_wave2_firm_names(wave2_csv)
    paired = wave1.merge(wave2, on="participant_id", how="inner", validate="one_to_one")
    decisions = [_firm_dummies(a, b)
                 for a, b in zip(paired.firm_name, paired.w2_firm_name)]
    dummies = pd.DataFrame(decisions, columns=["firm_match_strict", "firm_match_lenient", "status"])
    output = paired[["participant_id"]].reset_index(drop=True)
    for column in ("firm_match_strict", "firm_match_lenient"):
        output[column] = dummies[column].astype(np.int8)
    output["firm_match_unclear"] = dummies.status.isin(
        ["uncertain", "invalid_name", "missing_names"]
    ).astype(np.int8)
    if output.participant_id.duplicated().any():
        raise ValueError("Firm-match output is not one row per participant.")
    for column in ("firm_match_strict", "firm_match_lenient", "firm_match_unclear"):
        if not output[column].isin([0, 1]).all():
            raise ValueError(f"{column} contains a value other than 0/1.")
    output = output.sort_values("participant_id", kind="stable").reset_index(drop=True)
    audit = {
        "strict": int(output.firm_match_strict.sum()),
        "lenient": int(output.firm_match_lenient.sum()),
        "formatting": int(dummies.status.eq("lenient_format").sum()),
        "legal_suffix": int(dummies.status.eq("lenient_suffix").sum()),
        "spelling": int(dummies.status.eq("lenient_spelling").sum()),
        "different_names": int(dummies.status.eq("different_names").sum()),
        "unclear": int(output.firm_match_unclear.sum()),
        "unresolved": int(dummies.status.isin(["uncertain", "invalid_name"]).sum()),
        "missing_names": int(dummies.status.eq("missing_names").sum()),
    }
    return output, audit


def write_firm_matches(frame: pd.DataFrame, out_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    labels = {
        "participant_id": "Prolific participant ID (cleaned)",
        "firm_match_strict": "Strict firm-name match (1=yes, 0=no accepted match)",
        "firm_match_lenient": "Rule-based name match: formatting/suffix/one edit (1=yes)",
        "firm_match_unclear": "Name pair unresolved by fixed text rules (1=yes)",
    }
    values = {column: {0: "No accepted match", 1: "Accepted match"}
              for column in ("firm_match_strict", "firm_match_lenient")}
    values["firm_match_unclear"] = {0: "Assessed by text rules", 1: "Unclear or unusable pair"}
    if pyreadstat is not None:
        pyreadstat.write_dta(frame, out_path, version=14,
                            column_labels=labels, variable_value_labels=values)
    else:
        frame.to_stata(out_path, write_index=False, version=118,
                       variable_labels=labels, value_labels=values)


if __name__ == "__main__":
    raise SystemExit("This file is a coding helper. Run 01_prepare_surveys.py in Spyder instead.")
