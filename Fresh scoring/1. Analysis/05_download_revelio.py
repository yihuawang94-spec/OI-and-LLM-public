# -*- coding: utf-8 -*-
"""05. Download full-history Glassdoor NIS and actual turnover from WRDS.

SPYDER: run this file directly after installing requirements.txt. WRDS uses
its normal login / .pgpass. Set up saved credentials in Anaconda Prompt first
if Spyder warns that it cannot hide password input. Approve any Duo Mobile push.
No existing DTA or previous project is required.
Paths follow this package wherever it is extracted.

Default population: US-located current employees, English-language Glassdoor
reviews; US workforce. START_YEAR/END_YEAR=None discovers all available eligible
review history. Workforce queries cover review years t-1 through t+1.

NIS definitions are unchanged: Pros + Cons + Advice, no headline; whitespace
word counts; whole-word FPP we/us/our/ours/ourselves minus TPP
 they/them/their/theirs/themselves. nis_1 pools pronoun and word counts across
reviews; nis_2 averages individual-review ratios. Empty selected text yields
missing NIS; nonempty text without pronouns yields zero. SQL computes review
counts and firm-year aggregates on WRDS. Individual review texts are not
transferred. This script makes no LLM requests.

ALL review firms enter the workforce query. The former >=20 reviews in any
one year query restriction has been removed. MIN_REVIEWS_FLAG and
MIN_HEADCOUNT_FLAG produce diagnostic columns only; neither controls queries
nor deletes panel rows. More firms may therefore have turnover data than in
the earlier extraction, and the full-history download may take much longer.
Queries run in 100-firm batches with bounded retries, progress messages and
checksummed checkpoints; a rerun resumes completed queries in THIS package.
Local planning uses a compact missing-year mask, with a direct path for an
empty workforce cache. Workforce SQL, batch ordering and saved-cache rules are unchanged.
Company mappings use separately saved 1,000-firm batches when the query plan
supports them. A sequential-scan plan instead queries all remaining firms once,
avoiding repeated full-table scans. Existing whole-request mapping caches work.

Turnover = annual external outflow / mean monthly headcount. A valid annual
rate requires 12 complete calendar months, positive average headcount, and
nonnegative nonmissing headcount/outflow in all supplied geographic cells.
Missing inflow does not invalidate turnover. No zero-fill, winsorisation or
headcount cutoff is applied. Turnover includes both quits and other exits.
turnover_lead1 matches exactly calendar year t+1. NAICS comes from the same
RCID in company_mapping; there is no parent-company rollup.

FILES
  0. Raw/Revelio/<scope>/cache: downloaded aggregate chunks and SQL metadata.
  2. Data/analysis_nis_turnover.dta: full review-firm-year panel for Stata.
  2. Data/reviews_firmyear.dta and workforce_firmyear.dta: component panels.
  4. Output Appendix/Revelio/<scope>: coverage, dictionary and run manifest.
  4. Output Appendix/Logs/05_revelio_<scope>.log: connection/query diagnostics.
Global scope, if selected, writes data under 2. Data/global.

No old project is scanned or imported. Existing chunks within this package
can resume: recent review years refresh after 24h, and workforce probes detect
recent vendor changes. --refresh re-queries all data. Cached aggregates do not
make a point-in-time archive of the vendor's underlying source records.

Terminal alternatives:
  python 05_download_revelio.py --self-test       # offline, no WRDS connection
  python 05_download_revelio.py --check-only      # licensed WRDS schema checks
  python 05_download_revelio.py --refresh         # fresh queries for this package

Required licensed data: Revelio sentiment_individual_reviews,
workforce_dynamics_geo, company_mapping. Source definitions:
https://www.data-dictionary.reveliolabs.com/data.html
https://www.data-dictionary.reveliolabs.com/faq.html
"""

# %% Study configuration comes from settings.json; internal query constants follow.
from pathlib import Path

from project_settings import load_settings
PROJECT_DIR = Path(__file__).resolve().parent.parent
SETTINGS = load_settings(PROJECT_DIR)
DATA_DIR = PROJECT_DIR / "2. Data"
OUTPUT_DIR = DATA_DIR
CACHE_BASE = PROJECT_DIR / "0. Raw" / "Revelio"
REPORTS_BASE = PROJECT_DIR / "3. Output" / "Appendix" / "Revelio"
LOGS_DIR = PROJECT_DIR / "3. Output" / "Logs"
COUNTRY_SCOPE = SETTINGS["wrds"]["workforce_scope"]                    # "US" or "GLOBAL"
START_YEAR = SETTINGS["wrds"]["start_year"]                       # None = earliest eligible review year
END_YEAR = SETTINGS["wrds"]["end_year"]                         # None = latest eligible review year
WRDS_USERNAME = SETTINGS["wrds"]["username"] or None               # WRDS account name; never put a password here
CURRENT_EMPLOYEES_ONLY = True
ENGLISH_LANGUAGE_CODES = ("eng", "en")
INCLUDE_SUMMARY = False
USE_SCALED = False                      # select a MATCHED metric trio
MIN_REVIEWS_FLAG = SETTINGS["analysis"]["min_reviews_per_firm_year"]                    # diagnostic flag only; all firms are queried
MIN_HEADCOUNT_FLAG = 50
FIRMS_PER_WORKFORCE_QUERY = 100          # ~100 keeps Postgres on the (rcid, datemonth) index
FIRMS_PER_INDUSTRY_QUERY = 1000          # checkpoint company mappings independently when indexed
WORKFORCE_PROBE_FIRMS = 25               # firms re-queried (at most daily) to detect WRDS updates
STATEMENT_TIMEOUT_MINUTES = 30
CONNECT_TIMEOUT_SECONDS = 120           # network connection only; user-input prompts have no timer
QUERY_ATTEMPTS = 3
RECENT_YEARS_TO_REFRESH = 2
RECENT_CACHE_MAX_AGE_HOURS = 24

# If your WRDS subscription uses another schema/table, edit here. Field aliases
# below accommodate both your previous script and the vendor's public names.
WRDS_SCHEMA = "revelio"
REVIEWS_TABLE = "sentiment_individual_reviews"
WORKFORCE_TABLE = "workforce_dynamics_geo"
COMPANY_TABLE = "company_mapping"       # rcid -> naics_code, for industry fixed effects

# %% Imports and measurement definitions
import argparse
import collections
import contextlib
import dataclasses
import hashlib
import importlib.util
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

VERSION = "oi_llm_fresh_revelio_2.0.0"
CACHE_VERSION = "shared-cache-1"
NATIVE_DATE_TYPES = ("date", "timestamp without time zone", "timestamp with time zone")
OPTIONAL_SERVER_SETTINGS = ("jit",)
STRING_COLUMNS = ("company", "naics_code")
WF_COLUMNS = ["rcid", "year", "months_scope", "months_hc", "months_outflow", "months_inflow",
              "months_global_hc", "headcount_avg_obs", "global_hc_avg_obs", "external_outflow_obs",
              "external_inflow_obs", "first_month_scope", "last_month_scope"]
US_ALIASES = ("united states", "united states of america", "usa", "us", "u.s.", "u.s.a.")
FPP = ("we", "us", "our", "ours", "ourselves")
TPP = ("they", "them", "their", "theirs", "themselves")
SOURCES = ["https://www.data-dictionary.reveliolabs.com/data.html",
           "https://www.data-dictionary.reveliolabs.com/faq.html"]

REVIEW_ALIASES = {
    "rcid": ("rcid",), "date": ("review_date", "review_date_time"),
    "company": ("company", "company_name"), "country": ("country",),
    "language": ("review_language_id", "review_language", "language"),
    "current": ("reviewer_current_job",), "seniority": ("seniority",),
    "tenure": ("reviewer_length_of_employment",),
    "pros": ("review_pros",), "cons": ("review_cons",),
    "advice": ("review_advice",), "summary": ("review_summary",),
    "overall_rating": ("rating_overall",),
    "rating_culture": ("rating_culture_and_values",),
    "rating_career": ("rating_career_opportunities",),
    "rating_compensation": ("rating_compensation_and_benefits",),
    "rating_leadership": ("rating_senior_leadership",),
    "rating_worklife": ("rating_work_life_balance",),
    "rating_diversity": ("rating_diversity_and_inclusion",),
    "ceo_approval": ("rating_ceo",), "recommend": ("rating_recommend_to_friend",),
    "business_outlook": ("rating_business_outlook",),
}
STAR_FIELDS = ("overall_rating", "rating_culture", "rating_career", "rating_compensation",
               "rating_leadership", "rating_worklife", "rating_diversity")
CATEGORY_FIELDS = ("ceo_approval", "recommend", "business_outlook")
MEAN_FIELDS = STAR_FIELDS + CATEGORY_FIELDS + ("seniority_mean", "tenure_mean")
TRUE_VALUES = ("true", "t", "yes", "1", "current", "current employee")
POSITIVE_VALUES = ("approve", "positive", "yes", "approved", "recommend")
NEGATIVE_VALUES = ("disapprove", "negative", "no", "disapproved", "not recommend")
NEUTRAL_VALUES = ("no opinion", "no_opinion", "neutral", "neither")


@dataclasses.dataclass(frozen=True)
class Settings:
    scope: str = COUNTRY_SCOPE
    start_year: object = START_YEAR
    end_year: object = END_YEAR
    current_only: bool = CURRENT_EMPLOYEES_ONLY
    languages: tuple = ENGLISH_LANGUAGE_CODES
    include_summary: bool = INCLUDE_SUMMARY
    scaled: bool = USE_SCALED
    min_reviews: int = MIN_REVIEWS_FLAG
    min_headcount: int = MIN_HEADCOUNT_FLAG
    firms_per_query: int = FIRMS_PER_WORKFORCE_QUERY
    probe_firms: int = WORKFORCE_PROBE_FIRMS
    schema: str = WRDS_SCHEMA
    reviews_table: str = REVIEWS_TABLE
    workforce_table: str = WORKFORCE_TABLE
    company_table: str = COMPANY_TABLE


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def qi(name):
    """Quote a single trusted SQL identifier; never allow SQL fragments."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Invalid SQL identifier: " + repr(name))
    return '"' + name + '"'


def lit(value):
    # Use SQL E strings and escape both backslashes and quotes explicitly.
    return "E'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def sql_values(values):
    return ", ".join(lit(v) for v in values)


def normalised(col):
    return f"lower(btrim({col}::text))"


def scope_sql(col, cfg):
    return f"{normalised(col)} IN ({sql_values(US_ALIASES)})" if cfg.scope == "US" else "TRUE"


def safe_numeric(col):
    # No cast is attempted for missing, sentinel text, NaN or infinity strings.
    return (f"CASE WHEN btrim({col}::text) ~ "
            + lit(r"^[+-]?[0-9]+([.][0-9]+)?$")
            + f" THEN btrim({col}::text)::double precision END")


def bounded_numeric(col, lower, upper):
    n = safe_numeric(col)
    return f"CASE WHEN ({n}) BETWEEN {lower} AND {upper} THEN ({n}) END"


def category_numeric(col):
    v = normalised(col)
    return (f"CASE WHEN {v} IN ({sql_values(POSITIVE_VALUES)}) THEN 1.0 "
            f"WHEN {v} IN ({sql_values(NEGATIVE_VALUES)}) THEN 0.0 "
            f"WHEN {v} IN ({sql_values(NEUTRAL_VALUES)}) THEN 0.5 END")


def date_expr(col, dtype, monthly=False):
    """Native dates preserve range-index opportunities; text must use ISO dates."""
    if dtype in ("date", "timestamp without time zone", "timestamp with time zone"):
        return col
    # The vendor documents YYYY-MM for month and ISO timestamps for reviews.
    if monthly:
        return (f"CASE WHEN btrim({col}::text) ~ " + lit(r"^[0-9]{4}-(0[1-9]|1[0-2])($|[- T])")
                + f" THEN (left(btrim({col}::text), 7) || '-01')::date END")
    return (f"CASE WHEN btrim({col}::text) ~ " + lit(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}")
            + f" THEN left(btrim({col}::text), 10)::date END")


def count_expressions(text_col):
    # PostgreSQL word boundaries differ from Python's. E strings preserve them.
    wc = f"CASE WHEN {text_col} = '' THEN 0 ELSE cardinality(string_to_array({text_col}, ' ')) END"
    fp_pattern = lit(r"\m(" + "|".join(FPP) + r")\M")
    tp_pattern = lit(r"\m(" + "|".join(TPP) + r")\M")
    fp = f"cardinality(regexp_split_to_array({text_col}, {fp_pattern})) - 1"
    tp = f"cardinality(regexp_split_to_array({text_col}, {tp_pattern})) - 1"
    return wc, fp, tp


def normalise_text_sql(expression):
    return f"btrim(regexp_replace(lower({expression}), {lit('[[:space:]]+')}, ' ', 'g'))"


def reference_counts(text):
    """Independent Python reference used by both offline and live SQL checks."""
    text = text.lower()
    return (len(text.split()), len(re.findall(r"\b(?:" + "|".join(FPP) + r")\b", text)),
            len(re.findall(r"\b(?:" + "|".join(TPP) + r")\b", text)))


# %% WRDS connection, bounded retries and schema discovery
def authentication_failed(exc):
    """Authentication errors often have no SQLSTATE when connecting via libpq."""
    orig = getattr(exc, "orig", exc)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    message = str(orig).lower()
    return (str(code).startswith("28") or any(marker in message for marker in (
        "pam authentication failed", "password authentication failed",
        "authentication failed", "no password supplied", "account is locked", "account locked")))


class Database:
    def __init__(self, username=None):
        self.username = username
        self.conn = None
        # Session settings, re-applied on every (re)connect. JIT compilation adds
        # compile time to each of hundreds of aggregation queries, so it is off.
        self.settings = {"statement_timeout": str(int(STATEMENT_TIMEOUT_MINUTES * 60 * 1000)),
                         "lock_timeout": "60000", "TimeZone": "UTC", "jit": "off"}

    def close(self):
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
        self.conn = None

    def connect(self):
        try:
            import wrds
        except ImportError as e:
            raise RuntimeError("Install wrds in this Python environment: pip install wrds pandas numpy") from e
        self.close()
        logging.info("Connecting to WRDS; complete any login prompt and approve the Duo Mobile push. "
                     "The data query has not started. Network connection timeout: %ss.",
                     CONNECT_TIMEOUT_SECONDS)
        kwargs = {"wrds_connect_args": {
            "sslmode": "require", "application_name": "OI_and_LLM_Revelio",
            "connect_timeout": int(CONNECT_TIMEOUT_SECONDS),
            # Server statement_timeout cannot detect a broken network socket.
            # libpq applies these where supported by the operating system.
            "keepalives": 1, "keepalives_idle": 60,
            "keepalives_interval": 15, "keepalives_count": 4,
            "tcp_user_timeout": 120000,
            # Apply timeouts before wrds.Connection loads its library list.
            "options": (f"-c statement_timeout={self.settings['statement_timeout']} "
                        f"-c lock_timeout={self.settings['lock_timeout']}"),
        }}
        if self.username:
            kwargs["wrds_username"] = self.username
        self.conn = wrds.Connection(**kwargs)
        for name, value in list(self.settings.items()):
            try:
                self.conn.raw_sql(f"SELECT set_config({lit(name)}, {lit(value)}, false)")
            except Exception as exc:  # wrds runs in autocommit, so the session stays usable
                if name not in OPTIONAL_SERVER_SETTINGS:
                    raise
                logging.warning("Server setting %s=%s unavailable (%s); continuing without it.", name, value, exc)
                self.settings.pop(name)
        logging.info("Connected to WRDS; per-query timeout %s minutes.", STATEMENT_TIMEOUT_MINUTES)

    def set(self, name, value):
        """Change a session setting now and after any reconnect."""
        self.settings[name] = str(value)
        self.query(f"SELECT set_config({lit(name)}, {lit(str(value))}, false)", f"Set {name}={value}")

    def query(self, sql, label):
        for attempt in range(1, QUERY_ATTEMPTS + 1):
            started = time.monotonic()
            stop = threading.Event()
            worker = None

            def heartbeat():
                while not stop.wait(30):
                    logging.info("%s: still running after %.0fs.", label, time.monotonic() - started)

            try:
                if self.conn is None:
                    self.connect()
                started = time.monotonic()
                logging.info("%s: query started.", label)
                worker = threading.Thread(target=heartbeat, daemon=True)
                worker.start()
                result = self.conn.raw_sql(sql)
                logging.info("%s: %s rows in %.1fs.", label, f"{len(result):,}", time.monotonic() - started)
                return result
            except Exception as exc:
                orig = getattr(exc, "orig", exc)
                code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
                self.close()
                if authentication_failed(exc):
                    raise RuntimeError(
                        "WRDS rejected the login. Automatic retries have stopped. Check the WRDS "
                        "username/password and approve Duo Mobile; a changed IP may require fresh MFA. "
                        "If this persists after a successful website login on the same connection, "
                        "contact WRDS support. Saved download chunks are unchanged. "
                        "Use Anaconda Prompt for password entry if Spyder cannot hide it."
                    ) from exc
                if code == "57014":  # statement timeout or cancel: a retry would repeat the same wait
                    raise RuntimeError(f"{label} hit the {STATEMENT_TIMEOUT_MINUTES}-minute statement timeout "
                                       "or was cancelled. Completed downloads are cached; lower "
                                       "FIRMS_PER_WORKFORCE_QUERY or raise STATEMENT_TIMEOUT_MINUTES, "
                                       "then rerun.") from exc
                transient = (str(code).startswith("08") or code in ("40001", "40P01", "57P01")
                             or type(orig).__name__ in ("OperationalError", "InterfaceError"))
                if not transient or attempt == QUERY_ATTEMPTS:
                    raise RuntimeError(f"{label} failed; completed downloads are cached. "
                                       f"SQLSTATE={code}; {exc}") from exc
                logging.warning("%s: transient failure %s, retry %d/%d in %ds.",
                                label, code, attempt + 1, QUERY_ATTEMPTS, 2 ** attempt)
                stop.set()
                if worker is not None:
                    worker.join(timeout=1)
                time.sleep(2 ** attempt)
            finally:
                stop.set()
                if worker is not None:
                    worker.join(timeout=1)


class TableUnavailableError(RuntimeError):
    """The schema query succeeded, but the requested table was not visible."""


def table_metadata(db, schema, table):
    sql = ("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = "
           + lit(schema) + " AND table_name = " + lit(table) + " ORDER BY ordinal_position")
    result = db.query(sql, f"Inspect {schema}.{table}")
    if result.empty:
        raise TableUnavailableError(f"Cannot see {schema}.{table}. Check WRDS subscription/table names in CONFIG.")
    return dict(zip(result.column_name, result.data_type))


def resolve_schema(db, cfg):
    rmeta = table_metadata(db, cfg.schema, cfg.reviews_table)
    wmeta = table_metadata(db, cfg.schema, cfg.workforce_table)
    r = {key: next((x for x in aliases if x in rmeta), None)
         for key, aliases in REVIEW_ALIASES.items()}
    required = ["rcid", "date", "language", "pros", "cons", "advice"]
    if cfg.current_only:
        required.append("current")
    if cfg.scope == "US":
        required.append("country")
    if cfg.include_summary:
        required.append("summary")
    missing = [key for key in required if r[key] is None]
    prefix = "scaled_" if cfg.scaled else ""
    w = {"rcid": "rcid", "date": next((x for x in ("datemonth", "month") if x in wmeta), None),
         "country": "country", "hc": prefix + "count", "outflow": prefix + "external_outflow",
         "inflow": prefix + "external_inflow"}
    wrequired = ["rcid", "date", "hc", "outflow"] + (["country"] if cfg.scope == "US" else [])
    wmissing = [key for key in wrequired if not w[key] or w[key] not in wmeta]
    if missing or wmissing:
        raise RuntimeError(f"Required columns missing: reviews={missing}; workforce={wmissing}. "
                           "Edit the field aliases/CONFIG; no cached measures have been reused.")
    if w["inflow"] not in wmeta:
        w["inflow"] = None
        logging.warning("External inflow unavailable. Turnover will still be computed.")
    for field in MEAN_FIELDS:
        key = {"seniority_mean": "seniority", "tenure_mean": "tenure"}.get(field, field)
        if r.get(key) is None:
            logging.warning("Optional review field %s unavailable; output will be missing.", field)
    if r["seniority"] is None:
        logging.warning("Seniority unavailable: rank-and-file NIS will be missing.")
    return {"reviews": r, "workforce": w, "review_types": rmeta, "workforce_types": wmeta}


def review_parts(cfg, schema):
    r = schema["reviews"]
    dt = date_expr(qi(r["date"]), schema["review_types"][r["date"]])
    filters = [f"{qi(r['rcid'])} IS NOT NULL",
               f"{normalised(qi(r['language']))} IN ({sql_values(cfg.languages)})"]
    if cfg.current_only:
        filters.append(f"{normalised(qi(r['current']))} IN ({sql_values(TRUE_VALUES)})")
    if cfg.scope == "US":
        filters.append(scope_sql(qi(r["country"]), cfg))
    return r, dt, " AND ".join(filters)


def annual_date_filter(col, dtype, year, monthly=False):
    """Range-filter ISO text directly so WRDS can use any date-column index.

    Date parsing stays in SELECT/GROUP BY. Text columns documented as YYYY-MM
    or ISO timestamps sort in chronological order. Reject malformed date rows
    separately using the parser; native date/timestamp columns need no parsing.
    """
    lo, hi = lit(str(year) + "-01"), lit(str(year + 1) + "-01")
    if dtype in ("date", "timestamp without time zone", "timestamp with time zone"):
        return f"{col} >= {lit(str(year) + '-01-01')} AND {col} < {lit(str(year + 1) + '-01-01')}"
    parsed = date_expr(col, dtype, monthly=monthly)
    return f"{col} >= {lo} AND {col} < {hi} AND {parsed} IS NOT NULL"


def discover_review_history(db, cfg, schema, cache, refresh=False):
    """Eligible review date range. The workforce range follows from it (t-1 to t+1),
    which replaces the previous full-table MIN/MAX over the 7-billion-row workforce
    table. The result is cached for RECENT_CACHE_MAX_AGE_HOURS."""
    r, rdt, filters = review_parts(cfg, schema)
    sql = (f"SELECT MIN({rdt}) AS first_date, MAX({rdt}) AS last_date "
           f"FROM {qi(cfg.schema)}.{qi(cfg.reviews_table)} WHERE {filters}")
    key = digest(sql)
    saved = cache.state.get("review_history", {}).get(key)
    if saved and not refresh and time.time() - saved["epoch"] < RECENT_CACHE_MAX_AGE_HOURS * 3600:
        logging.info("Eligible reviews history (checked %s): %s through %s.",
                     saved["checked_at"], saved["first"], saved["last"])
        return {"first": saved["first"], "last": saved["last"]}
    frame = db.query(sql, "Discover eligible reviews history")
    if frame.empty or pd.isna(frame.iloc[0].first_date) or pd.isna(frame.iloc[0].last_date):
        raise RuntimeError("No eligible reviews dates found. Inspect population filters and access.")
    first, last = (pd.Timestamp(frame.iloc[0][x]) for x in ("first_date", "last_date"))
    if first.year < 1900 or last.year > datetime.now(timezone.utc).year + 1:
        raise RuntimeError(f"Implausible reviews history: {first} to {last}. Check date fields.")
    bounds = {"first": first.date().isoformat(), "last": last.date().isoformat()}
    logging.info("Eligible reviews history: %s through %s.", bounds["first"], bounds["last"])
    cache.state.setdefault("review_history", {})[key] = {**bounds, "epoch": time.time(), "checked_at": utc_now()}
    cache.save_state()
    return bounds


def workforce_indexes(db, cfg):
    """The workforce table's index definitions; the fast path needs one led by rcid."""
    frame = db.query("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = " + lit(cfg.schema)
                     + " AND tablename = " + lit(cfg.workforce_table) + " ORDER BY indexname",
                     "List workforce table indexes")
    return [str(x) for x in frame.indexdef] if not frame.empty else []


def sample_review_firms(db, cfg, schema, n):
    """Up to n review firm IDs, used to test the workforce query plan in --check-only."""
    r, dt, where = review_parts(cfg, schema)
    frame = db.query(f"SELECT DISTINCT rcid FROM (SELECT {qi(r['rcid'])}::bigint AS rcid "
                     f"FROM {qi(cfg.schema)}.{qi(cfg.reviews_table)} WHERE {where} LIMIT 20000) s",
                     "Sample review firm IDs")
    ids = sorted(int(x) for x in frame.rcid)[:n]
    if not ids:
        raise RuntimeError("No eligible review firms found for the query-plan check.")
    return ids


def inspect_categories(db, cfg, schema):
    """Inspect an available sample; preserve raw codes so mappings are auditable."""
    r, dt, where = review_parts(cfg, schema)
    cols = [r.get(k) for k in ("ceo_approval", "recommend", "business_outlook", "current", "language") if r.get(k)]
    select = ", ".join(qi(c) for c in cols)
    if not cols:
        return {}
    # LIMIT is deliberately a sample, not a full-history count of each category.
    sample = db.query(f"SELECT {select} FROM {qi(cfg.schema)}.{qi(cfg.reviews_table)} WHERE {where} LIMIT 10000",
                      "Inspect review category codes (up to 10,000 rows)")
    result = {c: sample[c].fillna("<missing>").astype(str).value_counts().to_dict() for c in cols}
    known = set(POSITIVE_VALUES + NEGATIVE_VALUES + NEUTRAL_VALUES)
    for key in CATEGORY_FIELDS:
        if r.get(key) in sample:
            values = set(sample[r[key]].dropna().astype(str).str.strip().str.lower())
            unknown = values - known - {"", "nan", "none", "null"}
            if unknown:
                logging.warning("Unmapped %s category values %s. They remain missing; see manifest/CONFIG.", key, sorted(unknown))
    return result


def live_count_check(db):
    cases = ["", "We like our team. They respect us.", "we\twork\nwith them", "awesome ours ourselves theirs themselves",
             "WE, WE! they/them; our-workers.", "  our   people \r\n ", "we_work together", "we're with them"]
    values = ", ".join(f"({i}, {lit(t)})" for i, t in enumerate(cases))
    wc, fp, tp = count_expressions("txt")
    sql = (f"WITH v(id, raw_text) AS (VALUES {values}), t AS (SELECT id, "
           + normalise_text_sql("raw_text") + f" AS txt FROM v) SELECT id, {wc} AS wc, {fp} AS fp, {tp} AS tp FROM t ORDER BY id")
    actual = db.query(sql, "Verify PostgreSQL word/pronoun counting")
    expected = [reference_counts(t) for t in cases]
    got = [tuple(int(row[c]) for c in ("wc", "fp", "tp")) for _, row in actual.iterrows()]
    if got != expected:
        raise RuntimeError(f"Live SQL/Python count mismatch. SQL={got}, reference={expected}. Stop before extraction.")


# %% SQL: one annual review aggregation and one annual workforce firm batch
def review_sql(year, cfg, schema):
    r, dt, where = review_parts(cfg, schema)
    text_keys = ("pros", "cons", "advice") + (("summary",) if cfg.include_summary else ())
    text = "concat_ws(' ', " + ", ".join(f"coalesce({qi(r[k])}::text, '')" for k in text_keys) + ")"
    selected = [f"{qi(r['rcid'])}::bigint AS rcid",
                (f"{qi(r['company'])}::text AS company" if r.get("company") else "NULL::text AS company"),
                normalise_text_sql(text) + " AS txt"]
    for name in MEAN_FIELDS:
        key = {"seniority_mean": "seniority", "tenure_mean": "tenure"}.get(name, name)
        col = qi(r[key]) if r.get(key) else None
        if col is None:
            expression = "NULL::double precision"
        elif name in STAR_FIELDS:
            expression = bounded_numeric(col, 1, 5)
        elif name in CATEGORY_FIELDS:
            expression = category_numeric(col)
        elif name == "seniority_mean":
            expression = bounded_numeric(col, 1, 7)
        else:
            expression = bounded_numeric(col, 0, 100)
        selected.append(f"{expression} AS {qi(name)}")
    wc, fp, tp = count_expressions("txt")
    aggregates = ["MAX(company) AS company", "COUNT(*) AS n_reviews", "COUNT(*) FILTER (WHERE wc > 0) AS n_nis_reviews",
                  "SUM(wc) AS n_words", "SUM(fp) AS n_fpp", "SUM(tp) AS n_tpp",
                  "AVG((fp-tp)::double precision / NULLIF(wc, 0)) AS nis_2",
                  "COUNT(*) FILTER (WHERE seniority_mean BETWEEN 1 AND 4) AS n_reviews_rf",
                  "COUNT(*) FILTER (WHERE seniority_mean BETWEEN 1 AND 4 AND wc > 0) AS n_nis_reviews_rf",
                  "SUM(wc) FILTER (WHERE seniority_mean BETWEEN 1 AND 4) AS n_words_rf",
                  "SUM(fp) FILTER (WHERE seniority_mean BETWEEN 1 AND 4) AS n_fpp_rf",
                  "SUM(tp) FILTER (WHERE seniority_mean BETWEEN 1 AND 4) AS n_tpp_rf",
                  "AVG((fp-tp)::double precision / NULLIF(wc, 0)) FILTER (WHERE seniority_mean BETWEEN 1 AND 4) AS nis_2_rf"]
    for name in MEAN_FIELDS:
        aggregates.extend([f"AVG({qi(name)}) AS {qi(name)}", f"COUNT({qi(name)}) AS {qi('n_' + name)}"])
    return ("WITH base AS (SELECT " + ",\n".join(selected)
            + f" FROM {qi(cfg.schema)}.{qi(cfg.reviews_table)} WHERE {where}"
            + " AND " + annual_date_filter(qi(r['date']), schema['review_types'][r['date']], year) + "), "
            + f"scored AS (SELECT *, {wc} AS wc, {fp} AS fp, {tp} AS tp FROM base) "
            + f"SELECT rcid, {year}::integer AS year, " + ",\n".join(aggregates)
            + " FROM scored GROUP BY rcid ORDER BY rcid")


def valid_value(col):
    """Finite and nonnegative. NULL, NaN, +/-Infinity and negative values are invalid."""
    return f"({col} IS NOT NULL AND {col} >= 0 AND {col} < {lit('Infinity')}::double precision)"


def range_date_filter(col, dtype, first_year, last_year, monthly=False):
    """annual_date_filter for a span of calendar years; both columns of the
    (rcid, datemonth) index then serve as index conditions."""
    if dtype in NATIVE_DATE_TYPES:
        return (f"{col} >= {lit(str(first_year) + '-01-01')} "
                f"AND {col} < {lit(str(last_year + 1) + '-01-01')}")
    parsed = date_expr(col, dtype, monthly=monthly)
    return (f"{col} >= {lit(str(first_year) + '-01')} AND {col} < {lit(str(last_year + 1) + '-01')} "
            f"AND {parsed} IS NOT NULL")


def workforce_sql(ids, first_year, last_year, cfg, schema):
    """Firm-year workforce aggregates for one firm batch over a span of years.

    Level 1 groups cells by (firm, source month, in scope) on WRDS, so the
    country test and the validity checks run once per cell. Level 2 forms
    firm-months with the previous rules: a month's scope metric requires every
    supplied scope cell to be valid (missing/negative/NaN/infinite cells void
    it rather than being skipped by SUM); global headcount requires every cell
    to be valid. Level 3 forms calendar-year aggregates, as before.
    """
    w = schema["workforce"]
    dtype = schema["workforce_types"][w["date"]]
    ids = [int(x) for x in ids]
    if not ids:
        raise ValueError("Empty workforce ID batch")
    if first_year > last_year:
        raise ValueError("Empty workforce year span")
    scope = scope_sql(qi(w["country"]), cfg) if cfg.scope == "US" else "TRUE"
    metrics = [("hc", qi(w["hc"])), ("outflow", qi(w["outflow"]))]
    if w.get("inflow"):
        metrics.append(("inflow", qi(w["inflow"])))
    cell = [f"{qi(w['rcid'])}::bigint AS rcid", f"{qi(w['date'])} AS source_month",
            f"({scope}) AS in_scope", "COUNT(*) AS cells"]
    for name, col in metrics:
        cell += [f"COUNT(*) FILTER (WHERE NOT {valid_value(col)}) AS bad_{name}", f"SUM({col}) AS {name}"]
    month = date_expr("source_month", dtype, monthly=True)
    scope_cells = "COALESCE(SUM(cells) FILTER (WHERE in_scope), 0)"
    monthly = ["rcid", f"date_trunc('month', ({month})::timestamp)::date AS month", f"{scope_cells} AS scope_cells"]
    for name in ("hc", "outflow", "inflow"):
        if name == "inflow" and not w.get("inflow"):
            monthly.append("NULL::double precision AS inflow")
        else:
            monthly.append(f"CASE WHEN {scope_cells} > 0 AND COALESCE(SUM(bad_{name}) FILTER (WHERE in_scope), 0) = 0 "
                           f"THEN SUM({name}) FILTER (WHERE in_scope) END AS {name}")
    monthly.append("CASE WHEN SUM(bad_hc) = 0 THEN SUM(hc) END AS hc_global")
    annual = ["COUNT(*) FILTER (WHERE scope_cells > 0) AS months_scope",
              "COUNT(hc) AS months_hc", "COUNT(outflow) AS months_outflow", "COUNT(inflow) AS months_inflow",
              "COUNT(hc_global) AS months_global_hc", "AVG(hc) AS headcount_avg_obs",
              "AVG(hc_global) AS global_hc_avg_obs", "SUM(outflow) AS external_outflow_obs",
              "SUM(inflow) AS external_inflow_obs",
              "MIN(EXTRACT(MONTH FROM month)) FILTER (WHERE scope_cells > 0) AS first_month_scope",
              "MAX(EXTRACT(MONTH FROM month)) FILTER (WHERE scope_cells > 0) AS last_month_scope"]
    return ("WITH cell AS (SELECT " + ",\n".join(cell)
            + f" FROM {qi(cfg.schema)}.{qi(cfg.workforce_table)} WHERE {qi(w['rcid'])} IN ({','.join(map(str, ids))})"
            + " AND " + range_date_filter(qi(w["date"]), dtype, first_year, last_year, monthly=True)
            + " GROUP BY 1, 2, 3), "
            + "firm_month AS (SELECT " + ",\n".join(monthly) + " FROM cell GROUP BY 1, 2) "
            + "SELECT rcid, EXTRACT(YEAR FROM month)::integer AS year, " + ",\n".join(annual)
            + " FROM firm_month GROUP BY 1, 2 ORDER BY 1, 2")


def workforce_family(cfg, schema):
    """Digest of everything in the workforce SQL except firm IDs and years, so cached
    results combine only across identical definitions (scope, metric trio, table)."""
    return digest(workforce_sql([0], 1, 1, cfg, schema))[:16]


def check_workforce_plan(db, cfg, sql, n_firms):
    """EXPLAIN (no execution) one real workforce query; stop if it would read the whole table."""
    plan = db.query("EXPLAIN " + sql, "Check workforce query plan")
    lines = [str(x) for x in plan.iloc[:, 0]]
    scans = [line.strip() for line in lines if "Scan" in line]
    for line in scans:
        logging.info("Workforce plan (%d firms): %s", n_firms, line)
    if any(re.search(r'Seq Scan on "?' + re.escape(cfg.workforce_table) + r'\b', line) for line in lines):
        raise RuntimeError(f"Postgres plans a full scan of {cfg.workforce_table} for a {n_firms}-firm query, "
                           "which takes hours per query on WRDS. Nothing was pulled. Lower "
                           "FIRMS_PER_WORKFORCE_QUERY, or check the index list at the top of run.log.")
    return scans


# %% Shared download cache: one checksummed CSV.GZ + JSON pair per query, keyed by its SQL
class Cache:
    def __init__(self, directory, db):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.state_path = self.dir / "cache_state.json"
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.stats = {"queried": 0, "reused": 0}

    def save_state(self):
        atomic_json(self.state_path, self.state)

    @staticmethod
    def key(sql):
        # The SQL fixes the result for a given WRDS vintage; thresholds and flags stay out.
        return digest({"cache": CACHE_VERSION, "sql": sql})

    def paths(self, kind, key):
        stem = f"{kind}_{key[:24]}"
        return self.dir / (stem + ".csv.gz"), self.dir / (stem + ".json")

    @staticmethod
    def read(data_path, meta):
        if hashlib.sha256(Path(data_path).read_bytes()).hexdigest() != meta["file_sha256"]:
            raise ValueError("checksum mismatch")
        # Only empty fields are missing, so a company called "NA" keeps its name;
        # code columns stay text, so a NAICS code keeps its exact digits.
        dtypes = {"rcid": "int64", "year": "int64", **{c: str for c in meta.get("string_columns", [])}}
        frame = pd.read_csv(data_path, dtype=dtypes, keep_default_na=False, na_values=[""])
        if list(frame.columns) != meta["columns"] or len(frame) != meta["rows"]:
            raise ValueError("row/column mismatch")
        return frame

    def lookup(self, kind, sql):
        data_path, meta_path = self.paths(kind, self.key(sql))
        if not (data_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("sql") != sql:
                raise ValueError("stored SQL differs")
            return self.read(data_path, meta), meta
        except (ValueError, KeyError, OSError) as exc:
            logging.warning("Cached %s cannot be reused (%s); querying again.", data_path.name, exc)
            return None

    def store(self, kind, sql, frame, extra, saved_epoch=None):
        key = self.key(sql)
        data_path, meta_path = self.paths(kind, key)
        tmp = data_path.with_name(data_path.name + ".tmp")
        frame.to_csv(tmp, index=False, compression="gzip")
        checksum = hashlib.sha256(tmp.read_bytes()).hexdigest()
        os.replace(tmp, data_path)
        epoch = time.time() if saved_epoch is None else float(saved_epoch)
        meta = {"kind": kind, "key": key, "sql": sql, "rows": len(frame), "columns": list(frame.columns),
                "file_sha256": checksum, "saved_epoch": epoch,
                "saved_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(), **extra}
        atomic_json(meta_path, meta)
        return meta

    def query(self, kind, sql, label, extra):
        frame = self.db.query(sql, label)
        keys = extra.get("keys", ["rcid", "year"])
        if not set(keys).issubset(frame.columns):
            raise RuntimeError(f"Extraction returned no {'/'.join(keys)} key columns")
        if frame.duplicated(keys).any():
            raise RuntimeError(f"Duplicate {'/'.join(keys)} keys in extraction")
        if kind == "workforce" and len(frame):
            outside = (~frame.rcid.astype("int64").isin(extra["rcids"])
                       | ~frame.year.astype("int64").between(*extra["years"]))
            if outside.any():
                raise RuntimeError("A workforce query returned firm-years outside its request")
        if kind == "industry" and "rcids" in extra and not frame.rcid.isin(extra["rcids"]).all():
            raise RuntimeError("A company-mapping query returned firms outside its request")
        meta = self.store(kind, sql, frame, extra)
        self.stats["queried"] += 1
        # Return the stored copy, so fresh and cached results have identical types.
        return self.read(self.paths(kind, meta["key"])[0], meta), meta





def fetch_review_year(cache, year, sql, fresh_after):
    """Reuse verified SQL-identical chunks from this package, otherwise query WRDS."""
    hit = cache.lookup("reviews", sql)
    if hit is not None and hit[1]["saved_epoch"] >= fresh_after:
        cache.stats["reused"] += 1
        logging.info("Reuse reviews %d: %s firms.", year, f"{len(hit[0]):,}")
        return hit[0]
    return cache.query("reviews", sql, f"Reviews {year}", {"year": year})[0]


# %% Workforce pull: coverage per firm and year, 100-firm queries over all needed years
def workforce_firms(reviews):
    """All review firms; diagnostic review-count thresholds never select queries."""
    return sorted(int(x) for x in reviews.rcid.dropna().unique())


def fresh_threshold(invalidations, year):
    """Earliest save time still counted as current for a calendar year."""
    return max([float(epoch) for from_year, epoch in invalidations if from_year <= year], default=0.0)


@contextlib.contextmanager
def local_progress(label):
    """Keep long local work visible; database timeouts do not cover these steps."""
    started = time.monotonic()
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(30):
            logging.info("%s: still working locally after %.0fs.", label, time.monotonic() - started)

    logging.info("%s: starting local work.", label)
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    except BaseException:
        logging.info("%s: stopped after %.1fs.", label, time.monotonic() - started)
        raise
    else:
        logging.info("%s: finished in %.1fs.", label, time.monotonic() - started)
    finally:
        stop.set()
        worker.join(timeout=1)


def load_workforce_entries(cache, family):
    entries = []
    paths = sorted(cache.dir.glob("workforce_*.json"))
    logging.info("Workforce cache: %s saved chunk records to inspect.", f"{len(paths):,}")
    for number, meta_path in enumerate(paths, 1):
        if number % 100 == 0:
            logging.info("Workforce cache: inspecting record %s/%s; %s matching chunks verified.",
                         f"{number:,}", f"{len(paths):,}", f"{len(entries):,}")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("family") != family:
            continue
        data_path = meta_path.with_name(meta_path.name[:-len(".json")] + ".csv.gz")
        try:
            entries.append({"meta": meta, "frame": Cache.read(data_path, meta)})
        except (ValueError, KeyError, OSError) as exc:
            logging.warning("Cached %s cannot be reused (%s); its firm-years will be queried again.",
                            data_path.name, exc)
    return entries


def choose_workforce_sources(entries, firms, years, invalidations):
    """For each needed (rcid, year), the most recently saved current query covering it.

    A query covers every requested firm-year, including those for which WRDS
    returned no row, so a newer query also retires rows that have disappeared.
    """
    # Test each cached batch against one set, rather than scanning the entire
    # requested firm array again for each batch. Cache precedence stays below.
    firm_set = set(int(x) for x in firms)
    thresholds = {y: fresh_threshold(invalidations, y) for y in range(years[0], years[1] + 1)}
    pieces = []
    for i, entry in enumerate(entries):
        meta = entry["meta"]
        ids = np.asarray(meta["rcids"], dtype="int64")
        ids = ids[np.fromiter((int(x) in firm_set for x in ids), dtype=bool, count=len(ids))]
        span = np.arange(max(meta["years"][0], years[0]), min(meta["years"][1], years[1]) + 1)
        span = span[[meta["saved_epoch"] >= thresholds[y] for y in span]]
        if len(ids) and len(span):
            pieces.append(pd.DataFrame({"rcid": np.repeat(ids, len(span)), "year": np.tile(span, len(ids)),
                                        "entry": i, "epoch": float(meta["saved_epoch"])}))
    if not pieces:
        return pd.DataFrame({"rcid": pd.Series(dtype="int64"), "year": pd.Series(dtype="int64"),
                             "entry": pd.Series(dtype="int64"), "epoch": pd.Series(dtype="float64")})
    cover = pd.concat(pieces, ignore_index=True).sort_values(["rcid", "year", "epoch", "entry"])
    return cover.drop_duplicates(["rcid", "year"], keep="last").reset_index(drop=True)


def missing_pairs(cover, firms, years):
    need = pd.MultiIndex.from_product([np.asarray(firms, dtype="int64"), np.arange(years[0], years[1] + 1)],
                                      names=["rcid", "year"])
    have = pd.MultiIndex.from_arrays([cover.rcid.to_numpy("int64"), cover.year.to_numpy("int64")],
                                     names=["rcid", "year"])
    return need.difference(have).to_frame(index=False)


def plan_workforce_queries(missing, chunk):
    """Group firms by their missing year runs, then cut each group into chunks.

    First run: every firm misses the full span (one group). Later runs: new
    firms, a new year, or refreshed recent years each form one group.
    """
    if missing.empty:
        return []
    groups = collections.defaultdict(list)
    for rcid, ys in missing.groupby("rcid")["year"]:
        ys = np.sort(ys.to_numpy())
        cuts = np.flatnonzero(np.diff(ys) != 1)
        runs = tuple(zip(np.r_[ys[0], ys[cuts + 1]].tolist(), np.r_[ys[cuts], ys[-1]].tolist()))
        groups[runs].append(int(rcid))
    plan = []
    for runs in sorted(groups):
        firms = sorted(groups[runs])
        for i in range(0, len(firms), chunk):
            plan.extend((firms[i:i + chunk], y0, y1) for y0, y1 in runs)
    return plan


def workforce_missing_mask(cover, firms, years):
    """Compact coverage bookkeeping: one byte per possible firm-year.

    Existing cache coverage includes queries that returned no row. This mask
    uses those requested keys, rather than treating absent data as a failed pull.
    Slice the cache keys so temporary index arrays also have bounded size.
    """
    ids = np.unique(np.asarray(firms, dtype="int64"))
    n_years = years[1] - years[0] + 1
    if n_years < 1:
        raise ValueError("Workforce years must be in ascending order")
    missing = np.ones((len(ids), n_years), dtype=bool)
    if not len(ids) or cover.empty:
        return ids, missing
    for first in range(0, len(cover), 100000):
        piece = cover.iloc[first:first + 100000]
        rcids = piece.rcid.to_numpy(dtype="int64")
        offsets = piece.year.to_numpy(dtype="int64") - years[0]
        rows = np.searchsorted(ids, rcids)
        valid = (rows < len(ids)) & (offsets >= 0) & (offsets < n_years)
        rows, offsets, rcids = rows[valid], offsets[valid], rcids[valid]
        match = ids[rows] == rcids
        missing[rows[match], offsets[match]] = False
    return ids, missing


def plan_missing_workforce(cover, firms, years, chunk):
    """Make the same ordered SQL batches without a full table of missing pairs."""
    if chunk < 1 or years[1] < years[0]:
        raise ValueError("Invalid workforce batch size or year range")
    ids = np.unique(np.asarray(firms, dtype="int64"))
    if not len(ids):
        return []
    if cover.empty:
        # All firms need the same span: no Cartesian product or per-firm loop.
        return [(ids[i:i + chunk].tolist(), years[0], years[1]) for i in range(0, len(ids), chunk)]
    ids, missing = workforce_missing_mask(cover, ids, years)
    patterns, inverse = np.unique(np.packbits(missing, axis=1), axis=0, return_inverse=True)
    del missing
    order = np.argsort(inverse, kind="stable")
    ends = np.r_[0, np.cumsum(np.bincount(inverse, minlength=len(patterns)))]
    groups = []
    for pattern_id, pattern in enumerate(patterns):
        offsets = np.flatnonzero(np.unpackbits(pattern)[:years[1] - years[0] + 1])
        if not len(offsets):
            continue
        ys = offsets + years[0]
        cuts = np.flatnonzero(np.diff(ys) != 1)
        runs = tuple(zip(np.r_[ys[0], ys[cuts + 1]].tolist(), np.r_[ys[cuts], ys[-1]].tolist()))
        # Stable inverse sorting retains ascending firm IDs within each group.
        group_ids = ids[order[ends[pattern_id]:ends[pattern_id + 1]]]
        groups.append((runs, group_ids))
    plan = []
    # Preserve the original ordering so SQL strings and cache keys stay identical.
    for runs, group_ids in sorted(groups, key=lambda x: x[0]):
        for first in range(0, len(group_ids), chunk):
            batch = group_ids[first:first + chunk].tolist()
            plan.extend((batch, y0, y1) for y0, y1 in runs)
    return plan


def count_missing_workforce(cover, firms, years):
    """Check completion without rebuilding the Cartesian firm-year table."""
    if cover.empty:
        return len(np.unique(np.asarray(firms, dtype="int64"))) * (years[1] - years[0] + 1)
    _, missing = workforce_missing_mask(cover, firms, years)
    return int(np.count_nonzero(missing))


def assemble_workforce(entries, cover):
    parts = [entries[i]["frame"].merge(keys, on=["rcid", "year"], how="inner")
             for i, keys in cover.groupby("entry")[["rcid", "year"]]]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame({c: pd.Series(dtype="int64" if c in ("rcid", "year") else "float64")
                             for c in WF_COLUMNS})
    return pd.concat(parts, ignore_index=True)[WF_COLUMNS].sort_values(["rcid", "year"]).reset_index(drop=True)


def changed_years(cached, fresh):
    """Calendar years whose firm-year rows differ between two pulls (keys or values)."""
    keys = ["rcid", "year"]
    a, b = cached.set_index(keys).sort_index(), fresh.set_index(keys).sort_index()
    years = set(a.index.symmetric_difference(b.index).get_level_values("year"))
    common = a.index.intersection(b.index)
    x = a.loc[common, WF_COLUMNS[2:]].to_numpy(dtype=float)
    y = b.loc[common, WF_COLUMNS[2:]].to_numpy(dtype=float)
    differs = ~np.isclose(x, y, rtol=1e-9, atol=1e-9, equal_nan=True).all(axis=1)
    years |= set(common[differs].get_level_values("year"))
    return sorted(int(v) for v in years)


def probe_workforce(cache, cfg, schema, family, years, cover, entries):
    """Re-query a spread of fully cached firms; returns (changed years, new cache entry)."""
    counts = cover.groupby("rcid").size()
    complete = sorted(int(x) for x in counts.index[counts == years[1] - years[0] + 1])
    if not complete:
        return None, None
    probe = complete[::max(1, len(complete) // cfg.probe_firms)][:cfg.probe_firms]
    cached = assemble_workforce(entries, cover[cover.rcid.isin(probe)])
    fresh, meta = cache.query("workforce", workforce_sql(probe, years[0], years[1], cfg, schema),
                              f"Probe {len(probe)} firms for WRDS updates",
                              {"family": family, "rcids": probe, "years": list(years)})
    return changed_years(cached, fresh), {"meta": meta, "frame": fresh}


def pull_workforce(db, cache, cfg, schema, firms, years, recent_from, refresh=False):
    family = workforce_family(cfg, schema)
    fam = cache.state.setdefault("workforce", {}).setdefault(family, {"invalidations": [], "checked_epoch": 0.0})
    if refresh:
        fam["invalidations"].append([0, time.time()])
        cache.save_state()
    db.set("enable_seqscan", "off")  # favour the (rcid, datemonth) index for every workforce query
    with local_progress("Read and verify saved workforce chunks"):
        entries = load_workforce_entries(cache, family)
    with local_progress("Identify cached workforce years"):
        cover = choose_workforce_sources(entries, firms, years, fam["invalidations"])
    info = {"workforce_family": family, "workforce_firmyears_cached_at_start": int(len(cover))}
    started_empty = cover.empty
    if not refresh and not cover.empty and time.time() - fam["checked_epoch"] > RECENT_CACHE_MAX_AGE_HOURS * 3600:
        started = time.time()
        changed, probe_entry = probe_workforce(cache, cfg, schema, family, years, cover, entries)
        if probe_entry is not None:
            entries.append(probe_entry)
        if changed is None:
            info["workforce_probe"] = "skipped (no fully cached firm yet)"
        elif not changed:
            info["workforce_probe"] = "unchanged"
            logging.info("Probe: WRDS workforce data match the cache.")
        else:
            info["workforce_probe"] = {"changed_years": changed}
            if max(changed) >= recent_from:
                fam["invalidations"].append([recent_from, started])
                logging.info("Probe: WRDS changed workforce years %s; re-pulling %d onwards for all firms.",
                             changed, recent_from)
            if min(changed) < recent_from:
                logging.warning("Probe: WRDS also changed years before %d (%s). Rerun with --refresh "
                                "to rebuild every year from one WRDS vintage.", recent_from,
                                [y for y in changed if y < recent_from])
        if changed is not None:
            fam["checked_epoch"] = time.time()
        cache.save_state()
        with local_progress("Update cached workforce years after freshness check"):
            cover = choose_workforce_sources(entries, firms, years, fam["invalidations"])
    with local_progress("Plan missing workforce downloads"):
        plan = plan_missing_workforce(cover, firms, years, cfg.firms_per_query)
    info.update({"workforce_queries_planned": len(plan), "workforce_firmyears_needed":
                 len(firms) * (years[1] - years[0] + 1)})
    if plan:
        ids, y0, y1 = plan[0]
        info["workforce_query_plan"] = check_workforce_plan(db, cfg, workforce_sql(ids, y0, y1, cfg, schema), len(ids))
        logging.info("Workforce: %s of %s firm-years cached; %d queries of up to %d firms to run.",
                     f"{len(cover):,}", f"{info['workforce_firmyears_needed']:,}", len(plan), cfg.firms_per_query)
        clock = time.monotonic()
        for i, (ids, y0, y1) in enumerate(plan, 1):
            frame, meta = cache.query("workforce", workforce_sql(ids, y0, y1, cfg, schema),
                                      f"Workforce {i}/{len(plan)} ({len(ids)} firms, {y0}-{y1})",
                                      {"family": family, "rcids": ids, "years": [y0, y1]})
            entries.append({"meta": meta, "frame": frame})
            spent = time.monotonic() - clock
            logging.info("Workforce progress: %d/%d queries in %.1f min; about %.0f min to go at the running average.",
                         i, len(plan), spent / 60, spent / i * (len(plan) - i) / 60)
        if started_empty or refresh:
            fam["checked_epoch"] = time.time()
            cache.save_state()
    else:
        logging.info("Workforce: all %s needed firm-years come from the cache.", f"{len(cover):,}")
    with local_progress("Verify final workforce coverage"):
        cover = choose_workforce_sources(entries, firms, years, fam["invalidations"])
        left = count_missing_workforce(cover, firms, years)
    if left:
        raise RuntimeError(f"{left:,} workforce firm-years remain uncovered; rerun to complete them.")
    with local_progress("Assemble downloaded workforce data"):
        workforce = assemble_workforce(entries, cover)
    return workforce, info


# %% Industry: NAICS per workforce firm, with independently saved mapping batches
def industry_sql(ids, cfg):
    """Modal NAICS code per rcid. company_mapping can hold several rows per rcid
    (one per child entity), so the most frequent code is kept and the number of
    distinct codes is reported; ties go to the lowest code."""
    ids = [int(x) for x in ids]
    if not ids:
        raise ValueError("Empty industry ID list")
    code = 'btrim("naics_code"::text)'
    return (f'SELECT "rcid"::bigint AS rcid, MODE() WITHIN GROUP (ORDER BY {code}) AS naics_code, '
            f"COUNT(DISTINCT {code}) AS naics_n_codes "
            f"FROM {qi(cfg.schema)}.{qi(cfg.company_table)} "
            f'WHERE "rcid" IN ({",".join(map(str, ids))}) AND "naics_code" IS NOT NULL AND {code} <> {lit("")} '
            "GROUP BY 1 ORDER BY 1")


def pull_firm_industry(db, cache, cfg, firms, fresh_after=0.0):
    """Same per-firm NAICS rule; reuse completed batches after interruption.

    An old whole-request checkpoint remains valid. If WRDS plans a sequential
    scan for a small batch, query the remaining IDs once rather than repeat that
    full-table scan for every batch. Batching does not change the NAICS rule.
    """
    firms = sorted(set(int(x) for x in firms))
    if FIRMS_PER_INDUSTRY_QUERY < 1:
        raise ValueError("FIRMS_PER_INDUSTRY_QUERY must be positive")
    empty = pd.DataFrame({"rcid": pd.Series(firms, dtype="int64"),
                          "naics_code": pd.Series([None] * len(firms), dtype=object),
                          "naics_n_codes": pd.Series([0] * len(firms), dtype="int64")})
    if not firms:
        return empty, {"industry_source": "no firms", "industry_firms_coded": 0,
                       "industry_firms_multi_code": 0}
    sql = industry_sql(firms, cfg)
    hit = cache.lookup("industry", sql)
    components = []
    if hit is not None and hit[1]["saved_epoch"] >= fresh_after:
        cache.stats["reused"] += 1
        found = hit[0]
        logging.info("Reuse company mappings: verified complete checkpoint for %s firms.", f"{len(firms):,}")
    else:
        frames, pending = [], []
        batches = [firms[i:i + FIRMS_PER_INDUSTRY_QUERY]
                   for i in range(0, len(firms), FIRMS_PER_INDUSTRY_QUERY)]
        for ids in batches:
            batch_sql = industry_sql(ids, cfg)
            saved = cache.lookup("industry", batch_sql)
            if saved is not None and saved[1]["saved_epoch"] >= fresh_after:
                cache.stats["reused"] += 1
                frames.append(saved[0])
                components.append(saved[1])
            else:
                pending.append(ids)
        logging.info("Company mappings: %d/%d batches reused; %d remaining (up to %d firms each).",
                     len(batches) - len(pending), len(batches), len(pending), FIRMS_PER_INDUSTRY_QUERY)
        # A completed checkpoint needs no live schema inspection or connection.
        if pending:
            try:
                columns = table_metadata(db, cfg.schema, cfg.company_table)
            except TableUnavailableError as exc:
                logging.warning("%s Industry codes stay empty, so the do-file's industry fixed effects are unavailable.", exc)
                return empty, {"industry_source": "unavailable"}
            if not {"rcid", "naics_code"}.issubset(columns):
                logging.warning("%s.%s lacks rcid/naics_code; industry codes stay empty.", cfg.schema, cfg.company_table)
                return empty, {"industry_source": "columns missing"}
        if len(pending) > 1:
            plan = db.query("EXPLAIN " + industry_sql(pending[0], cfg), "Check company-mapping query plan")
            lines = [str(x) for x in plan.iloc[:, 0]]
            if any(re.search(r'Seq Scan on "?' + re.escape(cfg.company_table) + r'\b', line) for line in lines):
                pending = [[rcid for ids in pending for rcid in ids]]
                logging.info("Company-mapping plan scans the source table for each batch; "
                             "querying the remaining %s firms once to avoid repeated full scans.",
                             f"{len(pending[0]):,}")
        for i, ids in enumerate(pending, 1):
            frame, meta = cache.query(
                "industry", industry_sql(ids, cfg),
                f"Company mappings {i}/{len(pending)} ({len(ids):,} firms)",
                {"keys": ["rcid"], "string_columns": ["naics_code"], "rcids": ids})
            frames.append(frame)
            components.append(meta)
        found = pd.concat(frames, ignore_index=True)
    if found.duplicated("rcid").any() or not found.rcid.isin(firms).all():
        raise RuntimeError("Company mappings returned duplicate or unrequested firm IDs")
    if len(components) > 1:
        # MODE is grouped by rcid, so disjoint batches equal the whole SQL.
        # Retain the oldest component time rather than making old data fresh.
        cache.store("industry", sql, found,
                    {"keys": ["rcid"], "string_columns": ["naics_code"],
                     "assembled_from": [meta["key"] for meta in components]},
                    saved_epoch=min(meta["saved_epoch"] for meta in components))
    out = empty[["rcid"]].merge(found, on="rcid", how="left", validate="one_to_one")
    out["naics_n_codes"] = pd.to_numeric(out.naics_n_codes).fillna(0).astype("int64")
    coded = int(out.naics_code.notna().sum())
    logging.info("Industry: NAICS codes for %s of %s workforce firms (%.1f%%); %s firms carry more than one code.",
                 f"{coded:,}", f"{len(out):,}", 100 * coded / max(len(out), 1),
                 f"{int((out.naics_n_codes > 1).sum()):,}")
    return out, {"industry_source": f"{cfg.schema}.{cfg.company_table}", "industry_firms_coded": coded,
                 "industry_firms_multi_code": int((out.naics_n_codes > 1).sum())}


def finish_reviews(raw):
    frame = raw.copy()
    if frame.duplicated(["rcid", "year"]).any():
        raise RuntimeError("Duplicate review firm-years")
    for suffix in ("", "_rf"):
        for col in ("n_words", "n_fpp", "n_tpp", "n_reviews", "n_nis_reviews"):
            frame[col + suffix] = pd.to_numeric(frame[col + suffix], errors="raise").fillna(0).astype("int64")
        frame["nis_1" + suffix] = ((frame["n_fpp" + suffix] - frame["n_tpp" + suffix])
                                  / frame["n_words" + suffix].replace(0, np.nan))
        frame["nis_2" + suffix] = pd.to_numeric(frame["nis_2" + suffix], errors="raise")
    frame["rcid"] = frame.rcid.astype("int64")
    frame["year"] = frame.year.astype("int64")
    return frame.sort_values(["rcid", "year"]).reset_index(drop=True)


def finish_workforce(raw):
    frame = raw.copy()
    if frame.duplicated(["rcid", "year"]).any():
        raise RuntimeError("Duplicate workforce firm-years; ID batches must not overlap")
    frame["rcid"] = frame.rcid.astype("int64")
    frame["year"] = frame.year.astype("int64")
    for col in ("months_scope", "months_hc", "months_outflow", "months_inflow", "months_global_hc"):
        frame[col] = pd.to_numeric(frame[col], errors="raise").fillna(0).astype("int64")
        if not frame[col].between(0, 12).all():
            raise RuntimeError(f"Invalid monthly coverage in {col}")
    complete = ((frame.months_scope == 12) & (frame.months_hc == 12) & (frame.months_outflow == 12)
                & (frame.first_month_scope == 1) & (frame.last_month_scope == 12))
    frame["complete_turnover_year"] = complete.astype("int8")
    frame["headcount_avg"] = frame.headcount_avg_obs.where(frame.months_hc == 12)
    frame["global_headcount_avg"] = frame.global_hc_avg_obs.where(frame.months_global_hc == 12)
    frame["external_outflow"] = frame.external_outflow_obs.where(frame.months_outflow == 12)
    frame["external_inflow"] = frame.external_inflow_obs.where(frame.months_inflow == 12)
    frame["turnover"] = (frame.external_outflow / frame.headcount_avg).where(complete & (frame.headcount_avg > 0))
    frame["turnover_pct"] = frame.turnover * 100
    frame["hiring_rate"] = (frame.external_inflow / frame.headcount_avg).where(
        (frame.months_inflow == 12) & (frame.months_hc == 12) & (frame.headcount_avg > 0))
    return frame.sort_values(["rcid", "year"]).reset_index(drop=True)


def merge_panel(reviews, workforce, cfg):
    panel = reviews.merge(workforce, on=["rcid", "year"], how="left", validate="one_to_one", indicator=True)
    panel["has_workforce_row"] = panel.pop("_merge").eq("both").astype("int8")
    lead_cols = ["turnover", "turnover_pct", "headcount_avg", "external_outflow", "complete_turnover_year"]
    lead = workforce[["rcid", "year"] + lead_cols].copy()
    lead["year"] -= 1  # an explicit calendar key, preserving gaps
    lead = lead.rename(columns={x: x + "_lead1" for x in lead_cols})
    panel = panel.merge(lead, on=["rcid", "year"], how="left", validate="one_to_one")
    panel["eligible_nis"] = ((panel.n_nis_reviews >= cfg.min_reviews) & panel.nis_1.notna()).astype("int8")
    panel["eligible_sameyear"] = (panel.eligible_nis.eq(1) & panel.turnover.notna()
                                  & panel.headcount_avg.ge(cfg.min_headcount)).astype("int8")
    panel["eligible_lead1"] = (panel.eligible_nis.eq(1) & panel.turnover_lead1.notna()
                               & panel.headcount_avg_lead1.ge(cfg.min_headcount)).astype("int8")
    return panel.sort_values(["rcid", "year"]).reset_index(drop=True)


# %% Exports and dictionary
DEFINITIONS = {
    "rcid": "Revelio firm identifier, same entity level in reviews and workforce; no parent rollup.",
    "year": "Calendar year of the review, or of the workforce record in workforce_firmyear.",
    "company": "One source company name per review firm-year (lexicographic MAX; descriptive only).",
    "nis_1": "Pooled-word NIS: (total FPP - total TPP) / total whitespace-delimited words.",
    "nis_2": "Unweighted mean of review-level (FPP - TPP) / words; empty texts excluded.",
    "n_reviews": "All eligible reviews, including any with empty selected text fields.",
    "n_nis_reviews": "Eligible reviews with at least one word in the selected text fields.",
    "n_words": "Total whitespace-delimited words in selected review fields.",
    "n_fpp": "Total whole-word we/us/our/ours/ourselves occurrences (audit component).",
    "n_tpp": "Total whole-word they/them/their/theirs/themselves occurrences (audit component).",
    "months_scope": "Distinct months with at least one workforce cell in the selected population.",
    "months_hc": "Months with nonmissing nonnegative headcount in every supplied scope cell.",
    "months_outflow": "Months with nonmissing nonnegative external outflow in every supplied scope cell.",
    "months_inflow": "Months with nonmissing nonnegative external inflow in every supplied scope cell.",
    "months_global_hc": "Months with valid nonnegative headcount in every supplied global cell.",
    "first_month_scope": "First observed calendar month in the selected workforce population.",
    "last_month_scope": "Last observed calendar month in the selected workforce population.",
    "headcount_avg_obs": "Mean scope headcount over observed valid months; may describe a partial year.",
    "global_hc_avg_obs": "Mean global headcount over observed valid months; may describe a partial year.",
    "external_outflow_obs": "Sum of valid scope monthly external outflows; may describe a partial year.",
    "external_inflow_obs": "Sum of valid scope monthly external inflows; may describe a partial year.",
    "headcount_avg": "Mean monthly scope headcount, only with 12 valid months.",
    "global_headcount_avg": "Mean monthly global headcount, only with 12 valid months.",
    "external_outflow": "Annual external departures, only with 12 valid outflow months.",
    "external_inflow": "Annual external hires, only with 12 valid inflow months.",
    "complete_turnover_year": "1 = 12 scope months, each with valid headcount and external outflow.",
    "turnover": "Annual external departures / mean monthly headcount; complete years and positive denominator only.",
    "turnover_pct": "100 times turnover; the separate turnover variable is a fraction.",
    "hiring_rate": "Annual external inflow / mean monthly headcount; complete valid months only.",
    "has_workforce_row": "1 = this review firm-year has a workforce extract row; 0 = unmatched.",
    "workforce_firm": "1 = review firm included in the workforce query; no review-count threshold.",
    "naics_code": "NAICS code from Revelio company_mapping (modal code over the firm's rows); empty if unmapped.",
    "naics_n_codes": "Distinct NAICS codes on the firm's company_mapping rows (1 = unambiguous).",
    "eligible_nis": "1 = n_nis_reviews >= configured threshold and pooled NIS observed; flag only.",
    "eligible_sameyear": "1 = eligible NIS and observed same-year turnover/headcount above threshold.",
    "eligible_lead1": "1 = eligible NIS and observed next-year turnover/headcount above threshold.",
    "seniority_mean": "Mean reviewer seniority grade (1-7); not an annual employee census.",
    "tenure_mean": "Mean reviewer reported tenure in years; values outside 0-100 treated as missing.",
}


def definition(name):
    if name.endswith("_lead1"):
        return definition(name[:-6]) + " Measured in exact calendar year t+1 relative to reviews."
    if name.endswith("_rf"):
        return definition(name[:-3]) + " Uses only reviewer seniority grades 1-4."
    if name in DEFINITIONS:
        return DEFINITIONS[name]
    if name in STAR_FIELDS:
        return "Mean of eligible reviewers' 1-5 rating; invalid/missing codes excluded."
    if name in CATEGORY_FIELDS:
        return "Mean mapped category: positive/yes/approve=1, neutral/no opinion=0.5, negative/no/disapprove=0; unknown codes missing."
    if name.startswith("n_") and name[2:] in MEAN_FIELDS:
        return "Number of eligible reviews with a valid value for " + name[2:] + "."
    return name


def export_frame(frame, root, stem):
    if not frame.columns.is_unique or any(len(x) > 32 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x) for x in frame.columns):
        raise RuntimeError("Output variable names must be unique Stata-compatible identifiers of at most 32 characters")
    out = frame.copy()
    for col in out.columns:
        if col in STRING_COLUMNS:
            out[col] = out[col].astype(object).where(out[col].notna(), "").astype(str)
        elif out[col].dtype == "object":
            out[col] = pd.to_numeric(out[col], errors="raise")
    if np.isinf(out.select_dtypes(include=[np.number]).to_numpy(dtype=float)).any():
        raise RuntimeError("Infinite value in export; inspect the source metrics.")
    csv_path = root / (stem + ".csv.gz")
    temp_csv = csv_path.with_name(csv_path.name + ".tmp")
    out.to_csv(temp_csv, index=False, compression="gzip")
    os.replace(temp_csv, csv_path)
    dta_path = root / (stem + ".dta")
    temp_dta = dta_path.with_name(dta_path.name + ".tmp")
    out.to_stata(temp_dta, write_index=False, version=118,
                 variable_labels={c: definition(c)[:80] for c in out.columns},
                 data_label="Revelio Glassdoor NIS and actual external turnover")
    os.replace(temp_dta, dta_path)
    logging.info("Saved %s (%s rows).", dta_path, f"{len(out):,}")


def coverage_table(panel):
    return panel.groupby("year", as_index=False).agg(
        firms=("rcid", "nunique"), reviews=("n_reviews", "sum"), nis_reviews=("n_nis_reviews", "sum"),
        firms_with_nis=("nis_1", "count"), firms_with_workforce=("has_workforce_row", "sum"),
        firms_with_turnover=("turnover", "count"), firms_with_turnover_lead1=("turnover_lead1", "count"),
        eligible_sameyear=("eligible_sameyear", "sum"), eligible_lead1=("eligible_lead1", "sum"))


# %% Offline tests: no WRDS account required
def self_test():
    all_review_firms = pd.DataFrame({"rcid": [1, 2, 3], "n_nis_reviews": [1, 19, 20]})
    assert workforce_firms(all_review_firms) == [1, 2, 3]
    assert reference_counts("We like our team. They respect us.") == (7, 3, 1)
    assert reference_counts("we\twork\nwith them") == (4, 1, 1)
    assert reference_counts("awesome neither") == (2, 0, 0)
    assert reference_counts("") == (0, 0, 0)
    cfg = Settings(min_reviews=1, min_headcount=1)
    raw = pd.DataFrame({"rcid": [1, 1, 2], "year": [2020, 2022, 2020], "company": ["A", "A", "B"],
        "n_reviews": [2, 1, 1], "n_nis_reviews": [2, 1, 0], "n_words": [12, 2, 0],
        "n_fpp": [2, 0, 0], "n_tpp": [0, 1, 0], "nis_2": [(1/2 + 1/10)/2, -1/2, np.nan],
        "n_reviews_rf": [1, 0, 0], "n_nis_reviews_rf": [1, 0, 0], "n_words_rf": [2, 0, 0],
        "n_fpp_rf": [1, 0, 0], "n_tpp_rf": [0, 0, 0], "nis_2_rf": [1/2, np.nan, np.nan]})
    reviews = finish_reviews(raw)
    assert np.isclose(reviews.iloc[0].nis_1, 1/6) and np.isclose(reviews.iloc[0].nis_2, .3)
    assert pd.isna(reviews.loc[reviews.rcid.eq(2), "nis_1"]).all()
    wf = pd.DataFrame({"rcid": [1, 1, 1, 2], "year": [2020, 2022, 2023, 2021],
        "months_scope": [12, 11, 12, 12], "months_hc": [12, 11, 12, 12],
        "months_outflow": [12, 11, 12, 12], "months_inflow": [0, 11, 12, 12],
        "months_global_hc": [12, 11, 12, 12], "headcount_avg_obs": [100., 100., 100., 0.],
        "global_hc_avg_obs": [150., 150., 150., 0.], "external_outflow_obs": [24., 22., 130., 0.],
        "external_inflow_obs": [np.nan, 20., 25., 0.], "first_month_scope": [1, 1, 1, 1],
        "last_month_scope": [12, 11, 12, 12]})
    workforce = finish_workforce(wf)
    assert np.isclose(workforce.iloc[0].turnover, .24)  # no inflow required
    assert pd.isna(workforce.iloc[1].turnover)  # partial year
    assert np.isclose(workforce.iloc[2].turnover, 1.3)  # no winsorizing/capping
    assert pd.isna(workforce.iloc[3].turnover)  # zero denominator
    panel = merge_panel(reviews, workforce, cfg)
    assert len(panel) == len(reviews)
    assert pd.isna(panel.loc[(panel.rcid == 1) & (panel.year == 2020), "turnover_lead1"]).all()
    assert np.isclose(panel.loc[(panel.rcid == 1) & (panel.year == 2022), "turnover_lead1"].iloc[0], 1.3)
    assert panel.loc[panel.rcid == 2, "has_workforce_row"].iloc[0] == 0  # retain unmatched review year
    assert panel.loc[panel.rcid == 2, "headcount_avg_lead1"].iloc[0] == 0

    class FakeDB:
        def __init__(self):
            self.calls = 0

        def query(self, sql, label):
            self.calls += 1
            return pd.DataFrame({"rcid": [3], "year": [2020], "value": [1/3]})

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fake = FakeDB()
        cache = Cache(root / "cache", fake)
        x = fetch_review_year(cache, 2020, "SELECT test", 0.0)
        y = fetch_review_year(cache, 2020, "SELECT test", 0.0)
        assert fake.calls == 1 and np.isclose(x.value.iloc[0], y.value.iloc[0])
        fetch_review_year(cache, 2020, "SELECT other", 0.0)
        assert fake.calls == 2  # different SQL, different cache entry
        fetch_review_year(cache, 2020, "SELECT test", time.time() + 1)
        assert fake.calls == 3  # older than the freshness threshold: queried again
        cache.paths("reviews", Cache.key("SELECT other"))[0].write_bytes(b"broken")
        fetch_review_year(cache, 2020, "SELECT other", 0.0)
        assert fake.calls == 4  # the damaged file is replaced rather than trusted
        export_frame(panel, root, "test")
        roundtrip = pd.read_stata(root / "test.dta", convert_categoricals=False)
        assert len(roundtrip) == len(panel) and np.allclose(roundtrip.nis_1, panel.nis_1, equal_nan=True)
        coverage_table(panel).to_csv(root / "coverage.csv", index=False)

    # Workforce coverage: the newest current query wins per firm-year, including absent rows.
    def wf_rows(pairs, value):
        rows = [dict({c: 12 for c in WF_COLUMNS}, rcid=r, year=yr, headcount_avg_obs=value) for r, yr in pairs]
        return pd.DataFrame(rows, columns=WF_COLUMNS)

    old_entry = {"meta": {"rcids": [1, 2], "years": [2019, 2021], "saved_epoch": 100.0},
                 "frame": wf_rows([(1, 2019), (1, 2020), (1, 2021), (2, 2020), (2, 2021)], 1.0)}
    new_entry = {"meta": {"rcids": [2], "years": [2021, 2022], "saved_epoch": 200.0},
                 "frame": wf_rows([(2, 2022)], 2.0)}  # firm 2's 2021 row has disappeared at WRDS
    entries = [old_entry, new_entry]
    cover = choose_workforce_sources(entries, [1, 2, 3], (2019, 2022), [])
    got = assemble_workforce(entries, cover).set_index(["rcid", "year"]).headcount_avg_obs.to_dict()
    assert got == {(1, 2019): 1.0, (1, 2020): 1.0, (1, 2021): 1.0, (2, 2020): 1.0, (2, 2022): 2.0}
    missing = missing_pairs(cover, [1, 2, 3], (2019, 2022))  # firm 2's empty 2019 counts as covered
    assert sorted(map(tuple, missing.values.tolist())) == [(1, 2022), (3, 2019), (3, 2020), (3, 2021), (3, 2022)]
    plan = plan_workforce_queries(missing, 2)
    assert sorted((tuple(ids), a, b) for ids, a, b in plan) == [((1,), 2022, 2022), ((3,), 2019, 2022)]
    stale = choose_workforce_sources(entries, [1, 2], (2019, 2022), [[2021, 150.0]])
    assert sorted(map(tuple, missing_pairs(stale, [1, 2], (2019, 2022)).values.tolist())) == [(1, 2021), (1, 2022)]
    many = pd.DataFrame({"rcid": np.repeat([10, 11, 12, 13, 14], 2), "year": np.tile([2019, 2020], 5)})
    assert [(ids, a, b) for ids, a, b in plan_workforce_queries(many, 2)] == [
        ([10, 11], 2019, 2020), ([12, 13], 2019, 2020), ([14], 2019, 2020)]
    a = wf_rows([(1, 2019), (1, 2020)], 1.0)
    b = a.copy()
    b.loc[1, "headcount_avg_obs"] = 1.5
    assert changed_years(a, a.copy()) == [] and changed_years(a, b) == [2020]
    assert changed_years(a, b.iloc[:1]) == [2020]

    # Check the generated SQL, with fake metadata for a native-date installation.
    r = {k: v[0] for k, v in REVIEW_ALIASES.items()}
    types = {v: "text" for v in r.values()}
    types[r["date"]] = "timestamp without time zone"
    schema = {"reviews": r, "review_types": types,
              "workforce": {"rcid": "rcid", "date": "datemonth", "country": "country", "hc": "count",
                            "outflow": "external_outflow", "inflow": "external_inflow"},
              "workforce_types": {"datemonth": "date"}}
    sql = review_sql(2020, cfg, schema)
    assert "[[:space:]]+" in sql and qi(r["summary"]) not in sql
    assert qi(r["summary"]) in review_sql(2020, dataclasses.replace(cfg, include_summary=True), schema)
    wsql = workforce_sql([1, 2], 2019, 2021, cfg, schema)
    assert "IN (1,2)" in wsql and "2019-01-01" in wsql and "2022-01-01" in wsql and "external_outflow" in wsql
    isql = industry_sql([5, 6], cfg)
    assert "IN (5,6)" in isql and "MODE() WITHIN GROUP" in isql
    assert "%" not in sql + wsql + isql  # psycopg2 would read % as a parameter marker
    with tempfile.TemporaryDirectory() as td:
        class CodeDB:
            def query(self, sql, label):
                return pd.DataFrame({"rcid": [5], "naics_code": ["031"], "naics_n_codes": [2]})
        codes = Cache(Path(td), CodeDB()).query("industry", isql, "codes",
                                                {"keys": ["rcid"], "string_columns": ["naics_code"]})[0]
        assert codes.naics_code.iloc[0] == "031"  # leading zero kept as text
    assert workforce_family(cfg, schema) != workforce_family(dataclasses.replace(cfg, scope="GLOBAL"), schema)
    assert qi("review_date") in sql and "2021-01-01" in sql
    assert lit(r"\mwe\M") == "E'\\\\mwe\\\\M'"

    class PlanDB:
        def __init__(self, lines):
            self.lines = lines

        def query(self, sql, label):
            return pd.DataFrame({"QUERY PLAN": self.lines})

    assert check_workforce_plan(PlanDB(["Finalize GroupAggregate", "  ->  Parallel Index Scan using "
                                        "workforce_dynamics_geo_rcid_datemonth_idx on workforce_dynamics_geo"]),
                                cfg, wsql, 2)
    try:
        check_workforce_plan(PlanDB(["Gather", "  ->  Parallel Seq Scan on workforce_dynamics_geo"]), cfg, wsql, 2)
        raise AssertionError("Full-table plan accepted")
    except RuntimeError:
        pass
    try:
        qi("table; DROP TABLE x")
        raise AssertionError("Unsafe identifier accepted")
    except ValueError:
        pass
    print("Offline checks passed: word counts; NIS weighting/empty text; incomplete years; missing inflow; "
          "zero headcount; calendar leads; unmatched firms; cache reuse/integrity; workforce coverage, "
          "freshness and query planning; probe comparison; plan check; Stata round-trip; SQL generation.")


# %% Run all available history



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--scope", choices=("US", "GLOBAL"), default=COUNTRY_SCOPE)
    parser.add_argument("--start-year", type=int, default=START_YEAR)
    parser.add_argument("--end-year", type=int, default=END_YEAR)
    parser.add_argument("--username", default=WRDS_USERNAME)
    parser.add_argument("--refresh", action="store_true", default=SETTINGS["wrds"]["refresh"],
                        help="Re-query everything for this configuration; an interrupted refresh resumes on the next run")
    parser.add_argument("--check-only", action="store_true",
                        help="Connect, inspect tables/history, test SQL counts and the workforce query plan; no extraction")
    parser.add_argument("--self-test", action="store_true", help="Run meaningful offline checks without connecting to WRDS")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return
    data_dir = args.output_dir.expanduser().resolve()
    if args.scope == "GLOBAL":
        data_dir = data_dir / "global"
    reports_dir = REPORTS_BASE / args.scope.lower()
    cfg = Settings(scope=args.scope, start_year=args.start_year, end_year=args.end_year)
    manifest_name = "check_manifest.json" if args.check_only else "run_manifest.json"
    if cfg.firms_per_query < 1 or cfg.probe_firms < 1 or cfg.min_reviews < 1 or cfg.min_headcount < 0:
        raise ValueError("Invalid batch size, probe size or diagnostic flag settings in CONFIG")
    if cfg.start_year is not None and cfg.end_year is not None and cfg.start_year > cfg.end_year:
        raise ValueError("START_YEAR exceeds END_YEAR")
    for y in (cfg.start_year, cfg.end_year):
        if y is not None and not (1900 <= y <= datetime.now(timezone.utc).year + 1):
            raise ValueError("Requested review years must lie between 1900 and next year")
    # Console logging begins before login and review date discovery.
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True,
                        handlers=[logging.StreamHandler(), logging.FileHandler(
                            LOGS_DIR / f"05_revelio_{args.scope.lower()}.log", encoding="utf-8")])
    db = Database(args.username)
    manifest = None
    root = None
    try:
        schema = resolve_schema(db, cfg)
        base = CACHE_BASE / cfg.scope.lower()
        cache = Cache(base / "cache", db)
        if args.refresh and not args.check_only:
            cache.state["reviews_invalidated_epoch"] = time.time()
            cache.save_state()
        bounds = {"reviews": discover_review_history(db, cfg, schema, cache, refresh=args.refresh)}
        live_count_check(db)
        category_codes = inspect_categories(db, cfg, schema)
        indexes = workforce_indexes(db, cfg)
        # Outputs depend on every setting except query batching; the cache is shared across them all.
        output_settings = {k: v for k, v in dataclasses.asdict(cfg).items()
                           if k not in ("firms_per_query", "probe_firms")}
        semantic = {"version": VERSION, "settings": output_settings, "resolved_schema": schema,
                    "pronouns": {"fpp": FPP, "tpp": TPP}, "word_count": "collapsed POSIX whitespace",
                    "source_tables": [cfg.reviews_table, cfg.workforce_table]}
        fingerprint = digest(semantic)[:16]
        root = data_dir
        root.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        logging.info("Output folder: %s", root)
        for d in indexes:
            logging.info("Workforce index: %s", d)
        if not any(re.search(r'\(\s*"?rcid"?\s*[,)]', d) for d in indexes):
            logging.warning("No visible workforce index starts with rcid. The query-plan check before the "
                            "workforce pull shows whether Postgres can avoid full-table scans.")
        lo = max(pd.Timestamp(bounds["reviews"]["first"]).year, cfg.start_year or 1900)
        hi = min(pd.Timestamp(bounds["reviews"]["last"]).year, cfg.end_year or 9999)
        if lo > hi:
            raise RuntimeError("Requested years do not overlap the eligible review history")
        workforce_years = (lo - 1, hi + 1)
        manifest = {"status": "checked" if args.check_only else "running", "started_at": utc_now(),
                    "configuration_fingerprint": fingerprint, "semantic_configuration": semantic,
                    "discovered_history": bounds, "review_years": [lo, hi], "category_sample_codes": category_codes,
                    "workforce_years": list(workforce_years), "workforce_indexes": indexes,
                    "source_definitions": SOURCES, "python": sys.version, "pandas": pd.__version__,
                    "word_count_live_sql_check": "passed", "scope_note": "Review geography and workforce geography match; HQ country does not select the sample.",
                    "cache_dir": str(cache.dir), "data_dir": str(root), "reports_dir": str(reports_dir),
                    "cache_note": ("Shared cache keyed by SQL. The two most recent review years refresh after the configured "
                                   "age; workforce recent years refresh when a daily probe finds WRDS changes; "
                                   "--refresh re-queries everything."),
                    "refresh_requested": args.refresh}
        atomic_json(reports_dir / manifest_name, manifest)
        if args.check_only:
            sample = sample_review_firms(db, cfg, schema, cfg.firms_per_query)
            db.set("enable_seqscan", "off")
            manifest["workforce_query_plan"] = check_workforce_plan(
                db, cfg, workforce_sql(sample, workforce_years[0], workforce_years[1], cfg, schema), len(sample))
            atomic_json(reports_dir / manifest_name, manifest)
            logging.info("Checks complete. Run without --check-only to extract %d-%d review years.", lo, hi)
            return
        latest_r = pd.Timestamp(bounds["reviews"]["last"]).year
        recent_from = latest_r - RECENT_YEARS_TO_REFRESH + 1
        now = time.time()
        invalidated = float(cache.state.get("reviews_invalidated_epoch", 0.0))
        review_frames = []
        for year in range(lo, hi + 1):
            recent = now - RECENT_CACHE_MAX_AGE_HOURS * 3600 if year >= recent_from else 0.0
            review_frames.append(fetch_review_year(cache, year, review_sql(year, cfg, schema),
                                                   max(invalidated, recent)))
        with local_progress("Combine review years and calculate NIS"):
            raw_reviews = pd.concat(review_frames, ignore_index=True)
            if raw_reviews.empty:
                raise RuntimeError("No eligible reviews in selected years. Check filters and available history.")
            reviews = finish_reviews(raw_reviews)
            del raw_reviews, review_frames  # release redundant copies before export/workforce planning
        with local_progress("Save review firm-year files"):
            export_frame(reviews, root, "reviews_firmyear")
        firms = workforce_firms(reviews)
        if not firms:
            raise RuntimeError("No review firms were available for the workforce query.")
        manifest.update({"review_firms": int(reviews.rcid.nunique()), "workforce_firms": len(firms),
                         "workforce_firm_rule": "ALL review firms; no review-count threshold",
                         "diagnostic_flags_only": {"minimum_reviews": cfg.min_reviews, "minimum_headcount": cfg.min_headcount},
                         "workforce_firm_ids_sha256": digest(firms), "workforce_metrics": schema["workforce"]})
        atomic_json(reports_dir / manifest_name, manifest)
        logging.info("All %s review firms enter the workforce pull (%d-%d); no review-count threshold.",
                     f"{len(firms):,}", *workforce_years)
        raw_workforce, workforce_info = pull_workforce(db, cache, cfg, schema, firms, workforce_years,
                                                       recent_from, refresh=args.refresh)
        manifest.update(workforce_info)
        industry, industry_info = pull_firm_industry(db, cache, cfg, firms, fresh_after=invalidated)
        manifest.update(industry_info)
        workforce = finish_workforce(raw_workforce)
        export_frame(workforce, root, "workforce_firmyear")
        panel = merge_panel(reviews, workforce, cfg)
        panel["workforce_firm"] = panel.rcid.isin(firms).astype("int8")
        panel = panel.merge(industry, on="rcid", how="left", validate="many_to_one")
        export_frame(panel, root, "analysis_nis_turnover")
        coverage = coverage_table(panel)
        coverage.to_csv(reports_dir / "coverage_by_year.csv", index=False)
        all_cols = list(dict.fromkeys(list(reviews.columns) + list(workforce.columns) + list(panel.columns)))
        pd.DataFrame({"variable": all_cols, "definition": [definition(c) for c in all_cols]}).to_csv(
            reports_dir / "measure_dictionary.csv", index=False)
        manifest.update({"status": "complete", "finished_at": utc_now(), "review_firmyears": len(reviews),
                         "workforce_firmyears": len(workforce), "panel_firmyears": len(panel),
                         "sameyear_turnover_nonmissing": int(panel.turnover.notna().sum()),
                         "lead1_turnover_nonmissing": int(panel.turnover_lead1.notna().sum()),
                         "cache_stats": dict(cache.stats), "output_columns": all_cols})
        atomic_json(reports_dir / manifest_name, manifest)
        print("\n" + coverage.to_string(index=False))
        print(f"\nSaved {root / 'analysis_nis_turnover.dta'}")
        print(f"Individual Glassdoor reviews contributing to NIS in the saved DTA: {int(panel['n_nis_reviews'].sum()):,}")
        print(f"WRDS queries this run: {cache.stats['queried']} (reused {cache.stats['reused']} verified chunks from this package).")
        print("All review firm-years retained. Diagnostic count flags do not filter the Stata models.")
    except (Exception, KeyboardInterrupt) as exc:
        if manifest is not None:
            manifest.update({"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                             "stopped_at": utc_now(), "error": str(exc)})
            atomic_json(reports_dir / manifest_name, manifest)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
