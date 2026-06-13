"""
TaskCookieManager: encapsulation of cookie / session / HTTP
infrastructure for the downloader.

This class was extracted from Task to reduce the Task surface
area. The Task class had 6+ methods related to cookie / session
management that were tightly coupled but unrelated to the
"download a file" responsibility. By grouping them here, we
achieve:

  * Higher cohesion: all cookie concerns live in one class
  * Lower coupling: Task only needs to know that
    `mgr.get_requests_jar()` / `mgr.create_session()` exist
  * Testability: cookie logic can be unit-tested in isolation

The interface is intentionally minimal:
    mgr.get_mozilla_jar() -> MoodleDLCookieJar | None
    mgr.get_requests_jar() -> clone of mozilla jar | None
    mgr.create_session() -> requests.Session
"""
import copy
import http.cookiejar
import logging
from io import StringIO
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from moodle_dl.utils import MoodleDLCookieJar


# Cache attribute names (set on opts to memoize the mozilla jar
# across calls — the original code did this on opts itself,
# so we preserve that for compatibility).
_MOZILLA_CACHE_KEY = '_moodle_dl_cookie_jar_cache'
_MOZILLA_CACHE_TEXT_KEY = '_moodle_dl_cookie_jar_cache_text'


class TaskCookieManager:
    """Cookie / session helpers for the downloader subsystem.

    Holds a reference to the Task's `opts` (so it can read the
    cookies_text) but no other state. Stateless aside from the
    cache on `opts` itself.
    """

    def __init__(self, opts, retry_attempts: int = 3, backoff_factor: float = 0.5):
        self.opts = opts
        self.retry_attempts = retry_attempts
        self.backoff_factor = backoff_factor

    # ------------------------------------------------------------------
    # Mozilla cookie jar (used by both aiohttp and requests)
    # ------------------------------------------------------------------
    def get_mozilla_jar(self) -> Optional[MoodleDLCookieJar]:
        """Return the cached Mozilla cookie jar, building it from
        `opts.cookies_text` on first use (and invalidating when
        the text changes)."""
        if not self.opts.cookies_text:
            # None OR empty string: no cookies
            return None

        if getattr(self.opts, _MOZILLA_CACHE_TEXT_KEY, None) != self.opts.cookies_text:
            cookie_jar = MoodleDLCookieJar(StringIO(self.opts.cookies_text))
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
            setattr(self.opts, _MOZILLA_CACHE_TEXT_KEY, self.opts.cookies_text)
            setattr(self.opts, _MOZILLA_CACHE_KEY, cookie_jar)
        return getattr(self.opts, _MOZILLA_CACHE_KEY)

    @staticmethod
    def clone_mozilla_jar(cookie_jar: Any) -> Any:
        """Return an independent copy of the cookie jar.

        Used when we want to mutate cookies without affecting
        the original (e.g. for a one-off requests session)."""
        if cookie_jar is None or not isinstance(cookie_jar, http.cookiejar.CookieJar):
            return cookie_jar
        cloned = MoodleDLCookieJar()
        for cookie in cookie_jar:
            cloned.set_cookie(copy.copy(cookie))
        return cloned

    def get_requests_jar(self) -> Any:
        """Return a fresh clone of the mozilla jar suitable for
        a requests.Session (cookies=True)."""
        return self.clone_mozilla_jar(self.get_mozilla_jar())

    # ------------------------------------------------------------------
    # requests.Session factory
    # ------------------------------------------------------------------
    def create_session(self) -> requests.Session:
        """Build a requests.Session with retry/backoff/cookies
        pre-configured. Cookie loading errors are logged but
        non-fatal (we proceed with an empty session)."""
        session = requests.Session()
        retry_strategy = Retry(
            total=self.retry_attempts,
            backoff_factor=self.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['HEAD', 'GET', 'OPTIONS'],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount('https://', adapter)
        session.mount('http://', adapter)

        if self.opts.cookies_text is not None:
            try:
                session.cookies = self.clone_mozilla_jar(self.get_mozilla_jar())
                logging.debug('✓ Successfully loaded Cookie')
            except Exception as e:
                logging.warning('⚠️  Failed to load Cookie: %s', e)

        skip_verify = getattr(
            self.opts.global_opts, 'skip_cert_verify', False
        )
        if skip_verify:
            session.verify = False
        return session
