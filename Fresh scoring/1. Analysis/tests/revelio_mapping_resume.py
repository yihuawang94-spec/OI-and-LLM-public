"""Offline regressions for bounded company mappings and interrupted downloads.

Run with Python from any directory. Uses synthetic firms and a temporary cache;
never connects to WRDS or reads participant data.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import importlib.util
import re
import sys
import tempfile
import time
import unittest

import pandas as pd

ANALYSIS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ANALYSIS))
spec = importlib.util.spec_from_file_location("revelio_mapping_under_test", ANALYSIS / "05_download_revelio.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def requested_ids(sql):
    match = re.search(r'WHERE "rcid" IN \(([0-9,]+)\)', sql)
    if not match:
        raise AssertionError("Expected bounded RCID query")
    return [int(x) for x in match.group(1).split(",")]


def rows(ids):
    # Firm 7 has no valid mapping; firm 2 has two distinct codes. Codes stay text.
    values = [(x, "002" if x == 2 else "031", 2 if x == 2 else 1) for x in ids if x != 7]
    return pd.DataFrame(values, columns=["rcid", "naics_code", "naics_n_codes"])


class FixtureDB:
    def __init__(self, *, sequential=False, fail_at=None, forbid=False):
        self.sequential, self.fail_at, self.forbid = sequential, fail_at, forbid
        self.data_calls, self.metadata_calls, self.plan_calls = [], 0, 0

    def query(self, sql, label):
        if self.forbid:
            raise AssertionError("A completed cache must require no database calls")
        if "information_schema.columns" in sql:
            self.metadata_calls += 1
            return pd.DataFrame({"column_name": ["rcid", "naics_code"], "data_type": ["bigint", "text"]})
        if sql.startswith("EXPLAIN "):
            self.plan_calls += 1
            plan = "Seq Scan on company_mapping" if self.sequential else "Index Scan using company_rcid_idx on company_mapping"
            return pd.DataFrame({"QUERY PLAN": [plan]})
        ids = requested_ids(sql)
        self.data_calls.append(ids)
        if self.fail_at == len(self.data_calls):
            raise RuntimeError("Synthetic second-batch interruption")
        return rows(ids)


class MappingResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.cfg = m.Settings()
        self.firms = list(range(1, 2502))
        self.batch_patch = mock.patch.object(m, "FIRMS_PER_INDUSTRY_QUERY", 1000)
        self.batch_patch.start()
        self.addCleanup(self.batch_patch.stop)

    def cache(self, db):
        return m.Cache(self.directory, db)

    def check_output(self, output):
        self.assertEqual(output.rcid.tolist(), self.firms)
        self.assertEqual(output.loc[output.rcid == 2, "naics_code"].item(), "002")
        self.assertEqual(output.loc[output.rcid == 2, "naics_n_codes"].item(), 2)
        self.assertTrue(pd.isna(output.loc[output.rcid == 7, "naics_code"].item()))
        self.assertEqual(output.loc[output.rcid == 7, "naics_n_codes"].item(), 0)
        self.assertEqual(int(output.naics_code.notna().sum()), len(self.firms) - 1)

    def test_indexed_queries_are_bounded_and_complete_cache_is_offline(self):
        db = FixtureDB()
        output, info = m.pull_firm_industry(db, self.cache(db), self.cfg, self.firms)
        self.assertEqual([len(x) for x in db.data_calls], [1000, 1000, 501])
        self.assertEqual([x for batch in db.data_calls for x in batch], self.firms)
        self.assertEqual(info["industry_firms_multi_code"], 1)
        self.check_output(output)
        offline = FixtureDB(forbid=True)
        again, _ = m.pull_firm_industry(offline, self.cache(offline), self.cfg, self.firms)
        pd.testing.assert_frame_equal(output, again)

    def test_interrupted_second_batch_resumes_only_missing(self):
        first = FixtureDB(fail_at=2)
        with self.assertRaisesRegex(RuntimeError, "Synthetic"):
            m.pull_firm_industry(first, self.cache(first), self.cfg, self.firms)
        resumed = FixtureDB()
        output, _ = m.pull_firm_industry(resumed, self.cache(resumed), self.cfg, self.firms)
        self.assertEqual([x for batch in resumed.data_calls for x in batch], self.firms[1000:])
        self.check_output(output)
        offline = FixtureDB(forbid=True)
        m.pull_firm_industry(offline, self.cache(offline), self.cfg, self.firms)

    def test_legacy_complete_cache_does_not_inspect_schema(self):
        offline = FixtureDB(forbid=True)
        cache = self.cache(offline)
        cache.store("industry", m.industry_sql(self.firms, self.cfg), rows(self.firms),
                    {"keys": ["rcid"], "string_columns": ["naics_code"]})
        output, _ = m.pull_firm_industry(offline, cache, self.cfg, self.firms)
        self.check_output(output)

    def test_sequential_scan_uses_one_remaining_query(self):
        db = FixtureDB(sequential=True)
        output, _ = m.pull_firm_industry(db, self.cache(db), self.cfg, self.firms)
        self.assertEqual(db.plan_calls, 1)
        self.assertEqual(db.data_calls, [self.firms])
        self.check_output(output)

    def test_sequential_fallback_after_partial_cache_stays_resumable(self):
        db = FixtureDB(sequential=True)
        cache = self.cache(db)
        cache.store("industry", m.industry_sql(self.firms[:1000], self.cfg), rows(self.firms[:1000]),
                    {"keys": ["rcid"], "string_columns": ["naics_code"], "rcids": self.firms[:1000]})
        output, _ = m.pull_firm_industry(db, cache, self.cfg, self.firms)
        self.assertEqual(db.data_calls, [self.firms[1000:]])
        self.check_output(output)
        offline = FixtureDB(forbid=True)
        again, _ = m.pull_firm_industry(offline, self.cache(offline), self.cfg, self.firms)
        pd.testing.assert_frame_equal(output, again)

    def test_stale_checkpoints_are_requeried(self):
        db = FixtureDB()
        cache = self.cache(db)
        old = time.time() - 1000
        for ids in (self.firms, self.firms[:1000]):
            cache.store("industry", m.industry_sql(ids, self.cfg), rows(ids),
                        {"keys": ["rcid"], "string_columns": ["naics_code"]}, saved_epoch=old)
        output, _ = m.pull_firm_industry(db, cache, self.cfg, self.firms, fresh_after=old + 1)
        self.assertEqual([x for batch in db.data_calls for x in batch], self.firms)
        self.check_output(output)

    def test_metadata_transport_failure_propagates(self):
        class BrokenDB(FixtureDB):
            def query(self, sql, label):
                if "information_schema.columns" in sql:
                    raise RuntimeError("Synthetic connection failure")
                return super().query(sql, label)
        db = BrokenDB()
        with self.assertRaisesRegex(RuntimeError, "connection failure"):
            m.pull_firm_industry(db, self.cache(db), self.cfg, self.firms)
        self.assertFalse(db.data_calls)

    def test_combined_cache_keeps_oldest_component_freshness(self):
        db = FixtureDB()
        cache = self.cache(db)
        old = time.time() - 1000
        cache.store("industry", m.industry_sql(self.firms[:1000], self.cfg), rows(self.firms[:1000]),
                    {"keys": ["rcid"], "string_columns": ["naics_code"]}, saved_epoch=old)
        m.pull_firm_industry(db, cache, self.cfg, self.firms)
        complete = cache.lookup("industry", m.industry_sql(self.firms, self.cfg))
        self.assertEqual(complete[1]["saved_epoch"], old)
        refreshed_db = FixtureDB()
        m.pull_firm_industry(refreshed_db, self.cache(refreshed_db), self.cfg, self.firms, fresh_after=old + 1)
        self.assertEqual(refreshed_db.data_calls, [self.firms[:1000]])

    def test_out_of_scope_response_is_not_checkpointed(self):
        class WrongDB(FixtureDB):
            def query(self, sql, label):
                if sql.startswith("SELECT ") and "information_schema" not in sql:
                    return rows([999999])
                return super().query(sql, label)
        db = WrongDB()
        cache = self.cache(db)
        with self.assertRaisesRegex(RuntimeError, "outside"):
            m.pull_firm_industry(db, cache, self.cfg, [1, 2])
        self.assertIsNone(cache.lookup("industry", m.industry_sql([1, 2], self.cfg)))

    def test_connection_parameters_and_query_progress_order(self):
        calls = []
        class Connection:
            def __init__(self, **kwargs):
                calls.append(("connect", kwargs))
            def raw_sql(self, sql):
                calls.append(("sql", sql))
                return pd.DataFrame({"value": [1]})
            def close(self):
                pass
        fake_wrds = SimpleNamespace(Connection=Connection)
        with mock.patch.dict(sys.modules, {"wrds": fake_wrds}), mock.patch.object(m.logging, "info") as log:
            db = m.Database("offline-user")
            result = db.query("SELECT 1", "Synthetic query")
        self.assertEqual(len(result), 1)
        kwargs = calls[0][1]
        self.assertEqual(kwargs["wrds_username"], "offline-user")
        args = kwargs["wrds_connect_args"]
        for name in ("connect_timeout", "keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count", "tcp_user_timeout"):
            self.assertGreater(args[name], 0)
        messages = [str(call.args[0]) for call in log.call_args_list]
        connected = next(i for i, s in enumerate(messages) if s.startswith("Connected to WRDS"))
        query_started = next(i for i, s in enumerate(messages) if "query started" in s)
        self.assertLess(connected, query_started)


if __name__ == "__main__":
    unittest.main(verbosity=2)
