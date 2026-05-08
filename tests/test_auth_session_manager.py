# -*- coding: utf-8 -*-
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from moodle_dl.auth_session_manager import AuthSessionManager, normalize_playwright_cookie
from moodle_dl.database import StateRecorder


class TestNormalizePlaywrightCookie(unittest.TestCase):
    def test_normalizes_database_cookie_fields_for_playwright(self):
        cookie = {
            'cookie_id': 'db-row-id',
            'name': 'MoodleSession',
            'value': 'abc',
            'domain': 'moodle.example.com',
            'expires': 4102444800000,
            'secure': 1,
            'httponly': 0,
            'samesite': '',
        }

        normalized = normalize_playwright_cookie(cookie)

        self.assertNotIn('cookie_id', normalized)
        self.assertEqual(normalized['expires'], 4102444800)
        self.assertTrue(normalized['secure'])
        self.assertFalse(normalized['httpOnly'])
        self.assertEqual(normalized['sameSite'], 'Lax')
        self.assertEqual(normalized['path'], '/')

    def test_invalid_or_empty_expires_becomes_session_cookie(self):
        for expires in (None, '', -5, 'not-a-timestamp'):
            with self.subTest(expires=expires):
                normalized = normalize_playwright_cookie(
                    {'name': 'sid', 'value': 'v', 'domain': 'example.com', 'expires': expires}
                )
                self.assertEqual(normalized['expires'], -1)

    def test_positive_seconds_expires_is_kept_as_integer(self):
        normalized = normalize_playwright_cookie(
            {'name': 'sid', 'value': 'v', 'domain': 'example.com', 'expires': 4102444800.9}
        )

        self.assertEqual(normalized['expires'], 4102444800)


class TestAuthSessionManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'moodle_state.db')
        conn = sqlite3.connect(self.db_path)
        try:
            StateRecorder._create_fresh_database_v9(conn.cursor())
            conn.commit()
        finally:
            conn.close()

        self.manager = AuthSessionManager(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _fetch_session_row(self, session_id):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM auth_sessions WHERE session_id = ?', (session_id,)).fetchone()
            return dict(row)
        finally:
            conn.close()

    def test_create_session_stores_cookies_and_audit_details(self):
        cookies = [
            {
                'name': 'MoodleSession',
                'value': 'session-value',
                'domain': 'moodle.example.com',
                'path': '/',
                'expires': '',
                'secure': True,
                'httpOnly': True,
                'sameSite': 'None',
            }
        ]

        with patch('moodle_dl.auth_session_manager.uuid.uuid4', side_effect=['session-id', 'cookie-id']):
            session_id = self.manager.create_session(
                session_type=AuthSessionManager.TYPE_COOKIE_BATCH,
                source=AuthSessionManager.SOURCE_SSO,
                cookies=cookies,
                creator_id='creator',
                owner_id='owner',
                metadata={'browser': 'firefox'},
            )

        self.assertEqual(session_id, 'session-id')

        session = self.manager.get_valid_session(AuthSessionManager.TYPE_COOKIE_BATCH)
        self.assertEqual(session['session_id'], 'session-id')
        self.assertEqual(session['source'], AuthSessionManager.SOURCE_SSO)
        self.assertEqual(json.loads(session['metadata']), {'browser': 'firefox'})

        stored_cookies = self.manager.get_session_cookies(session_id)
        self.assertEqual(len(stored_cookies), 1)
        self.assertEqual(stored_cookies[0]['name'], 'MoodleSession')
        self.assertEqual(stored_cookies[0]['expires'], -1)
        self.assertTrue(stored_cookies[0]['secure'])
        self.assertTrue(stored_cookies[0]['httpOnly'])

        audit_log = self.manager.get_audit_log(session_id=session_id, action=AuthSessionManager.ACTION_CREATE)
        self.assertEqual(len(audit_log), 1)
        self.assertEqual(audit_log[0]['status'], 'success')
        self.assertEqual(audit_log[0]['details']['cookies_count'], 1)

    def test_verify_session_marks_expired_session_and_logs_failure(self):
        with patch('moodle_dl.auth_session_manager.uuid.uuid4', return_value='expired-session'):
            session_id = self.manager.create_session(
                session_type=AuthSessionManager.TYPE_TOKEN,
                source=AuthSessionManager.SOURCE_API_LOGIN,
                token='old-token',
                expires_in_seconds=-1,
            )

        self.assertFalse(self.manager.verify_session(session_id))

        row = self._fetch_session_row(session_id)
        self.assertEqual(row['status'], AuthSessionManager.STATUS_EXPIRED)
        audit_log = self.manager.get_audit_log(session_id=session_id, action=AuthSessionManager.ACTION_VERIFY)
        self.assertEqual(audit_log[0]['status'], 'failed')
        self.assertEqual(audit_log[0]['reason'], 'Session expired')

    def test_verify_session_returns_false_for_missing_session(self):
        self.assertFalse(self.manager.verify_session('missing-session'))

        audit_log = self.manager.get_audit_log(session_id='missing-session', action=AuthSessionManager.ACTION_VERIFY)
        self.assertEqual(audit_log[0]['status'], 'failed')
        self.assertEqual(audit_log[0]['reason'], 'Session not found')

    def test_verify_session_success_updates_last_accessed_and_logs_success(self):
        with patch('moodle_dl.auth_session_manager.uuid.uuid4', return_value='valid-session'):
            session_id = self.manager.create_session(
                session_type=AuthSessionManager.TYPE_TOKEN,
                source=AuthSessionManager.SOURCE_API_LOGIN,
                token='token',
            )

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('UPDATE auth_sessions SET last_accessed_at = 0 WHERE session_id = ?', (session_id,))
            conn.commit()
        finally:
            conn.close()

        with patch('moodle_dl.auth_session_manager.time.time', return_value=2000):
            self.assertTrue(self.manager.verify_session(session_id))

        row = self._fetch_session_row(session_id)
        self.assertEqual(row['last_accessed_at'], 2000)
        audit_log = self.manager.get_audit_log(session_id=session_id, action=AuthSessionManager.ACTION_VERIFY)
        self.assertEqual(audit_log[0]['status'], 'success')

    def test_refresh_session_replaces_old_session_and_stores_new_cookies(self):
        with patch('moodle_dl.auth_session_manager.uuid.uuid4', return_value='old-session'):
            old_session_id = self.manager.create_session(
                session_type=AuthSessionManager.TYPE_TOKEN,
                source=AuthSessionManager.SOURCE_API_LOGIN,
                token='old-token',
                private_token='old-private-token',
                owner_id='owner',
                creator_id='creator',
            )

        with patch('moodle_dl.auth_session_manager.uuid.uuid4', side_effect=['new-session', 'new-cookie']):
            new_session_id = self.manager.refresh_session(
                old_session_id,
                new_token='new-token',
                new_private_token='new-private-token',
                new_cookies=[
                    {'name': 'sid', 'value': 'new-cookie-value', 'domain': 'moodle.example.com', 'path': '/'}
                ],
                creator_id='refresh-user',
            )

        old_row = self._fetch_session_row(old_session_id)
        new_row = self._fetch_session_row(new_session_id)
        self.assertEqual(old_row['status'], AuthSessionManager.STATUS_REPLACED)
        self.assertEqual(old_row['replaced_by_session_id'], new_session_id)
        self.assertEqual(new_row['previous_session_id'], old_session_id)
        self.assertEqual(new_row['token_value'], 'new-token')
        self.assertEqual(new_row['private_token_value'], 'new-private-token')

        cookies = self.manager.get_session_cookies(new_session_id)
        self.assertEqual(cookies[0]['name'], 'sid')
        self.assertEqual(cookies[0]['value'], 'new-cookie-value')

    def test_refresh_session_raises_when_old_session_is_missing(self):
        with patch('moodle_dl.auth_session_manager.uuid.uuid4', return_value='new-session'):
            with self.assertRaisesRegex(ValueError, 'Session missing-session not found'):
                self.manager.refresh_session('missing-session', new_token='new-token')

    def test_revoke_session_hides_it_from_valid_session_lookup(self):
        with patch('moodle_dl.auth_session_manager.uuid.uuid4', return_value='revoked-session'):
            session_id = self.manager.create_session(
                session_type=AuthSessionManager.TYPE_TOKEN,
                source=AuthSessionManager.SOURCE_API_LOGIN,
                token='token',
            )

        self.manager.revoke_session(session_id, reason='user requested')

        self.assertIsNone(self.manager.get_valid_session(AuthSessionManager.TYPE_TOKEN))
        audit_log = self.manager.get_audit_log(session_id=session_id, action=AuthSessionManager.ACTION_REVOKE)
        self.assertEqual(audit_log[0]['status'], 'success')
        self.assertEqual(audit_log[0]['reason'], 'user requested')

    def test_save_sso_cookies_returns_none_when_create_session_fails(self):
        with patch.object(self.manager, 'create_session', side_effect=sqlite3.DatabaseError('locked')):
            self.assertIsNone(self.manager.save_sso_cookies([{'name': 'sid'}]))

    def test_save_sso_cookies_creates_cookie_batch_session(self):
        with patch('moodle_dl.auth_session_manager.uuid.uuid4', side_effect=['sso-session', 'sso-cookie']):
            session_id = self.manager.save_sso_cookies(
                [{'name': 'sid', 'value': 'cookie-value', 'domain': 'moodle.example.com'}],
                creator_id='browser-login',
            )

        self.assertEqual(session_id, 'sso-session')
        row = self._fetch_session_row(session_id)
        self.assertEqual(row['session_type'], AuthSessionManager.TYPE_COOKIE_BATCH)
        self.assertEqual(row['source'], AuthSessionManager.SOURCE_SSO)
        self.assertEqual(row['creator_id'], 'browser-login')


if __name__ == '__main__':
    unittest.main()
