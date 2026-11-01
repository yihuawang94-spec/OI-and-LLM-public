#!/usr/bin/env python3
"""Run in Spyder to code both survey exports WITHOUT excluding respondents.

Input: unchanged CSV exports in 0. Raw. Output: ALL Wave 1 response records in
2. Data/Prolific_all_respondents.dta and ALL Wave 2 response records in
2. Data/Prolific_all_wave2.dta. Run master.do sample next: Stata applies and
reports every sample restriction. This script never writes to either raw file.

Qualtrics includes two metadata records (question labels and ImportId objects)
above the responses. Those records are not participants. The bundled Wave 1
file contains 1,050 response records; Wave 2 contains 740. All remain in the raw
CSVs and all response records remain in these derived files.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import numpy as np
import pandas as pd

ANALYSIS = Path(__file__).resolve().parent
PROJECT = ANALYSIS.parent
if str(ANALYSIS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS))
import original_cleaner as cleaner

LIKERT = {
    "strongly disagree": 1, "disagree": 2, "somewhat disagree": 3,
    "neither agree nor disagree": 4, "somewhat agree": 5,
    "agree": 6, "strongly agree": 7,
}
HUMAN_ITEMS = [f"w2_oi_{i}" for i in range(1, 7)]
TURNOVER_ITEMS = [f"w2_turnover_{i}" for i in range(1, 4)]
BUNDLED_HASHES = {
    "Glassdoor.csv": "02ba59c25c608248cdbfc3f028aa4d1bb30fc17a3eda88a4e63d6bd2bae4b939",
    "Glassdoor W2.csv": "e4d459bd172fb1f7c728e3a5156f8d9b28c4df46f354971cacae0b86efd5eeef",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_export(path: Path) -> tuple[pd.DataFrame, int]:
    """Parse CSV records; only identify Qualtrics' leading metadata records."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    last_metadata = -1
    for pos in range(min(2, len(raw))):
        if any(re.match(r'^\s*\{\s*"ImportId"\s*:', str(v)) for v in raw.iloc[pos]):
            last_metadata = pos
    metadata_n = last_metadata + 1
    if not metadata_n:
        pid_cols = [c for c in raw if c.lower() in {"prolific_pid", "participant_id"}]
        if len(raw) and pid_cols and raw.iloc[0][pid_cols[0]].strip().lower() == pid_cols[0].lower():
            metadata_n = 1
    return raw.iloc[metadata_n:].copy().reset_index(drop=True), metadata_n


def check_labels(series: pd.Series, mapping: dict, name: str) -> None:
    allowed = {cleaner._norm(k) for k in mapping}
    unexpected = sorted({str(v).strip() for v in series
                         if cleaner._norm(v) and cleaner._norm(v) not in allowed})
    if unexpected:
        raise ValueError(f"{name}: unrecognised response labels: {unexpected}")


def numeric(series: pd.Series, name: str) -> pd.Series:
    stripped = series.map(cleaner._text)
    result = pd.to_numeric(stripped, errors="coerce")
    bad = stripped.ne("") & (result.isna() | ~np.isfinite(result))
    if bad.any():
        raise ValueError(f"{name}: nonnumeric or nonfinite responses: {sorted(set(stripped[bad]))}")
    return result


def validate_ids(raw: pd.DataFrame, wave: str) -> pd.Series:
    """Stop for ambiguous linkage; never silently drop or deduplicate records."""
    if "ResponseId" not in raw:
        raise ValueError(f"{wave}: ResponseId is required to retain every response.")
    ids = raw.ResponseId.map(cleaner._text)
    if ids.eq("").any() or ids.duplicated().any():
        raise ValueError(f"{wave}: missing or duplicate ResponseId; no records were removed.")
    pids, _ = cleaner._find_participant_ids(raw)
    duplicate = pids.notna() & pids.duplicated(keep=False)
    if duplicate.any():
        raise ValueError(f"{wave}: {int(duplicate.sum())} records have duplicate participant IDs; "
                         "resolve linkage explicitly. No records were removed.")
    print(f"{wave}: {len(raw):,} response records; {int(pids.isna().sum()):,} missing/invalid "
          "participant IDs; 0 duplicated participant IDs.")
    return pids


def validate_wave1(raw: pd.DataFrame) -> None:
    cleaner._require_columns(raw)
    maps = {
        "Intro2": {"Yes": 1, "No": 0, "I agree": 1, "I do not agree": 0,
                   "I agree to participate in this study.": 1,
                   "No, I do NOT wish to participate in this study.": 0},
        "Q23": {"Yes": 1, "No": 0},
        "Finished": {"True": 1, "False": 0, "Yes": 1, "No": 0},
        "Q18": cleaner.SENIORITY, "Size": cleaner.FIRM_SIZE,
        **{c: cleaner.STAR5 for c in cleaner.RATING_COLS},
        **{c: cleaner.BINARY for c in cleaner.BINARY_COLS},
    }
    if "Q22_1" not in raw:
        raise ValueError("Wave 1 lacks its original overall-rating item, Q22_1.")
    maps["Q22_1"] = cleaner.OVERALL_RATING5
    for col, mapping in maps.items():
        check_labels(raw[col], mapping, f"Wave 1 {col}")
    for col, order, synonyms in [
        ("Demographics 1", cleaner.GENDER_ORDER, cleaner.GENDER_SYN),
        ("Demographics 3", cleaner.ETH_ORDER, cleaner.ETH_SYN),
    ]:
        check_labels(raw[col], {**{k: 1 for k in order}, **synonyms}, f"Wave 1 {col}")
    for col in ["Progress", "Duration (in seconds)", "Q19", "Demographics 2"]:
        numeric(raw[col], f"Wave 1 {col}")


def score_likert(series: pd.Series, name: str) -> pd.Series:
    result = pd.Series(np.nan, index=series.index, dtype=float)
    for index, value in series.items():
        text = str(value).strip().lower()
        if not text:
            continue
        if text in LIKERT:
            result.at[index] = LIKERT[text]
            continue
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"{name}: unknown response {value!r}") from exc
        if not math.isfinite(number) or number != int(number) or not 1 <= number <= 7:
            raise ValueError(f"{name}: expected integer 1 to 7, got {value!r}")
        result.at[index] = number
    return result


def wave2_frame(raw: pd.DataFrame, pids: pd.Series) -> pd.DataFrame:
    required = ["Q24", "Duration (in seconds)", "Q28", *[f"OI_{i}" for i in range(1, 7)],
                *[f"turnover_{i}" for i in range(1, 4)]]
    absent = [c for c in required if c not in raw]
    if absent:
        raise ValueError(f"Wave 2 lacks required columns: {absent}")
    out = pd.DataFrame({"participant_id": pids,
                        "w2_response_id": raw.ResponseId.map(cleaner._text),
                        "raw_w2_record": np.arange(1, len(raw) + 1),
                        "w2_pid_available": pids.notna().astype(np.int8)})
    out["w2_firm_name"] = raw.Q24.map(cleaner._text)
    out["wave2_duration_sec"] = numeric(raw["Duration (in seconds)"], "Wave 2 duration")
    check_labels(raw.Q28, {"Yes": 1, "No": 0, "1": 1, "2": 0}, "Wave 2 Q28")
    out["w2_employee_check"] = raw.Q28.map(lambda x: {
        "yes": 1, "1": 1, "no": 0, "2": 0}.get(str(x).strip().lower(), np.nan))
    for i in range(1, 7):
        out[f"w2_oi_{i}"] = score_likert(raw[f"OI_{i}"], f"Wave 2 OI item {i}")
    out["w2_oi_n"] = out[HUMAN_ITEMS].notna().sum(axis=1)
    out["w2_oi_mean"] = out[HUMAN_ITEMS].mean(axis=1)
    for i in range(1, 4):
        out[f"w2_turnover_{i}"] = score_likert(raw[f"turnover_{i}"], f"Wave 2 turnover item {i}")
    out["w2_turnover_n"] = out[TURNOVER_ITEMS].notna().sum(axis=1)
    out["w2_turnover_mean"] = out[TURNOVER_ITEMS].mean(axis=1)
    for source, destination in [("Finished", "w2_finished"), ("Q23", "w2_current_yes")]:
        if source in raw:
            check_labels(raw[source], {"True": 1, "False": 0, "Yes": 1, "No": 0}, f"Wave 2 {source}")
            out[destination] = raw[source].map({"True": 1, "False": 0, "Yes": 1, "No": 0}).astype(float)
    if "Progress" in raw:
        out["w2_progress"] = numeric(raw.Progress, "Wave 2 progress")
    return out


def write_dta(frame: pd.DataFrame, path: Path, labels: dict, value_labels: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp.dta")
    ready = frame.copy()
    for col in ready.select_dtypes(include=["object", "string"]).columns:
        ready[col] = ready[col].where(ready[col].notna(), "").astype(str)
    ready.to_stata(temporary, write_index=False, version=118,
                   variable_labels={c: str(v)[:80] for c, v in labels.items() if c in ready},
                   value_labels={c: v for c, v in value_labels.items() if c in ready},
                   data_label="OI and LLM: ALL response records; Stata selects analysis sample")
    os.replace(temporary, path)


def prepare(project: Path = PROJECT) -> dict:
    raw_dir = project / "0. Raw"
    sources = {name: raw_dir / name for name in BUNDLED_HASHES}
    for path in sources.values():
        if not path.is_file():
            raise FileNotFoundError(f"Original survey export missing: {path}")
    source_hashes = {name: sha256(path) for name, path in sources.items()}
    raw1, meta1 = read_export(sources["Glassdoor.csv"])
    raw2, meta2 = read_export(sources["Glassdoor W2.csv"])
    validate_wave1(raw1)
    pids1 = validate_ids(raw1, "Wave 1")
    pids2 = validate_ids(raw2, "Wave 2")

    # Call the unchanged original coding functions in their ALL-RECORD mode.
    # A temporary metadata-free copy avoids physical-line handling of multiline
    # question labels. Neither original CSV is ever edited or overwritten.
    with tempfile.TemporaryDirectory(prefix="oi_all_responses_") as temp:
        normalized = Path(temp) / "wave1.csv"
        raw1.to_csv(normalized, index=False)
        old_skip = cleaner.SKIP_METADATA_ROWS
        cleaner.SKIP_METADATA_ROWS = []
        try:
            w1, dynamic_labels, cleaner_audit = cleaner.build(
                str(normalized), None, min_review_words=25,
                speed_threshold=180, keep_ineligible=True, require_pid=False)
        finally:
            cleaner.SKIP_METADATA_ROWS = old_skip
    if len(w1) != len(raw1) or not w1.response_id.equals(raw1.ResponseId):
        raise RuntimeError("Wave 1 coding changed its response records or order; stopped.")
    w1["raw_w1_record"] = np.arange(1, len(raw1) + 1)
    for source, target in [("Q25", "review_pros"), ("Q13", "review_cons"), ("Q12", "review_advice")]:
        # Same record order verified above; works even when a participant ID is absent.
        w1[target] = raw1[source].map(cleaner._text)
    mandatory_columns = [cleaner.COLS[k] for k in ["pros", "cons", "seniority", "tenure",
                          "firm_size", "age", "gender", "ethnicity"]]
    mandatory_columns += [*cleaner.RATING_COLS, *cleaner.BINARY_COLS, cleaner.COLS["overall_rating"]]
    missing_answers = raw1[mandatory_columns].apply(lambda column: column.map(cleaner._is_blank))
    w1["mandatory_answers_present"] = (~missing_answers).all(axis=1).astype(np.int8)
    w2 = wave2_frame(raw2, pids2)

    # Unique sentinel keys ensure a missing PID never matches another missing PID.
    w1["_wave_join"] = pids1.where(pids1.notna(), "missing_w1_" + w1.response_id)
    w2["_wave_join"] = pids2.where(pids2.notna(), "missing_w2_" + w2.w2_response_id)
    w2["paired_wave1"] = (pids2.notna() & pids2.isin(pids1.dropna())).astype(np.int8)
    all_rows = w1.merge(w2.drop(columns=["participant_id", "paired_wave1"]),
                        on="_wave_join", how="left", validate="one_to_one", indicator=True, sort=False)
    all_rows["paired_wave2"] = all_rows["_merge"].eq("both").astype(np.int8)
    all_rows.drop(columns=["_wave_join", "_merge"], inplace=True)
    w2.drop(columns="_wave_join", inplace=True)
    if len(all_rows) != len(raw1) or list(all_rows.response_id) != list(raw1.ResponseId):
        raise RuntimeError("Wave linkage changed Wave 1 response records or order; stopped.")

    decisions = [cleaner._firm_dummies(a, b) if paired else (np.nan, np.nan, "no_wave2")
                 for a, b, paired in zip(all_rows.firm_name, all_rows.w2_firm_name, all_rows.paired_wave2)]
    firm = pd.DataFrame(decisions, columns=["firm_match_strict", "firm_match_lenient", "firm_match_status"])
    for col in firm:
        all_rows[col] = firm[col]
    all_rows["firm_match_unclear"] = np.where(all_rows.paired_wave2.eq(1),
        all_rows.firm_match_status.isin(["uncertain", "invalid_name", "missing_names"]).astype(float), np.nan)
    all_rows["oi"] = all_rows.w2_oi_mean
    all_rows["turnover"] = all_rows.w2_turnover_mean
    all_rows["nis_count"] = all_rows.fpp_n - all_rows.tpp_n
    all_rows["no_nis_pronouns"] = (all_rows.fpp_n.eq(0) & all_rows.tpp_n.eq(0)).astype(np.int8)
    all_rows["bundled_sources"] = np.int8(source_hashes == BUNDLED_HASHES)

    labels = {**cleaner.VAR_LABELS,
        **{f"w2_oi_{i}": f"Wave 2 OI item {i} (1-7)" for i in range(1, 7)},
        **{f"w2_turnover_{i}": f"Wave 2 turnover intention item {i} (1-7)" for i in range(1, 4)},
        "raw_w1_record": "Wave 1 response record order, excluding metadata records",
        "raw_w2_record": "Wave 2 response record order, excluding metadata records",
        "w2_response_id": "Qualtrics Wave 2 ResponseId (internal linkage only)",
        "mandatory_answers_present": "All original required Wave 1 questions answered",
        "review_pros": "Wave 1 review Pros; outer whitespace trimmed",
        "review_cons": "Wave 1 review Cons; outer whitespace trimmed",
        "review_advice": "Wave 1 review Advice; outer whitespace trimmed",
        "paired_wave1": "Wave 2 response has matching valid Wave 1 participant ID",
        "paired_wave2": "Wave 1 response has matching valid Wave 2 participant ID",
        "w2_firm_name": "Entered employer name, Wave 2 Q24 (not embedded FirmName)",
        "wave2_duration_sec": "Wave 2 Qualtrics duration in seconds",
        "w2_oi_mean": "Human OI available-item mean; Stata requires all six items",
        "w2_turnover_mean": "Human turnover intentions: available-item mean (1-7)",
        "firm_match_strict": "Exact normalised employer-name match between waves",
        "firm_match_lenient": "Employer match: exact, format, legal suffix or one spelling edit",
        "firm_match_unclear": "Employer-name pair unresolved or unusable",
        "firm_match_status": "Raw-source employer-name comparison decision",
        "bundled_sources": "Both source CSV hashes match the bundled original exports",
        "oi": "Human OI available-item mean; Stata requires all six items",
        "turnover": "Human turnover intentions: available-item mean (1-7)",
        "nis_count": "Net pronoun count: first-person plural minus third-person plural",
        "no_nis_pronouns": "No first-person plural or third-person plural pronouns",
    }
    values = {**cleaner.VAL_LABELS_STATIC, **dynamic_labels}
    for col in ["mandatory_answers_present", "paired_wave1", "paired_wave2", "firm_match_strict",
                "firm_match_lenient", "firm_match_unclear", "no_nis_pronouns", "bundled_sources"]:
        values[col] = {0: "No", 1: "Yes"}
    for col in HUMAN_ITEMS + TURNOVER_ITEMS:
        values[col] = {code: label.capitalize() for label, code in LIKERT.items()}
    if any(c.lower().startswith(("llm_", "loo_", "z_")) for c in all_rows):
        raise RuntimeError("Unexpected inherited LLM scores or standardised variables.")
    write_dta(all_rows, project / "2. Data" / "Prolific_all_respondents.dta", labels, values)
    write_dta(w2, project / "2. Data" / "Prolific_all_wave2.dta", labels, values)
    after_hashes = {name: sha256(path) for name, path in sources.items()}
    if source_hashes != after_hashes:
        raise RuntimeError("Source export changed during preparation; investigate before proceeding.")
    audit = {
        "schema": "oi-and-llm-all-responses-1", "raw_files_unchanged": True,
        "sources": {name: {"sha256": source_hashes[name], "bytes": path.stat().st_size}
                    for name, path in sources.items()},
        "original_cleaner_sha256": sha256(ANALYSIS / "original_cleaner.py"),
        "metadata_records_in_raw": {"wave1": meta1, "wave2": meta2},
        "wave1_response_records_in_raw": len(raw1), "wave1_response_records_in_output": len(all_rows),
        "wave2_response_records_in_raw": len(raw2), "wave2_response_records_in_output": len(w2),
        "wave1_records_with_wave2": int(all_rows.paired_wave2.sum()),
        "wave2_records_without_wave1": int(w2.paired_wave1.eq(0).sum()),
        "wave1_missing_pid": int(pids1.isna().sum()), "wave2_missing_pid": int(pids2.isna().sum()),
        "python_response_exclusions": 0, "cleaner_diagnostic_counts": cleaner_audit,
        "mandatory_wave1_columns": mandatory_columns,
        "sample_selection": "master.do sample applies all analysis restrictions and reports attrition.",
        "notes": ["No Prolific approval-list restriction.",
                  "Speed flag below 180 seconds is diagnostic only; Stata uses 150 <= duration < 900.",
                  "No Wave 2 employee, completion, progress or duration filter is newly imposed.",
                  "Both firm-match indicators are rebuilt from raw employer names and retained.",
                  "All Wave 2 records, including orphans or missing IDs, remain in Prolific_all_wave2.dta."],
    }
    audit_dir = project / "3. Output" / "Appendix"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "Preparation_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(f"Saved ALL {len(all_rows):,} Wave 1 records and ALL {len(w2):,} Wave 2 records.")
    print("Excluded respondents in Python: 0. Both raw CSV files are unchanged.")
    print("Next: run master.do sample in Stata (setup and exclusions together).")
    return audit


if __name__ == "__main__":
    prepare()
