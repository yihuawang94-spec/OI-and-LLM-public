# -*- coding: utf-8 -*-
"""06. Download Compustat annual accounts and add ROA to the fresh Revelio panel.

SPYDER: run 05_download_revelio.py, then run this file directly. It reads
2. Data/analysis_nis_turnover.dta automatically and writes
2. Data/analysis_nis_roa.dta. No existing project or imported DTA is consulted.
WRDS uses its normal login / .pgpass and requires Revelio company_mapping and
at least one licensed Compustat annual-fundamentals product.

Every original firm-year and variable remains unchanged. Firms without valid
accounts keep missing ROA. There are no review-count/headcount cutoffs,
industry exclusions, parent-company substitutions or automatic winsorisation.
Stata applies its explicit analysis transformations later.

UNCHANGED DEFINITIONS
  roa = net income / year-end total assets.
  roa_ib = income before extraordinary items / year-end total assets.
  roa_avg = net income / average current and comparable prior-year assets.
  *_lead1 = exact next calendar-end-year account, with gaps kept missing.

Accounts match the calendar year of datadate, not the fyear label. A fiscal
period ending next year can overlap the review year for non-December ends.
Average assets requires consecutive fiscal years, identical currency, a
300-430-day interval and positive assets in both periods. There is no fallback
to a different denominator. Losses and extreme ratios remain in this DTA.

Only direct RCID-GVKEY mappings from Revelio company_mapping are used.
Ambiguous/invalid mappings remain unmatched. This is a current vendor mapping,
not an ownership-history crosswalk. Several RCIDs may share a GVKEY; their
count is retained. No fuzzy employer-name/ticker matching is performed.

The script tries licensed North America and Global Compustat products, using
consolidated industrial-format statements. North America takes precedence
for duplicate company/date records. Within a calendar year, the latest fiscal
end is retained. Conflicting same-source/date records have missing financial
values and appear in the audit. Standard Compustat can contain restatements.

FILES
  0. Raw/Compustat: checksummed raw SQL chunks, mappings, annual accounts.
  2. Data/analysis_nis_roa.dta: full original panel with ROA variables.
  4. Output Appendix/Compustat: link/record audits, dictionary, coverage, manifest.
  4. Output Appendix/Logs/06_compustat.log: query and connection diagnostics.

Only caches within this package can resume. The 24-hour cache expiry reflects
changes to vendor links and accounts. --refresh forces fresh queries;
--self-test runs offline checks without requesting WRDS credentials.

Sources:
https://www.data-dictionary.reveliolabs.com/data.html#company-reference
https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/revelio-labs/
"""

# %% SETTINGS -- paths follow this package
from pathlib import Path

from project_settings import load_settings
PROJECT_DIR = Path(__file__).resolve().parent.parent
SETTINGS = load_settings(PROJECT_DIR)
DATA_DIR = PROJECT_DIR / "2. Data"
if SETTINGS["wrds"]["workforce_scope"] == "GLOBAL": DATA_DIR = DATA_DIR / "global"
INPUT_DTA = DATA_DIR / "analysis_nis_turnover.dta"
OUTPUT_DIR = DATA_DIR
REPORTS_DIR = PROJECT_DIR / "3. Output" / "Appendix" / "Compustat"
LOGS_DIR = PROJECT_DIR / "3. Output" / "Logs"
CACHE_DIR = PROJECT_DIR / "0. Raw" / "Compustat"
WRDS_USERNAME = SETTINGS["wrds"]["username"] or None          # None = WRDS's normal login / .pgpass
INCLUDE_GLOBAL = SETTINGS["wrds"]["include_global_compustat"]         # Try licensed Global accounts as well as North America
MAPPING_SCHEMA = None        # None = detect revelio_common or revelio
MAPPING_TABLE = "company_mapping"
QUERY_BATCH_SIZE = 5000
QUERY_TIMEOUT_MINUTES = 15
QUERY_ATTEMPTS = 3
CACHE_MAX_AGE_HOURS = 24

# %% Imports
import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

VERSION = "oi_llm_fresh_compustat_2.0.0"



"""Local, deterministic helpers for conservative Compustat ROA matching.

These functions perform no network requests and never modify their inputs.
"""

import ast
import re

import numpy as np
import pandas as pd


_FINANCIAL_FIELDS = ["fyear", "at", "ni", "ib", "sale", "lt"]
_VALUE_FIELDS = _FINANCIAL_FIELDS + ["curcd"]


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict, np.ndarray)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def normalize_gvkey(value):
    """Return a six-digit GVKEY or an empty string for an invalid value."""
    if _is_missing(value) or isinstance(value, (bool, np.bool_)):
        return ""
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value) or value != int(value):
            return ""
        value = str(int(value))
    elif isinstance(value, (int, np.integer)):
        value = str(int(value))
    else:
        value = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,6}(?:\.0+)?", value):
        return ""
    digits = value.split(".", 1)[0]
    return digits.zfill(6) if int(digits) > 0 else ""


def _rcid_key(value):
    if _is_missing(value):
        return ""
    if isinstance(value, (bool, np.bool_)):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"[0-9]+(?:\.0+)?", text):
        return str(int(text.split(".", 1)[0]))
    return text


def _gvkey_tokens(value, depth=0):
    """Expand SQL arrays and common serialized list formats conservatively."""
    if _is_missing(value):
        return []
    if depth > 5:
        return ["<invalid nested GVKEY list>"]
    if isinstance(value, dict):
        return [] if not value else ["<invalid GVKEY dictionary>"]
    if isinstance(value, (list, tuple, set, np.ndarray)):
        result = []
        for entry in value:
            result.extend(_gvkey_tokens(entry, depth + 1))
        return result
    if not isinstance(value, str):
        return [value]
    text = value.strip()
    if text.lower() in {"", "nan", "none", "null", "<na>", "nat"}:
        return []
    if normalize_gvkey(text):
        return [text]
    if text[:1] in {"[", "{", "(", "\"", "'"}:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if parsed is not None and not (isinstance(parsed, str) and parsed == text):
            return _gvkey_tokens(parsed, depth + 1)
    if text[:1] in {"[", "{", "("} and text[-1:] in {"]", "}", ")"}:
        text = text[1:-1].strip()
        if not text:
            return []
    if re.search(r"[,;|]", text):
        tokens = []
        for entry in re.split(r"[,;|]", text):
            entry = entry.strip().strip("\"'").strip()
            if entry:
                tokens.extend(_gvkey_tokens(entry, depth + 1))
        return tokens
    return [text.strip("\"'")]


def resolve_links(raw, input_rcids):
    """Resolve only unique, valid direct RCID-to-GVKEY snapshot links.

    Multiple distinct GVKEYs remain unmatched rather than selecting one. An
    invalid nonempty token also prevents a unique-looking link being accepted.
    Repeated identical links are harmless. Input RCID values retain their type.
    """
    if not {"rcid", "gvkey"}.issubset(raw.columns):
        raise ValueError("The direct link table must contain rcid and gvkey.")
    supplied = pd.Series(list(input_rcids), name="rcid").drop_duplicates()
    rcid_keys = supplied.map(_rcid_key)
    if rcid_keys.eq("").any():
        raise ValueError("Input RCIDs must be nonmissing identifiers.")
    if rcid_keys.duplicated().any():
        raise ValueError("Input RCIDs contain different representations of the same identifier.")
    candidates = {}
    for rcid, value in raw[["rcid", "gvkey"]].itertuples(index=False, name=None):
        key = _rcid_key(rcid)
        if key:
            candidates.setdefault(key, []).extend(_gvkey_tokens(value))
    rows = []
    for rcid, key in zip(supplied, rcid_keys):
        tokens = candidates.get(key, [])
        valid = {normalize_gvkey(token) for token in tokens}
        invalid = "" in valid
        valid.discard("")
        if len(valid) > 1:
            status = "ambiguous"
        elif invalid:
            status = "invalid"
        elif valid:
            status = "direct"
        else:
            status = "unmatched"
        rows.append({
            "rcid": rcid,
            "roa_gvkey": next(iter(valid)) if status == "direct" else "",
            "roa_link_status": status,
            "roa_n_gvkeys": len(valid),
            "roa_link_source": "Revelio direct snapshot",
        })
    return pd.DataFrame(rows, columns=[
        "rcid", "roa_gvkey", "roa_link_status", "roa_n_gvkeys", "roa_link_source"
    ])


def _account_columns():
    return [
        "gvkey", "cs_year", "roa", "roa_ib", "roa_avg", "cs_at", "cs_ni",
        "cs_ib", "cs_sale", "cs_lt", "cs_fyear", "cs_datadate", "cs_curcd",
        "cs_source", "cs_at_lag", "cs_avg_assets", "cs_n_reports_year",
        "cs_duplicate_conflict", "cs_source_disagreement",
    ]


def compute_accounts(raw):
    """Return annual accounts and a row-level resolution audit.

    Calendar year refers to the fiscal reporting date's calendar year. If a
    firm reports twice in that year, retain the later reporting date. Source
    priority is numerical (North America should be 0; Global should be 1).
    Conflicting records within the chosen source produce missing financial
    values. Cross-source differences are audited; priority decides the source.
    """
    required = {
        "gvkey", "datadate", "fyear", "curcd", "at", "ni", "sale", "lt",
        "cs_source", "cs_priority",
    }
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError("Missing standardized accounting columns: " + ", ".join(sorted(missing)))
    frame = raw.copy(deep=True).reset_index(drop=True)
    frame["audit_input_row"] = np.arange(len(frame), dtype=np.int64)
    if "ib" not in frame:
        frame["ib"] = np.nan
    frame["audit_raw_gvkey"] = frame["gvkey"].map(lambda x: "" if _is_missing(x) else str(x))
    frame["audit_raw_datadate"] = frame["datadate"].map(lambda x: "" if _is_missing(x) else str(x))
    frame["gvkey"] = frame["gvkey"].map(normalize_gvkey)
    frame["datadate"] = pd.to_datetime(frame["datadate"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    for field in _FINANCIAL_FIELDS:
        frame[field] = pd.to_numeric(frame[field], errors="coerce").replace([np.inf, -np.inf], np.nan)
    frame["curcd"] = frame["curcd"].map(lambda x: "" if _is_missing(x) else str(x).strip().upper())
    frame.loc[frame["curcd"].str.lower().isin(["nan", "none", "null", "<na>"]), "curcd"] = ""
    frame["cs_source"] = frame["cs_source"].map(lambda x: "" if _is_missing(x) else str(x).strip())
    priority = pd.to_numeric(frame["cs_priority"], errors="coerce")
    if priority.isna().any() or not np.isfinite(priority).all() or priority.mod(1).ne(0).any():
        raise ValueError("Every accounting source needs a finite integer cs_priority.")
    if frame["cs_source"].eq("").any():
        raise ValueError("Every accounting record needs a nonempty cs_source.")
    frame["cs_priority"] = priority.astype(np.int64)
    frame["audit_valid"] = frame["gvkey"].ne("") & frame["datadate"].notna()
    frame["audit_reason"] = ""
    frame.loc[frame["gvkey"].eq(""), "audit_reason"] = "invalid_gvkey"
    frame.loc[frame["datadate"].isna(), "audit_reason"] = "invalid_datadate"
    valid = frame.loc[frame["audit_valid"]].copy()
    audit_flags = [
        "audit_source_duplicate_conflict", "audit_cross_source_disagreement",
        "audit_same_priority_conflict", "audit_selected_source", "audit_selected_report_year",
    ]
    if valid.empty:
        for flag in audit_flags:
            frame[flag] = False
        return pd.DataFrame(columns=_account_columns()), frame

    source_keys = ["gvkey", "datadate", "cs_source"]
    date_keys = ["gvkey", "datadate"]
    source_group = valid.groupby(source_keys, dropna=False, sort=False)
    conflicts = source_group[_VALUE_FIELDS].nunique(dropna=False).gt(1).any(axis=1)
    source_stats = source_group.agg(
        audit_n_source_records=("audit_input_row", "size"),
        chosen_priority=("cs_priority", "min"),
    ).reset_index()
    source_stats["audit_source_duplicate_conflict"] = conflicts.to_numpy()
    collapsed = valid.drop_duplicates(source_keys, keep="first").merge(source_stats, on=source_keys, validate="1:1")
    collapsed["cs_priority"] = collapsed.pop("chosen_priority")
    bad = collapsed["audit_source_duplicate_conflict"]
    collapsed.loc[bad, _FINANCIAL_FIELDS] = np.nan
    collapsed.loc[bad, "curcd"] = ""

    # A shared source priority cannot silently resolve a genuine disagreement.
    minimum_priority = collapsed.groupby(date_keys, sort=False)["cs_priority"].transform("min")
    preferred = collapsed.loc[collapsed["cs_priority"].eq(minimum_priority)].copy()
    same_priority_conflicts = preferred.groupby(date_keys, sort=False)[_VALUE_FIELDS].nunique(dropna=False).gt(1).any(axis=1)
    source_disagreements = collapsed.groupby(date_keys, sort=False)[_VALUE_FIELDS].nunique(dropna=False).gt(1).any(axis=1)
    resolved = preferred.sort_values(date_keys + ["cs_priority", "cs_source"], kind="stable").drop_duplicates(date_keys).copy()
    resolved = resolved.merge(
        same_priority_conflicts.rename("audit_same_priority_conflict").reset_index(), on=date_keys, validate="1:1"
    ).merge(
        source_disagreements.rename("audit_cross_source_disagreement").reset_index(), on=date_keys, validate="1:1"
    )
    bad = resolved["audit_same_priority_conflict"]
    resolved.loc[bad, _FINANCIAL_FIELDS] = np.nan
    resolved.loc[bad, "curcd"] = ""
    resolved["cs_duplicate_conflict"] = (
        resolved["audit_source_duplicate_conflict"] | resolved["audit_same_priority_conflict"]
    ).astype(np.int8)
    resolved["cs_source_disagreement"] = resolved["audit_cross_source_disagreement"].astype(np.int8)
    resolved = resolved.sort_values(date_keys, kind="stable").reset_index(drop=True)
    previous = resolved.groupby("gvkey", sort=False)[["at", "fyear", "curcd", "datadate"]].shift(1)
    gap = (resolved["datadate"] - previous["datadate"]).dt.days
    comparable = (
        resolved["fyear"].sub(previous["fyear"]).eq(1)
        & resolved["curcd"].ne("")
        & resolved["curcd"].eq(previous["curcd"])
        & gap.between(300, 430)
        & resolved["at"].gt(0)
        & previous["at"].gt(0)
    )
    resolved["cs_at_lag"] = previous["at"].where(comparable)
    resolved["cs_avg_assets"] = ((resolved["at"] + previous["at"]) / 2).where(comparable)
    resolved["roa"] = resolved["ni"].div(resolved["at"].where(resolved["at"].gt(0)))
    resolved["roa_ib"] = resolved["ib"].div(resolved["at"].where(resolved["at"].gt(0)))
    resolved["roa_avg"] = resolved["ni"].div(resolved["cs_avg_assets"])
    resolved["cs_year"] = resolved["datadate"].dt.year.astype(np.int64)
    resolved["cs_n_reports_year"] = resolved.groupby(["gvkey", "cs_year"], sort=False)["datadate"].transform("size").astype(np.int32)
    selected = resolved.sort_values(date_keys, kind="stable").drop_duplicates(["gvkey", "cs_year"], keep="last").copy()

    # Keep every raw row in the audit, including unused sources and invalid IDs.
    source_audit = source_stats[source_keys + ["audit_n_source_records", "audit_source_duplicate_conflict"]]
    date_audit = resolved[date_keys + ["audit_cross_source_disagreement", "audit_same_priority_conflict", "cs_source"]].rename(columns={"cs_source": "audit_chosen_source"})
    year_audit = selected[date_keys].assign(audit_is_latest_date=True)
    audit = frame.merge(source_audit, on=source_keys, how="left", validate="m:1").merge(
        date_audit, on=date_keys, how="left", validate="m:1"
    ).merge(year_audit, on=date_keys, how="left", validate="m:1")
    audit["audit_selected_source"] = audit["cs_source"].eq(audit["audit_chosen_source"])
    audit["audit_selected_report_year"] = audit["audit_selected_source"] & audit["audit_is_latest_date"].eq(True)
    for flag in audit_flags:
        audit[flag] = audit[flag].eq(True)
    audit = audit.sort_values("audit_input_row", kind="stable").reset_index(drop=True)

    rename = {field: "cs_" + field for field in _FINANCIAL_FIELDS + ["datadate", "curcd"]}
    accounts = selected.rename(columns=rename)[_account_columns()].sort_values(["gvkey", "cs_year"], kind="stable").reset_index(drop=True)
    if accounts.duplicated(["gvkey", "cs_year"]).any():
        raise AssertionError("Accounting resolution did not produce unique GVKEY/calendar-year records.")
    return accounts, audit


def attach_roa(panel, links, accounts):
    """Attach exact calendar-year and next-calendar-year accounting records.

    Preserve all original columns, rows, order, and index. A missing subsequent
    year never substitutes a later available year. Workforce coverage is unused.
    """
    if not {"rcid", "year"}.issubset(panel.columns):
        raise ValueError("The input panel must contain rcid and year.")
    if panel.columns.duplicated().any():
        raise ValueError("The input panel has duplicate column names.")
    required_links = {"rcid", "roa_gvkey", "roa_link_status", "roa_n_gvkeys", "roa_link_source"}
    if not required_links.issubset(links.columns):
        raise ValueError("The resolved link table is missing required columns.")
    if not {"gvkey", "cs_year"}.issubset(accounts.columns):
        raise ValueError("The accounting table must contain gvkey and cs_year.")
    value_columns = [c for c in accounts.columns if c not in {"gvkey", "cs_year"}]
    if any(not (c.startswith("cs_") or c in {"roa", "roa_ib", "roa_avg"}) for c in value_columns):
        raise ValueError("Unexpected unprefixed column in the accounting table.")
    link_columns = [c for c in links.columns if c != "rcid"]
    output_columns = link_columns + value_columns + [c + "_lead1" for c in value_columns] + [
        "roa_has_link", "roa_has_accounts", "roa_has_accounts_lead1"
    ]
    collisions = set(output_columns).intersection(panel.columns)
    if collisions:
        raise ValueError("Refusing to overwrite existing panel columns: " + ", ".join(sorted(collisions)))
    internal = {"_roa_row_order", "_roa_rcid_key", "_roa_year_key", "_roa_account_gvkey", "_roa_account_year", "_roa_matched"}
    if internal.intersection(panel.columns) or internal.intersection(links.columns) or internal.intersection(accounts.columns):
        raise ValueError("Input includes a reserved ROA helper column.")
    original = panel
    result = panel.copy(deep=False)
    result["_roa_row_order"] = np.arange(len(result), dtype=np.int64)
    result["_roa_rcid_key"] = result["rcid"].map(_rcid_key)
    if result["_roa_rcid_key"].eq("").any():
        raise ValueError("The firm-year panel contains missing RCIDs.")
    year = pd.to_numeric(result["year"], errors="coerce")
    invalid_year = result["year"].notna() & (year.isna() | ~np.isfinite(year) | year.mod(1).ne(0))
    if invalid_year.any():
        raise ValueError("The firm-year panel contains noninteger years.")
    result["_roa_year_key"] = year.astype("Int64")
    if result.duplicated(["_roa_rcid_key", "_roa_year_key"]).any():
        raise ValueError("The input panel is not unique by RCID and year.")
    linked = links.copy(deep=True)
    linked["_roa_rcid_key"] = linked["rcid"].map(_rcid_key)
    if linked["_roa_rcid_key"].eq("").any() or linked["_roa_rcid_key"].duplicated().any():
        raise ValueError("Resolved links are not unique by valid RCID.")
    result = result.merge(linked.drop(columns="rcid"), on="_roa_rcid_key", how="left", sort=False, validate="m:1")
    result["roa_gvkey"] = result["roa_gvkey"].fillna("")
    result["roa_link_status"] = result["roa_link_status"].fillna("unmatched")
    result["roa_n_gvkeys"] = result["roa_n_gvkeys"].fillna(0).astype(np.int32)
    result["roa_link_source"] = result["roa_link_source"].fillna("Revelio direct snapshot")
    result["roa_has_link"] = (result["roa_link_status"].eq("direct") & result["roa_gvkey"].ne("")).astype(np.int8)
    if (result["roa_has_link"].eq(0) & result["roa_gvkey"].ne("")).any():
        raise ValueError("An unresolved link has a nonempty GVKEY.")

    account_data = accounts.copy(deep=True)
    normalized = account_data["gvkey"].map(normalize_gvkey)
    if normalized.eq("").any():
        raise ValueError("Resolved accounts contain invalid GVKEYs.")
    account_data["gvkey"] = normalized
    account_year = pd.to_numeric(account_data["cs_year"], errors="coerce")
    if account_year.isna().any() or account_year.mod(1).ne(0).any():
        raise ValueError("Resolved accounts contain invalid calendar years.")
    account_data["cs_year"] = account_year.astype("Int64")
    if account_data.duplicated(["gvkey", "cs_year"]).any():
        raise ValueError("Resolved accounts are not unique by GVKEY and calendar year.")
    for lead in (0, 1):
        suffix = "_lead1" if lead else ""
        renamed = {c: c + suffix for c in value_columns}
        renamed.update({"gvkey": "_roa_account_gvkey", "cs_year": "_roa_account_year"})
        right = account_data.rename(columns=renamed).copy()
        right["_roa_account_year"] = right["_roa_account_year"] - lead
        right["_roa_matched"] = np.int8(1)
        result = result.merge(right, left_on=["roa_gvkey", "_roa_year_key"], right_on=["_roa_account_gvkey", "_roa_account_year"], how="left", sort=False, validate="m:1")
        result["roa_has_accounts" + suffix] = result["_roa_matched"].fillna(0).astype(np.int8)
        result = result.drop(columns=["_roa_account_gvkey", "_roa_account_year", "_roa_matched"])
    result = result.sort_values("_roa_row_order", kind="stable").drop(columns=["_roa_row_order", "_roa_rcid_key", "_roa_year_key"])
    result.index = original.index
    if len(result) != len(original) or not result[list(original.columns)].equals(original):
        raise AssertionError("The ROA merge changed original panel rows, order, or values.")
    return result


# %% WRDS and resumable extraction
def now_utc():
    return datetime.now(timezone.utc).isoformat()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, obj):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def sql_identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid SQL identifier: " + repr(value))
    return '"' + value + '"'


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def qualified(schema, table):
    return sql_identifier(schema) + "." + sql_identifier(table)


class WRDSDatabase:
    def __init__(self, username=None):
        self.username, self.conn = username, None

    def close(self):
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
        self.conn = None

    def connect(self):
        try:
            import wrds
        except ImportError as exc:
            raise RuntimeError("Install packages in Spyder's environment: pip install wrds pandas numpy") from exc
        self.close()
        options = {"wrds_username": self.username} if self.username else {}
        self.conn = wrds.Connection(**options)
        self.conn.raw_sql("SELECT set_config('statement_timeout', "
                          + sql_literal(int(QUERY_TIMEOUT_MINUTES * 60000)) + ", false)")
        logging.info("Connected to WRDS.")

    def query(self, sql, label):
        for attempt in range(1, QUERY_ATTEMPTS + 1):
            started, stop = time.monotonic(), threading.Event()

            def heartbeat():
                while not stop.wait(30):
                    logging.info("%s: still running after %.0f seconds.", label, time.monotonic() - started)

            worker = threading.Thread(target=heartbeat, daemon=True)
            worker.start()
            try:
                if self.conn is None:
                    self.connect()
                result = self.conn.raw_sql(sql)
                logging.info("%s: %s rows, %.1f seconds.", label, f"{len(result):,}", time.monotonic() - started)
                return result
            except Exception as exc:
                underlying = getattr(exc, "orig", exc)
                code = getattr(underlying, "pgcode", None) or getattr(underlying, "sqlstate", None)
                transient = (str(code).startswith("08") or code in {"57014", "40001", "40P01", "57P01"}
                             or type(underlying).__name__ in {"OperationalError", "InterfaceError"})
                if not transient or attempt == QUERY_ATTEMPTS:
                    raise
                self.close()
                stop.set()
                worker.join(timeout=1)
                logging.warning("%s failed transiently (%s); retrying in %ss.", label, code, 2 ** attempt)
                time.sleep(2 ** attempt)
            finally:
                stop.set()
                worker.join(timeout=1)


class SQLCache:
    def __init__(self, directory, database, refresh=False):
        self.directory, self.db, self.refresh = Path(directory), database, refresh
        self.directory.mkdir(parents=True, exist_ok=True)

    def fetch(self, sql, label):
        key = object_hash({"version": VERSION, "sql": sql})
        base = self.directory / (label + "_" + key[:16])
        data_path, metadata_path = base.with_suffix(".csv.gz"), base.with_suffix(".json")
        if data_path.exists() and metadata_path.exists() and not self.refresh:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            age = time.time() - meta.get("saved_epoch", 0)
            if meta.get("key") == key and 0 <= age <= CACHE_MAX_AGE_HOURS * 3600:
                if meta.get("sha256") != file_hash(data_path):
                    raise RuntimeError("Checkpoint checksum failed: " + str(data_path) + "; rerun with --refresh.")
                cached = pd.read_csv(data_path, dtype={"gvkey": "str"}, low_memory=False)
                if len(cached) != meta["rows"]:
                    raise RuntimeError("Checkpoint row count failed: " + str(data_path))
                logging.info("Reused %s: %s rows.", label, f"{len(cached):,}")
                return cached
        base.with_suffix(".sql").write_text(sql, encoding="utf-8")
        result = self.db.query(sql, label)
        temporary = data_path.with_name(data_path.name + ".tmp")
        result.to_csv(temporary, index=False, compression="gzip")
        os.replace(temporary, data_path)
        atomic_json(metadata_path, {"key": key, "rows": len(result), "sha256": file_hash(data_path),
                                    "saved_epoch": time.time(), "saved_at": now_utc()})
        return result


def inspect_columns(db, schema, table):
    result = db.query("SELECT column_name FROM information_schema.columns WHERE table_schema="
                      + sql_literal(schema) + " AND table_name=" + sql_literal(table),
                      "Inspect " + schema + "." + table)
    return set(result["column_name"].astype(str).str.lower())


def resolve_mapping_table(db):
    schemas = [MAPPING_SCHEMA] if MAPPING_SCHEMA else ["revelio_common", "revelio"]
    checked = []
    for schema in schemas:
        cols = inspect_columns(db, schema, MAPPING_TABLE)
        checked.append({"schema": schema, "table": MAPPING_TABLE, "columns": sorted(cols)})
        if {"rcid", "gvkey"}.issubset(cols):
            db.query("SELECT rcid, gvkey FROM " + qualified(schema, MAPPING_TABLE) + " LIMIT 0", "Check mapping access")
            return schema, MAPPING_TABLE, cols
    raise RuntimeError("No accessible Revelio company_mapping table with rcid AND gvkey. "
                       "Check your Revelio subscription or set MAPPING_SCHEMA/MAPPING_TABLE. Inspected: " + str(checked))


def resolve_account_sources(db, include_global=True):
    candidates = {
        "NA": [("comp", "funda"), ("comp_na_daily_all", "funda"), ("compa", "funda")],
        "GLOBAL": [("comp", "g_funda"), ("comp_global_daily", "g_funda"),
                   ("comp_global", "g_funda"), ("comp_global", "funda")],
    }
    sources, unavailable = [], []
    required = {"gvkey", "datadate", "fyear", "at", "ni", "curcd", "indfmt", "datafmt", "popsrc", "consol"}
    for region, pairs in candidates.items():
        if region == "GLOBAL" and not include_global:
            continue
        for schema, table in pairs:
            cols = inspect_columns(db, schema, table)
            if not required.issubset(cols):
                continue
            try:
                db.query("SELECT gvkey, datadate, at, ni FROM " + qualified(schema, table) + " LIMIT 0",
                         "Check " + region + " account access")
            except Exception as exc:
                logging.warning("Cannot read %s.%s: %s", schema, table, exc)
                continue
            sources.append({"region": region, "schema": schema, "table": table, "columns": cols,
                            "priority": 0 if region == "NA" else 1})
            break
        else:
            unavailable.append(region)
    if not sources:
        raise RuntimeError("No accessible Compustat annual fundamentals table. "
                           "Your WRDS account needs Compustat North America or Global, including ni and at.")
    for region in unavailable:
        logging.warning("%s Compustat was not accessible; using the available product(s).", region)
    return sources, unavailable


def account_filters(db, source, first_year, last_year):
    table = qualified(source["schema"], source["table"])
    bounds = f"datadate >= '{first_year}-01-01' AND datadate < '{last_year + 1}-01-01'"
    codes = db.query("SELECT DISTINCT datafmt, popsrc FROM " + table + " WHERE " + bounds
                     + " AND indfmt='INDL' AND consol='C'", "Inspect " + source["region"] + " statement formats")
    normalized = {(str(a).strip().upper(), str(b).strip().upper()) for a, b in codes.itertuples(index=False, name=None)}
    preferences = [("STD", "D")] if source["region"] == "NA" else [
        ("HIST_STD", "I"), ("STD", "I"), ("HIST_STD", "D"), ("STD", "D")]
    selected = next((pair for pair in preferences if pair in normalized), None)
    if selected is None:
        raise RuntimeError("No supported consolidated industrial statement format in " + table
                           + ". Observed datafmt/popsrc: " + repr(sorted(normalized)))
    source["filters"] = {"indfmt": "INDL", "consol": "C", "datafmt": selected[0], "popsrc": selected[1]}
    logging.info("%s formats: %s", source["region"], source["filters"])
    return bounds + " AND " + " AND ".join(key + "=" + sql_literal(val) for key, val in source["filters"].items())


def pull_mapping(cache, ids, location):
    schema, table, cols = location
    fields = ["rcid", "gvkey"] + (["company"] if "company" in cols else [])
    frames = []
    for start in range(0, len(ids), QUERY_BATCH_SIZE):
        batch = ids[start:start + QUERY_BATCH_SIZE]
        sql = "SELECT " + ", ".join(fields) + " FROM " + qualified(schema, table)
        sql += " WHERE rcid IN (" + ",".join(str(int(v)) for v in batch) + ")"
        frames.append(cache.fetch(sql, f"mapping_batch_{start // QUERY_BATCH_SIZE + 1:04d}"))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=fields)


def pull_accounts(cache, db, gvkeys, sources, first_year, last_year):
    frames = []
    for source in sources:
        filters = account_filters(db, source, first_year, last_year)
        fields = ["gvkey", "datadate", "fyear", "curcd", "at", "ni"]
        fields += [f for f in ["ib", "sale", "lt", "fyr"] if f in source["columns"]]
        for start in range(0, len(gvkeys), QUERY_BATCH_SIZE):
            batch = gvkeys[start:start + QUERY_BATCH_SIZE]
            sql = "SELECT " + ", ".join(sql_identifier(f) for f in fields)
            sql += " FROM " + qualified(source["schema"], source["table"]) + " WHERE " + filters
            sql += " AND gvkey IN (" + ",".join(sql_literal(v) for v in batch) + ")"
            frame = cache.fetch(sql, source["region"].lower() + f"_accounts_{start // QUERY_BATCH_SIZE + 1:04d}")
            for optional in ["ib", "sale", "lt"]:
                if optional not in frame:
                    frame[optional] = np.nan
            frame["cs_source"] = source["schema"] + "." + source["table"]
            frame["cs_priority"] = source["priority"]
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["gvkey", "datadate", "fyear", "curcd", "at", "ni", "cs_source", "cs_priority"])
    return pd.concat(frames, ignore_index=True)


# %% Stata input/output and audit
def choose_input(configured=None):
    path = Path(INPUT_DTA if configured is None else configured).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("Expected freshly downloaded panel at " + str(path) +
                                ". Run 05_download_revelio.py in this package first.")
    if path.suffix.lower() != ".dta":
        raise ValueError("The Revelio panel must be a Stata .dta file.")
    return path


def read_panel(path):
    with pd.io.stata.StataReader(path, convert_categoricals=False, preserve_dtypes=True) as reader:
        frame = reader.read()
        labels = reader.variable_labels()
        label_sets = reader.value_labels()
        varlist = getattr(reader, "_varlist", getattr(reader, "varlist", list(frame.columns)))
        lbllist = getattr(reader, "_lbllist", getattr(reader, "lbllist", [""] * len(varlist)))
        fmtlist = getattr(reader, "_fmtlist", getattr(reader, "fmtlist", [""] * len(varlist)))
        value_labels = {v: label_sets[label] for v, label in zip(varlist, lbllist) if label in label_sets}
        date_formats = {v: str(fmt)[1:3] for v, fmt in zip(varlist, fmtlist)
                        if str(fmt).startswith("%t") and pd.api.types.is_datetime64_any_dtype(frame[v])}
    if not {"rcid", "year"}.issubset(frame.columns):
        raise ValueError("Input DTA must contain rcid and year, one row per firm-year.")
    for col in ["rcid", "year"]:
        numeric = pd.to_numeric(frame[col], errors="coerce")
        if numeric.isna().any() or not np.isfinite(numeric).all() or (numeric % 1 != 0).any():
            raise ValueError(col + " must contain nonmissing integer identifiers.")
        if not pd.api.types.is_numeric_dtype(frame[col]):
            raise ValueError(col + " must be numeric in the input DTA. Convert string IDs in Stata first.")
    if frame.duplicated(["rcid", "year"]).any():
        raise ValueError("Input has duplicate rcid/year keys. Use the firm-year panel, not individual reviews.")
    if frame.empty or not frame.year.between(1900, 2200).all():
        raise ValueError("Input is empty or has invalid calendar years.")
    return frame, labels, value_labels, date_formats


def variable_definition(col):
    fixed = {
        "roa": "Net income / year-end total assets; fraction, .05=5%.",
        "roa_ib": "Income before extraordinary items / year-end assets; fraction.",
        "roa_avg": "Net income / mean current/prior annual assets; comparable years only.",
        "roa_gvkey": "Direct same-entity Revelio GVKEY; vendor snapshot, not historical ownership.",
        "roa_link_status": "Direct, unmatched, ambiguous, or invalid RCID-GVKEY link.",
        "roa_has_link": "1=unambiguous direct vendor link to Compustat GVKEY.",
        "roa_has_accounts": "1=Compustat statement found ending in review calendar year.",
        "roa_n_gvkeys": "Distinct valid GVKEYs in vendor mapping for this RCID.",
        "roa_rcids_per_gvkey": "Number of input RCIDs sharing this GVKEY; consider dependence in inference.",
        "cs_at": "Compustat total assets, reporting-currency millions.",
        "cs_ni": "Compustat net income, reporting-currency millions.",
        "cs_ib": "Compustat income before extraordinary items, reporting-currency millions.",
        "cs_sale": "Compustat sales, reporting-currency millions.",
        "cs_lt": "Compustat total liabilities, reporting-currency millions.",
        "cs_fyear": "Compustat fiscal-year label; may differ from calendar year.",
        "cs_datadate": "Compustat fiscal statement end date; calendar year defines merge year.",
        "cs_curcd": "Compustat reporting currency; ratios use same-currency financial amounts.",
        "cs_source": "Compustat annual source table; NA preferred to Global for overlap.",
        "cs_at_lag": "Prior annual assets, only consecutive comparable fiscal periods.",
        "cs_avg_assets": "Average current/prior assets in comparable consecutive annual periods.",
        "cs_n_reports_year": "Distinct report end dates in calendar year; latest end retained.",
        "cs_duplicate_conflict": "1=conflicting same-source/date financial records; amounts left missing.",
    }
    if col.endswith("_lead1"):
        base = col[:-6]
        return "Next-calendar-end-year: " + fixed.get(base, base)
    return fixed.get(col, col.replace("_", " "))


def write_outputs(frame, original_columns, labels, value_labels, date_formats, output, links, accounts, audit,
                  reports_dir=None):
    output = Path(output)
    reports_dir = Path(reports_dir) if reports_dir is not None else output
    reports_dir.mkdir(parents=True, exist_ok=True)
    labels = dict(labels)
    additions = [c for c in frame.columns if c not in original_columns]
    for col in additions:
        labels[col] = variable_definition(col)[:80]
    export = frame.copy(deep=False)
    for col in additions:
        if pd.api.types.is_string_dtype(export[col]) or export[col].dtype == object:
            export[col] = export[col].fillna("").astype(str)
        elif pd.api.types.is_extension_array_dtype(export[col].dtype):
            export[col] = export[col].astype("float64")
    dates = dict(date_formats)
    dates.update({c: "td" for c in additions if pd.api.types.is_datetime64_any_dtype(export[c])})
    path = output / "analysis_nis_roa.dta"
    temporary = path.with_name(path.name + ".tmp")
    try:
        export.to_stata(temporary, write_index=False, version=118,
                        data_label="Revelio review panel plus Compustat annual ROA",
                        variable_labels=labels, value_labels=value_labels, convert_dates=dates)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    links.to_csv(reports_dir / "roa_link_audit.csv", index=False)
    audit.to_csv(reports_dir / "compustat_record_audit.csv.gz", index=False, compression="gzip")
    coverage = frame.groupby("year", as_index=False).agg(
        firm_years=("rcid", "size"), firms=("rcid", "nunique"),
        direct_links=("roa_has_link", "sum"), accounts=("roa_has_accounts", "sum"),
        accounts_lead1=("roa_has_accounts_lead1", "sum"),
        roa_observed=("roa", "count"), roa_lead1_observed=("roa_lead1", "count"))
    coverage.to_csv(reports_dir / "roa_coverage_by_year.csv", index=False)
    pd.DataFrame({"variable": additions, "definition": [variable_definition(c) for c in additions]}).to_csv(
        reports_dir / "roa_dictionary.csv", index=False)
    return path, coverage


def run_self_test():
    # Financial gaps, invalid denominators, ambiguous links and DTA labels.
    raw_links = pd.DataFrame({"rcid": [1, 2, 2, 3, 4], "gvkey": ["1234", "111111", "222222", "bad", "000000"]})
    links = resolve_links(raw_links, [1, 2, 3, 4, 5])
    assert links.loc[links.rcid.eq(1), "roa_gvkey"].iloc[0] == "001234"
    assert links.loc[links.rcid.eq(2), "roa_link_status"].iloc[0] == "ambiguous"
    assert links.loc[links.rcid.eq(3), "roa_gvkey"].iloc[0] == ""
    records = pd.DataFrame({"gvkey": ["001234"] * 4,
        "datadate": ["2019-12-31", "2020-12-31", "2022-12-31", "2023-12-31"],
        "fyear": [2019, 2020, 2022, 2023], "curcd": ["USD"] * 4,
        "at": [100., 120., 130., 0.], "ni": [10., -12., 13., 1.],
        "ib": [9., -11., 12., 1.], "sale": [40.] * 4, "lt": [10.] * 4,
        "cs_source": ["comp.funda"] * 4, "cs_priority": [0] * 4})
    accounts, audit = compute_accounts(records)
    year2020 = accounts.loc[accounts.cs_year.eq(2020)].iloc[0]
    assert np.isclose(year2020.roa, -.1)
    assert np.isclose(year2020.roa_avg, -12 / 110)
    assert accounts.loc[accounts.cs_year.eq(2022), "roa_avg"].isna().all()
    assert accounts.loc[accounts.cs_year.eq(2023), "roa"].isna().all()
    panel = pd.DataFrame({"rcid": [1, 1, 1, 5], "year": [2019, 2020, 2021, 2020],
                          "nis_1": [.1, -.1, 0., .2], "category": [1, 2, 1, 2]})
    merged = attach_roa(panel, links, accounts)
    pd.testing.assert_frame_equal(merged[panel.columns], panel)
    assert np.isclose(merged.loc[merged.year.eq(2019), "roa_lead1"].iloc[0], -.1)
    assert merged.loc[merged.year.eq(2020), "roa_lead1"].isna().all()
    assert np.isclose(merged.loc[merged.year.eq(2021), "roa_lead1"].iloc[0], .1)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.dta"
        panel.to_stata(path, write_index=False, variable_labels={"nis_1": "Original NIS"},
                       value_labels={"category": {1: "One", 2: "Two"}})
        original, labels, values, dates = read_panel(path)
        result = attach_roa(original, links, accounts)
        saved, _ = write_outputs(result, original.columns, labels, values, dates,
                                 Path(directory), links, accounts, audit)
        reread, reread_labels, reread_values, _ = read_panel(saved)
        pd.testing.assert_frame_equal(reread[original.columns], original)
        assert reread_labels["nis_1"] == "Original NIS"
        assert reread_values["category"] == {1: "One", 2: "Two"}
    print("Offline checks passed: direct/ambiguous links; ratios; losses; gaps; zero assets; "
          "exact next-year merge; original rows/values; Stata value/variable labels.")


# %% Main
def main(argv=None):
    parser = argparse.ArgumentParser(description="Download Compustat ROA for the fresh Revelio panel.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--username", default=WRDS_USERNAME)
    parser.add_argument("--no-global", action="store_true")
    parser.add_argument("--refresh", action="store_true", default=SETTINGS["wrds"]["refresh"])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        run_self_test()
        return
    if QUERY_BATCH_SIZE < 1 or QUERY_ATTEMPTS < 1 or CACHE_MAX_AGE_HOURS < 0:
        raise ValueError("Invalid batch/retry/cache settings.")
    input_path = choose_input()
    output = (args.output_dir if args.output_dir else input_path.parent).resolve()
    output.mkdir(parents=True, exist_ok=True)
    reports_dir = REPORTS_DIR.resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    if input_path == output / "analysis_nis_roa.dta":
        raise ValueError("Choose the ORIGINAL panel as input; the output cannot overwrite its input.")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True,
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOGS_DIR / "06_compustat.log", encoding="utf-8")])
    logging.info("Reading %s", input_path)
    panel, labels, values, dates = read_panel(input_path)
    ids = sorted(int(v) for v in panel.rcid.unique())
    first_year, last_year = int(panel.year.min()) - 1, int(panel.year.max()) + 1
    logging.info("Input: %s firm-years, %s firms, review years %s-%s. No sample thresholds.",
                 f"{len(panel):,}", f"{len(ids):,}", panel.year.min(), panel.year.max())
    manifest = {"version": VERSION, "status": "running", "started_at": now_utc(),
                "input_path": str(input_path), "input_bytes_sha256": file_hash(input_path),
                "input_rows": len(panel), "input_firms": len(ids),
                "account_end_years": [first_year, last_year], "formulas": {
                    "roa": "ni/at", "roa_ib": "ib/at", "roa_avg": "ni/mean(at,current/prior comparable year)"},
                "alignment": "calendar year of fiscal end datadate; exact t+1 for lead1",
                "thresholds_applied": False, "link_method": "same-RCID direct GVKEY, current vendor snapshot",
                "parent_substitution": False, "winsorization": False, "refresh": args.refresh}
    manifest_path = reports_dir / "run_manifest.json"
    atomic_json(manifest_path, manifest)
    db = WRDSDatabase(args.username)
    try:
        cache = SQLCache(CACHE_DIR.resolve(), db, args.refresh)
        location = resolve_mapping_table(db)
        raw_links = pull_mapping(cache, ids, location)
        links = resolve_links(raw_links, ids)
        gvkeys = sorted(v for v in links.roa_gvkey.unique() if v)
        links.to_csv(reports_dir / "roa_link_audit.csv", index=False)
        logging.info("Link status by firm:\n%s", links.roa_link_status.value_counts().to_string())
        if not gvkeys:
            raise RuntimeError("No unambiguous direct RCID-GVKEY links. See roa_link_audit.csv; no empty success export written.")
        sources, unavailable = resolve_account_sources(db, INCLUDE_GLOBAL and not args.no_global)
        raw_accounts = pull_accounts(cache, db, gvkeys, sources, first_year, last_year)
        if raw_accounts.empty:
            raise RuntimeError("No annual accounts returned for linked firms and requested years. Check account sources and filters.")
        accounts, record_audit = compute_accounts(raw_accounts)
        if accounts.empty:
            raise RuntimeError("No valid dated Compustat accounts remained; inspect the source extract.")
        share_counts = links.loc[links.roa_gvkey.ne("")].groupby("roa_gvkey").rcid.nunique()
        links["roa_rcids_per_gvkey"] = links.roa_gvkey.map(share_counts).fillna(0).astype("int32")
        result = attach_roa(panel, links, accounts)
        for col in panel.columns:
            pd.testing.assert_series_equal(result[col], panel[col], check_names=True)
        saved, coverage = write_outputs(result, panel.columns, labels, values, dates,
                                        output, links, accounts, record_audit, reports_dir=reports_dir)
        manifest.update({"status": "complete", "finished_at": now_utc(), "output_path": str(saved),
                         "output_rows": len(result), "linked_firms": int(links.roa_link_status.eq("direct").sum()),
                         "unique_gvkeys": len(gvkeys),
                         "roa_nonmissing": int(result.roa.notna().sum()),
                         "roa_lead1_nonmissing": int(result.roa_lead1.notna().sum()),
                         "mapping_table": location[0] + "." + location[1],
                         "sources": [{k: v for k, v in src.items() if k != "columns"} for src in sources],
                         "unavailable_products": unavailable})
        atomic_json(manifest_path, manifest)
        print("\n" + coverage.to_string(index=False))
        print("\nSaved " + str(saved))
        print(f"ROA observed: {result.roa.notna().sum():,}/{len(result):,}; "
              f"next-calendar-end-year ROA: {result.roa_lead1.notna().sum():,}/{len(result):,}.")
        print("All original rows retained. Reports: " + str(reports_dir))
    except (Exception, KeyboardInterrupt) as exc:
        manifest.update({"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                         "stopped_at": now_utc(), "error": str(exc)})
        atomic_json(manifest_path, manifest)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
