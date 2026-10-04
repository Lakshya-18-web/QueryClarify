"""
Regression tests for the "FK inspection failed ... 'TABLENAME'" bug.

SQLAlchemy is not required: the fallback imports ``sqlalchemy.text`` lazily and
these tests stub it.
"""

import sys
import types
import unittest

from app.fk_reflection import get_foreign_keys_safe, group_fk_rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows, log):
        self._rows, self._log = rows, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self._log.append((statement, params))
        return FakeResult(self._rows)


class FakeEngine:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.log = rows or [], fail, []

    def connect(self):
        if self.fail:
            raise RuntimeError("db down")
        return FakeConnection(self.rows, self.log)


class BrokenInspector:
    """Mimics the real failure: raises KeyError('TABLENAME')."""

    def get_foreign_keys(self, table):
        raise KeyError("TABLENAME")


class HealthyInspector:
    def get_foreign_keys(self, table):
        return [{"name": "fk", "constrained_columns": ["a"],
                 "referred_table": "p", "referred_columns": ["id"],
                 "referred_schema": None}]


class GroupingTests(unittest.TestCase):
    def test_enrolled_in_shape(self):
        rows = [
            ("enrolled_in_ibfk_1", "StuID", "Student", "StuID"),
            ("enrolled_in_ibfk_2", "CID", "Course", "CID"),
        ]
        fks = group_fk_rows(rows)
        self.assertEqual(
            {f["referred_table"] for f in fks}, {"Student", "Course"}
        )
        self.assertEqual(fks[0]["constrained_columns"], ["StuID"])
        self.assertEqual(fks[0]["referred_columns"], ["StuID"])
        self.assertIsNone(fks[0]["referred_schema"])

    def test_composite_key_stays_in_one_constraint(self):
        rows = [
            ("fk_x", "a", "Parent", "a_id"),
            ("fk_x", "b", "Parent", "b_id"),
        ]
        fks = group_fk_rows(rows)
        self.assertEqual(len(fks), 1)
        self.assertEqual(fks[0]["constrained_columns"], ["a", "b"])
        self.assertEqual(fks[0]["referred_columns"], ["a_id", "b_id"])

    def test_no_rows(self):
        self.assertEqual(group_fk_rows([]), [])


class FallbackTests(unittest.TestCase):
    def setUp(self):
        fake = types.ModuleType("sqlalchemy")
        fake.text = lambda sql: sql
        self._saved = sys.modules.get("sqlalchemy")
        sys.modules["sqlalchemy"] = fake

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("sqlalchemy", None)
        else:
            sys.modules["sqlalchemy"] = self._saved

    def test_healthy_inspector_is_used_untouched(self):
        engine = FakeEngine()
        out = get_foreign_keys_safe(engine, HealthyInspector(), "c")
        self.assertEqual(out[0]["referred_table"], "p")
        self.assertEqual(engine.log, [])        # no fallback query issued

    def test_tablename_keyerror_falls_back_to_information_schema(self):
        engine = FakeEngine(rows=[
            ("enrolled_in_ibfk_1", "StuID", "Student", "StuID"),
            ("enrolled_in_ibfk_2", "CID", "Course", "CID"),
        ])
        out = get_foreign_keys_safe(engine, BrokenInspector(), "Enrolled_in")
        self.assertEqual(
            sorted(f["referred_table"] for f in out), ["Course", "Student"]
        )
        sql, params = engine.log[0]
        self.assertIn("information_schema.KEY_COLUMN_USAGE", sql)
        self.assertIn("DATABASE()", sql)
        self.assertEqual(params, {"table_name": "Enrolled_in"})  # bound, not f-string

    def test_total_failure_returns_empty_list_instead_of_raising(self):
        out = get_foreign_keys_safe(FakeEngine(fail=True), BrokenInspector(), "t")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
