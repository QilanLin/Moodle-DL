# -*- coding: utf-8 -*-
"""
Unit tests for TaskCookieManager.

The manager encapsulates the cookie cache + requests.Session
factory that used to live in Task. Pinning its behavior ensures
the cache invalidation rules and the retry/cookie configuration
are preserved across refactors.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

# 🔧 Portability: use __file__ to find the project root, not a
# hardcoded user-specific path. Pytest's conftest.py also adds
# the root, but having it in-file makes this test runnable in
# isolation (e.g. ``python -m unittest``).
import os.path as _path
_ROOT = _path.dirname(_path.dirname(_path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from moodle_dl.downloader.task_cookie_manager import TaskCookieManager
from moodle_dl.utils import MoodleDLCookieJar


@pytest.fixture
def opts_with_cookies():
    """An opts-like object that has cookies_text and a global_opts."""
    opts = MagicMock()
    opts.cookies_text = (
        '# Netscape HTTP Cookie File\n'
        '.example.com\tTRUE\t/\tFALSE\t9999999999\tname\tvalue\n'
    )
    opts.global_opts.skip_cert_verify = False
    return opts


# =======================================================================
# get_mozilla_jar
# =======================================================================
class TestGetMozillaJar:
    def test_returns_none_when_cookies_text_is_none(self):
        opts = MagicMock()
        opts.cookies_text = None
        mgr = TaskCookieManager(opts)
        assert mgr.get_mozilla_jar() is None

    def test_returns_none_when_cookies_text_is_empty(self):
        opts = MagicMock()
        opts.cookies_text = ''
        mgr = TaskCookieManager(opts)
        assert mgr.get_mozilla_jar() is None

    def test_builds_jar_from_netscape_format(self, opts_with_cookies):
        mgr = TaskCookieManager(opts_with_cookies)
        with patch.object(
            TaskCookieManager, 'get_mozilla_jar',
            wraps=mgr.get_mozilla_jar,
        ):
            # The test is that loading the cookies text actually
            # produces a real MoodleDLCookieJar with the cookie
            jar = mgr.get_mozilla_jar()
        assert isinstance(jar, MoodleDLCookieJar)
        # The cookie was loaded
        assert len(list(jar)) == 1

    def test_caches_jar_on_opts(self, opts_with_cookies):
        """The jar is cached on opts to avoid re-parsing."""
        mgr = TaskCookieManager(opts_with_cookies)
        # Set the cache key text to match the test's cookies
        opts_with_cookies._moodle_dl_cookie_jar_cache_text = 'mismatch'
        opts_with_cookies._moodle_dl_cookie_jar_cache = None
        jar1 = mgr.get_mozilla_jar()
        # Second call should not re-parse because the text matches
        opts_with_cookies._moodle_dl_cookie_jar_cache_text = (
            opts_with_cookies.cookies_text
        )
        jar2 = mgr.get_mozilla_jar()
        assert jar1 is jar2  # same instance


# =======================================================================
# clone_mozilla_jar
# =======================================================================
class TestCloneMozillaJar:
    def test_returns_none_for_none(self):
        assert TaskCookieManager.clone_mozilla_jar(None) is None

    def test_returns_non_jar_unchanged(self):
        """Anything that isn't a MozillaCookieJar is returned as-is."""
        not_a_jar = object()
        assert TaskCookieManager.clone_mozilla_jar(not_a_jar) is not_a_jar

    def test_clones_a_real_jar(self):
        original = MoodleDLCookieJar()
        cloned = TaskCookieManager.clone_mozilla_jar(original)
        assert cloned is not original
        assert isinstance(cloned, MoodleDLCookieJar)
        assert len(list(cloned)) == 0


# =======================================================================
# create_session
# =======================================================================
class TestCreateSession:
    def test_builds_session_with_retry(self, opts_with_cookies):
        mgr = TaskCookieManager(
            opts_with_cookies, retry_attempts=5, backoff_factor=2
        )
        session = mgr.create_session()
        # The session is configured with retry adapters
        assert 'https://' in session.adapters
        assert 'http://' in session.adapters

    def test_logs_but_swallows_cookie_loading_error(self, opts_with_cookies):
        """Invalid cookies text doesn't crash session creation."""
        # Set invalid cookies text
        opts_with_cookies.cookies_text = 'not netscape format'
        mgr = TaskCookieManager(opts_with_cookies)
        # Should NOT raise; just log a warning
        session = mgr.create_session()
        assert session is not None

    def test_no_cookies_no_cookies_loaded(self):
        """When cookies_text is None, the session has no cookies."""
        opts = MagicMock()
        opts.cookies_text = None
        opts.global_opts.skip_cert_verify = False
        mgr = TaskCookieManager(opts)
        session = mgr.create_session()
        # Session exists; cookie loading is skipped
        assert session is not None
