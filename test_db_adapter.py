"""Test Suite for Unified Database Adapter (db_adapter.py)
Validates query translation, RowWrapper, cursor semantics, and PostgreSQL schema definitions.
"""
import os
import unittest
from unittest.mock import patch
import db_adapter

class TestDbAdapter(unittest.TestCase):
    def test_postgres_url_selects_postgres_backend(self):
        with patch.object(db_adapter.config, 'DATABASE_URL', ''):
            with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://user:pass@localhost/bms', 'BMS_DATABASE_URL': ''}):
                self.assertTrue(db_adapter.is_postgres())
                self.assertEqual(db_adapter.get_database_engine(), 'PostgreSQL')

    def test_no_database_url_keeps_sqlite_backend(self):
        with patch.object(db_adapter.config, 'DATABASE_URL', ''):
            with patch.dict(os.environ, {'DATABASE_URL': '', 'BMS_DATABASE_URL': ''}):
                self.assertFalse(db_adapter.is_postgres())
                self.assertEqual(db_adapter.get_database_engine(), 'SQLite')

    def test_bms_database_url_selects_postgres_backend(self):
        with patch.object(db_adapter.config, 'DATABASE_URL', ''):
            with patch.dict(os.environ, {'DATABASE_URL': '', 'BMS_DATABASE_URL': 'postgresql://user:pass@localhost/bms'}):
                self.assertTrue(db_adapter.is_postgres())
                self.assertEqual(db_adapter.get_database_engine(), 'PostgreSQL')

    def test_query_translation_basic(self):
        q = "SELECT * FROM users WHERE username = ?"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertEqual(t, "SELECT * FROM users WHERE username = %s")

    def test_query_translation_multiple(self):
        q = "SELECT * FROM users WHERE username = ? AND account_status = ?"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertEqual(t, "SELECT * FROM users WHERE username = %s AND account_status = %s")

    def test_insert_or_ignore_translation(self):
        q = "INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertTrue(t.startswith("INSERT INTO system_settings"))
        self.assertTrue(t.endswith("ON CONFLICT DO NOTHING"))
        self.assertIn("%s, %s, %s", t)

    def test_insert_or_replace_task_assignments(self):
        q = "INSERT OR REPLACE INTO task_assignments (task_id, staff_id, status, assigned_at) VALUES (?, ?, 'PENDING', ?)"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertIn("ON CONFLICT (task_id, staff_id) DO UPDATE", t)
        self.assertIn("EXCLUDED.status", t)

    def test_literal_question_mark_preserved(self):
        q = "SELECT * FROM announcements WHERE body LIKE '%?%' AND title = ?"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertIn("'%?%'", t)
        self.assertTrue(t.endswith("title = %s"))

    def test_is_not_translation(self):
        q = "SELECT * FROM users WHERE ? IS NOT handler_user_id"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertIn("IS DISTINCT FROM handler_user_id", t)
        self.assertNotIn("IS NOT handler_user_id", t)

    def test_is_not_null_preserved(self):
        q = "SELECT * FROM users WHERE deleted_at IS NOT NULL"
        t = db_adapter.translate_query_to_postgres(q)
        self.assertIn("IS NOT NULL", t)
        self.assertNotIn("IS DISTINCT FROM NULL", t)

    def test_cursor_iterable(self):
        cur = db_adapter.PostgresCursorWrapper.__new__(db_adapter.PostgresCursorWrapper)
        cur._results = [
            db_adapter.RowWrapper({'id': 1, 'name': 'a'}, (1, 'a'), ['id', 'name']),
            db_adapter.RowWrapper({'id': 2, 'name': 'b'}, (2, 'b'), ['id', 'name']),
        ]
        cur._index = 0
        names = [row['name'] for row in cur]
        self.assertEqual(names, ['a', 'b'])


    def test_row_wrapper(self):
        r = db_adapter.RowWrapper({'id': 1, 'username': 'admin'}, (1, 'admin'), ['id', 'username'])
        self.assertEqual(r['id'], 1)
        self.assertEqual(r['username'], 'admin')
        self.assertEqual(r[0], 1)
        self.assertEqual(r[1], 'admin')
        self.assertEqual(dict(r), {'id': 1, 'username': 'admin'})
        self.assertIn('id', r)
        self.assertNotIn('missing', r)
        self.assertEqual(r.get('missing', 'default'), 'default')

    def test_postgres_schema_contains_all_tables(self):
        expected_tables = [
            'users', 'staff_assignment_history', 'residents', 'system_settings',
            'resident_id_sequences', 'resident_id_issuances', 'requests',
            'classifications', 'tasks', 'announcements', 'certificate_issuances',
            'programs', 'notifications', 'activity_logs', 'barangay_officials',
            'gallery_items', 'conversations', 'messages', 'message_recipients',
            'task_assignments', 'task_updates'
        ]
        schema_lower = db_adapter.POSTGRES_SCHEMA.lower()
        for tbl in expected_tables:
            self.assertIn(f"create table if not exists {tbl}", schema_lower, f"Missing table in POSTGRES_SCHEMA: {tbl}")

if __name__ == '__main__':
    unittest.main()
