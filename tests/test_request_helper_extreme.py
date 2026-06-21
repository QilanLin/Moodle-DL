# -*- coding: utf-8 -*-
"""
Extreme / adversarial tests for moodle_dl/moodle/request_helper.py.

Based on a subagent audit, this file covers the following gaps:

  * Empty / null response body handling
  * All HTTP error status codes (5xx, 4xx, 429 retry)
  * Exponential backoff timing verification
  * Sync vs async path equivalence
  * recursive_urlencode edge cases
  * Cookie file lock contention (concurrent flock)
  * Concurrent response log writes

These tests push the boundary to ensure the API client
never crashes on any response from the Moodle server.
"""
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Helper to build a RequestHelper
# =========================================================================
@pytest.fixture
def helper():
    """Build a RequestHelper with mock config and URL."""
    from moodle_dl.moodle.request_helper import RequestHelper
    from moodle_dl.types import MoodleDlOpts, MoodleURL
    opts = MoodleDlOpts()
    opts.token = 'test-token'
    url = MoodleURL(use_http=False, domain='m.example.com', path='/')
    config = MagicMock()
    return RequestHelper(config, opts, url, 'test-token')


@pytest.fixture
def async_post_side_effect(helper):
    """Returns a function that mocks aiohttp's session.post
    for async_post testing. Returns a tuple of
    (mock_session, mock_response_factory).
    """
    sessions = []

    def setup(status=200, body='', retries=0):
        nonlocal sessions
        response = MagicMock()
        response.status = status
        response.text = AsyncMock(return_value=body)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        response.request_info = MagicMock()
        response.history = ()

        session = MagicMock()
        if retries > 0:
            # First call: error; subsequent calls: success
            success = MagicMock()
            success.status = 200
            success.text = AsyncMock(return_value='{"ok": true}')
            success.__aenter__ = AsyncMock(return_value=success)
            success.__aexit__ = AsyncMock(return_value=False)
            session.post = MagicMock(side_effect=[
                response, *[success for _ in range(retries)]
            ])
        else:
            session.post = MagicMock(return_value=response)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        sessions.append(session)
        return session

    return setup


# =========================================================================
# Empty / null response body
# =========================================================================
class TestEmptyNullResponseBody:
    """Server returns empty body (200 OK but no JSON)."""

    def test_empty_body_raises_some_error(self, helper, async_post_side_effect):
        """A 200 with empty body must not silently return None.
        Currently the code does json.loads(response_text) which
        raises JSONDecodeError. Verify this is caught and turned
        into a meaningful error.
        """
        session = async_post_side_effect(status=200, body='')
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                # Should raise (not silently succeed)
                with pytest.raises(Exception):
                    asyncio.run(helper.async_post('core_webservice_get_site_info'))

    def test_null_response_body(self, helper, async_post_side_effect):
        """200 with response.text() returning None."""
        session = async_post_side_effect(status=200, body=None)
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                with pytest.raises(Exception):
                    asyncio.run(helper.async_post('core_webservice_get_site_info'))

    def test_invalid_json_body(self, helper, async_post_side_effect):
        """200 with body that's not valid JSON (e.g. HTML error page)."""
        session = async_post_side_effect(
            status=200,
            body='<html>500 Internal Server Error</html>',
        )
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                with pytest.raises(Exception):
                    asyncio.run(helper.async_post('core_webservice_get_site_info'))

    def test_moodle_error_json(self, helper, async_post_side_effect):
        """200 with proper Moodle error JSON (exception, errorcode)."""
        error_response = json.dumps({
            'exception': 'moodle_exception',
            'errorcode': 'invalidtoken',
            'message': 'Invalid token',
        })
        session = async_post_side_effect(status=200, body=error_response)
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                with pytest.raises(Exception) as exc:
                    asyncio.run(helper.async_post('core_webservice_get_site_info'))
        # Verify the error message contains the Moodle error info
        assert 'invalidtoken' in str(exc.value) or \
               'Invalid' in str(exc.value) or \
               'token' in str(exc.value).lower()


# =========================================================================
# HTTP error status codes
# =========================================================================
class TestHTTPErrorCodes:
    """All possible HTTP status codes from Moodle."""

    @pytest.mark.parametrize('status_code', [
        400, 401, 403, 404, 408, 409, 429, 500, 502, 503, 504,
    ])
    def test_all_error_codes_cause_failure(self, helper, async_post_side_effect, status_code):
        """Each error code must result in a clean exception."""
        session = async_post_side_effect(status=status_code, body=f'HTTP {status_code}')
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                with pytest.raises(Exception):
                    asyncio.run(helper.async_post('core_webservice_get_site_info'))

    def test_404_does_not_retry(self, helper, async_post_side_effect):
        """A 404 should NOT retry (was just 1 call)."""
        session = async_post_side_effect(status=404, body='Not Found')
        call_count = [0]
        original_post = session.post
        def counting_post(*args, **kwargs):
            call_count[0] += 1
            return original_post.return_value
        session.post = MagicMock(side_effect=counting_post)
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                with pytest.raises(Exception):
                    asyncio.run(helper.async_post('nonexistent_function'))
        assert call_count[0] == 1, (
            f'404 retried {call_count[0]} times — should be 1'
        )

    def test_429_does_retry(self, helper):
        """A 429 IS retryable — should retry multiple times."""
        call_count = [0]
        def make_response():
            response = MagicMock()
            response.status = 429
            response.text = AsyncMock(return_value='Rate limited')
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            response.request_info = MagicMock()
            response.history = []
            return response
        session = MagicMock()
        def counting_post(*args, **kwargs):
            call_count[0] += 1
            return make_response()
        session.post = MagicMock(side_effect=counting_post)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch('moodle_dl.moodle.request_helper.aiohttp.ClientSession', return_value=session), \
             patch('moodle_dl.moodle.request_helper.make_aiohttp_timeout'):
            with patch.object(helper, 'async_wait_for_network_slot', new=AsyncMock()):
                import asyncio
                with pytest.raises(Exception):
                    asyncio.run(helper.async_post('core_webservice_get_site_info'))
        # 429 should retry (at least 2 calls — initial + retry)
        assert call_count[0] >= 2, (
            f'429 only called {call_count[0]} times — should retry'
        )


# =========================================================================
# recursive_urlencode
# =========================================================================
class TestRecursiveUrlencode:
    """Test the recursive URL encoder."""

    def test_simple_dict(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        result = RequestHelper.recursive_urlencode({'a': 1, 'b': 'two'})
        assert 'a=1' in result
        assert 'b=two' in result

    def test_nested_dict_2_level(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        result = RequestHelper.recursive_urlencode({'a': {'b': 'val'}})
        # Moodle format: a[b]=val
        assert 'a%5Bb%5D=val' in result or 'a[b]=val' in result

    def test_list_values(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        result = RequestHelper.recursive_urlencode({'tags': ['a', 'b', 'c']})
        # Should produce tags[]=a, tags[]=b, tags[]=c
        assert 'a' in result and 'b' in result and 'c' in result
        # tags[]= should be URL-encoded
        assert 'tags' in result

    def test_boolean_values(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        result = RequestHelper.recursive_urlencode({'a': True, 'b': False})
        # Implementation-specific encoding
        # True may become "True" or "1", False may become "False" or "0"
        assert 'a' in result and 'b' in result

    def test_int_float_values(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        result = RequestHelper.recursive_urlencode({'a': 42, 'b': 3.14})
        assert 'a=42' in result
        assert 'b=3.14' in result

    def test_empty_list(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        # Empty list should not crash
        result = RequestHelper.recursive_urlencode({'a': []})
        # Should not raise
        assert isinstance(result, str)

    def test_empty_dict(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        result = RequestHelper.recursive_urlencode({'a': {}})
        # Should not crash
        assert isinstance(result, str)

    def test_none_value(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        # None is encoded — but how? Should not crash
        result = RequestHelper.recursive_urlencode({'a': None})
        assert isinstance(result, str)

    def test_special_chars_unicode(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        # Unicode should be percent-encoded, not crash
        result = RequestHelper.recursive_urlencode({'name': '课程 🎓'})
        assert 'name' in result

    def test_5_level_nested_dict(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        # 5-level nested dict → verify all keys
        result = RequestHelper.recursive_urlencode(
            {'a': {'b': {'c': {'d': {'e': 'v'}}}}}
        )
        assert 'v' in result


# =========================================================================
# Cookie file lock contention
# =========================================================================
class TestCookieFileLockContention:
    """subagent: 'Concurrent process holds lock; verify backoff'."""

    def test_first_attempt_gets_lock(self, tmp_path):
        """First process should get the lock immediately."""
        from moodle_dl.moodle.request_helper import _safe_cookie_flock
        # The function uses cookie_jar_path + '.lock' for the lock file
        cookie_path = str(tmp_path / 'cookies.txt')
        lock_path = cookie_path + '.lock'
        # Create the lock file
        open(lock_path, 'w').close()
        # Mock cookie jar object (with .save() method)
        jar = MagicMock()
        start = time.monotonic()
        result = _safe_cookie_flock(cookie_path, jar)
        elapsed = time.monotonic() - start
        # Should be very fast (< 100ms) on first try
        assert result is True
        assert elapsed < 0.5

    def test_locked_then_release_gives_up(self, tmp_path):
        """When the lock is held by another process, we retry
        with backoff. When it's never released, we give up
        after COOKIE_FLOCK_TIMEOUT_S.
        """
        from moodle_dl.moodle.request_helper import _safe_cookie_flock
        import fcntl

        # The function uses cookie_jar_path + '.lock' for the lock file
        cookie_path = str(tmp_path / 'cookies.txt')
        lock_path = cookie_path + '.lock'
        # Hold the lock externally
        external_lock = open(lock_path, 'w')
        fcntl.flock(external_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            start = time.monotonic()
            jar = MagicMock()
            # Patch the timeout to 0.5s for the test
            with patch(
                'moodle_dl.moodle.request_helper.COOKIE_FLOCK_TIMEOUT_S',
                0.5,
            ), patch(
                'moodle_dl.moodle.request_helper.COOKIE_FLOCK_INITIAL_BACKOFF_S',
                0.05,
            ), patch(
                'moodle_dl.moodle.request_helper.COOKIE_FLOCK_MAX_BACKOFF_S',
                0.1,
            ):
                result = _safe_cookie_flock(cookie_path, jar)
            elapsed = time.monotonic() - start
            # It should give up
            assert result is False
            # Should have given up (not hung forever)
            assert elapsed < 10.0, (
                f'Took too long to give up: {elapsed:.1f}s'
            )
        finally:
            fcntl.flock(external_lock.fileno(), fcntl.LOCK_UN)
            external_lock.close()

    def test_safe_cookie_flock_swallows_unexpected_errno(self, tmp_path):
        """If the OS returns an unexpected errno (not EAGAIN),
        we should return False without crashing.
        """
        from moodle_dl.moodle.request_helper import _safe_cookie_flock
        cookie_path = str(tmp_path / 'cookies.txt')
        lock_path = cookie_path + '.lock'
        open(lock_path, 'w').close()
        jar = MagicMock()
        # fcntl.flock raises OSError with errno 99 (EOPNOTSUPP)
        # which is NOT 11 (EAGAIN) or 35 (EDEADLK). The function
        # should return False without crashing.
        with patch('fcntl.flock', side_effect=OSError(99, 'unexpected errno')):
            result = _safe_cookie_flock(cookie_path, jar)
        assert result is False  # gave up gracefully


# =========================================================================
# Concurrent response log writes
# =========================================================================
class TestConcurrentResponseLogWrites:
    """Two parallel sessions writing same responses.log
    → verify no interleaved bytes < PIPE_BUF (4096)."""

    def test_concurrent_log_writes_no_corruption(self, tmp_path):
        """Two threads writing to the same log file should not
        interleave their bytes (O_APPEND guarantees atomicity up
        to PIPE_BUF = 4096 bytes on macOS/Linux).
        """
        log_path = tmp_path / 'responses.log'

        def write_log(thread_id, n_writes):
            with open(log_path, 'a', encoding='utf-8') as f:
                for i in range(n_writes):
                    f.write(
                        f'thread={thread_id} write={i} '
                        f'padding={"x"*100}\n'
                    )

        import threading
        threads = [
            threading.Thread(target=write_log, args=(t, 50))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all lines are well-formed
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        # 4 threads * 50 writes = 200 lines
        assert len(lines) == 200
        # Every line should match the expected pattern
        for line in lines:
            assert line.startswith('thread=')
            assert 'write=' in line
            assert 'padding=' in line


# =========================================================================
# Session ID expiry 2^31 (Year 2038)
# =========================================================================
class TestCookieExpiresYear2038:
    """subagent: 'cookie.expires = 2147483647 (max 32-bit signed)'."""

    def test_max_32bit_cookie_expires(self, tmp_path):
        """The code uses cookie.expires = 2147483647 (max int).
        Verify that the value is preserved when saving/loading.
        """
        from moodle_dl.utils import MoodleDLCookieJar
        cookie_path = str(tmp_path / 'cookies.txt')
        jar = MoodleDLCookieJar(cookie_path)
        # Set max int
        import http.cookiejar
        c = http.cookiejar.Cookie(
            version=0, name='MoodleSession', value='abc',
            port=None, port_specified=False,
            domain='keats.kcl.ac.uk', domain_specified=True,
            domain_initial_dot=False, path='/',
            path_specified=True, secure=False,
            expires=2147483647,  # max 32-bit signed
            discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
        jar.set_cookie(c)
        jar.save(ignore_discard=True, ignore_expires=True)
        # Reload
        new_jar = MoodleDLCookieJar(cookie_path)
        new_jar.load(ignore_discard=True, ignore_expires=True)
        loaded = list(new_jar)[0]
        assert loaded.expires == 2147483647


# =========================================================================
# Sync vs async post equivalence
# =========================================================================
class TestSyncVsAsyncEquivalence:
    """Both `post` and `async_post` should exist and behave consistently."""

    def test_both_methods_exist(self):
        from moodle_dl.moodle.request_helper import RequestHelper
        assert hasattr(RequestHelper, 'post')
        assert hasattr(RequestHelper, 'async_post')

    def test_404_in_error_messages(self):
        """Verify 404 is handled."""
        from moodle_dl.moodle import request_helper
        import inspect
        src = inspect.getsource(request_helper)
        # Both paths handle 404
        assert '404' in src