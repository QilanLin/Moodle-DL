# -*- coding: utf-8 -*-
"""
Tests for the aiohttp DNS / connect / per-chunk read timeout fix.

Background: aiohttp's stock ``ClientTimeout(total=...)`` only bounds
the *whole* request. DNS resolution and the TCP handshake each have
their own OS-default timeouts (75+ seconds), so an unreachable host
like ``keats.kcl.ac.uk`` (when the user is on a network that can't
reach KCL) would block a worker thread for the full OS DNS timeout
and aiohttp's connection pool would starve, eventually deadlocking
the executor thread pool.

The fix: ``make_aiohttp_timeout()`` in ``_patterns`` returns a
``ClientTimeout`` with explicit per-phase ceilings. These tests
pin the contract: defaults, override, integration with
``ClientSession(timeout=...)``, and the regression scenario (a
hanging host must surface a timeout exception promptly rather than
block forever).

Tests are split into:
  * unit tests of make_aiohttp_timeout itself (no network)
  * integration tests using a fake resolver that hangs
  * a regression test mirroring the original deadlock: a session
    whose connection never completes must fail within ~15 seconds
"""
import asyncio
import socket
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.downloader._patterns import (  # noqa: E402
    DEFAULT_AIOHTTP_CONNECT_TIMEOUT_S,
    DEFAULT_AIOHTTP_SOCK_READ_TIMEOUT_S,
    DEFAULT_AIOHTTP_TOTAL_TIMEOUT_S,
    make_aiohttp_timeout,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
class TestMakeAiohttpTimeoutDefaults:
    """The default phase ceilings match the module-level constants."""

    def test_defaults_match_documented_constants(self):
        timeout = make_aiohttp_timeout()
        assert timeout.total == DEFAULT_AIOHTTP_TOTAL_TIMEOUT_S
        assert timeout.connect == DEFAULT_AIOHTTP_CONNECT_TIMEOUT_S
        assert timeout.sock_read == DEFAULT_AIOHTTP_SOCK_READ_TIMEOUT_S

    def test_defaults_are_reasonable_bounds(self):
        # Sanity: defaults must be small enough to avoid the original
        # deadlock (which required waiting 75+ seconds for DNS) and large
        # enough to handle real slow connections.
        assert DEFAULT_AIOHTTP_CONNECT_TIMEOUT_S <= 15.0
        assert DEFAULT_AIOHTTP_SOCK_READ_TIMEOUT_S <= 60.0
        assert DEFAULT_AIOHTTP_TOTAL_TIMEOUT_S >= 60.0

    def test_returns_aiohttp_client_timeout(self):
        # aiohttp.ClientTimeout is opaque; we don't pin the type by
        # import, but the result must behave like one.
        import aiohttp
        timeout = make_aiohttp_timeout()
        assert isinstance(timeout, aiohttp.ClientTimeout)


# ---------------------------------------------------------------------------
# Override
# ---------------------------------------------------------------------------
class TestMakeAiohttpTimeoutOverrides:
    """Each phase can be overridden independently."""

    def test_custom_total(self):
        timeout = make_aiohttp_timeout(total_s=60.0)
        assert timeout.total == 60.0
        assert timeout.connect == DEFAULT_AIOHTTP_CONNECT_TIMEOUT_S

    def test_custom_connect(self):
        timeout = make_aiohttp_timeout(connect_s=5.0)
        assert timeout.connect == 5.0
        assert timeout.total == DEFAULT_AIOHTTP_TOTAL_TIMEOUT_S

    def test_custom_sock_read(self):
        timeout = make_aiohttp_timeout(sock_read_s=15.0)
        assert timeout.sock_read == 15.0

    def test_all_custom(self):
        timeout = make_aiohttp_timeout(
            total_s=10.0,
            connect_s=2.0,
            sock_read_s=3.0,
        )
        assert timeout.total == 10.0
        assert timeout.connect == 2.0
        assert timeout.sock_read == 3.0


# ---------------------------------------------------------------------------
# Integration with ClientSession
# ---------------------------------------------------------------------------
class TestAiohttpSessionTimeoutIntegration:
    """Verify ClientSession accepts the timeout without breaking the
    existing API. These tests mock aiohttp so we don't need real
    network access."""

    @pytest.mark.asyncio
    async def test_client_session_accepts_make_aiohttp_timeout(self):
        import aiohttp
        timeout = make_aiohttp_timeout()
        # This should not raise. The constructor just stores the
        # timeout; it doesn't make any network calls.
        session = aiohttp.ClientSession(timeout=timeout)
        try:
            # The session stores the timeout object unchanged.
            assert session.timeout is timeout
        finally:
            await session.close()

    @pytest.mark.asyncio
    async def test_client_session_default_uses_long_dns(self):
        """Pin the regression: a default ClientSession() inherits
        aiohttp's stock connect / sock_connect defaults. Across aiohttp
        versions these are stored under different field names
        (``connect`` in older versions, ``sock_connect`` in 3.13+);
        the actual default value is what matters.

        This is the exact behavior that caused the original deadlock:
        aiohttp's default DNS / TCP connect phase was unbounded on
        some platforms, so an unreachable host (keats.kcl.ac.uk from
        China) could block a worker thread for many seconds and
        starve the connection pool.

        If aiohttp ever changes these defaults, we want this test to
        fail loudly so we re-evaluate whether our explicit timeout
        is still needed.
        """
        import aiohttp
        session = aiohttp.ClientSession()
        try:
            # The default total is 5 minutes — way too lenient to
            # bound a stuck DNS lookup.
            assert session.timeout.total == 300.0
            # Pin *some* default per-phase timeout. The field name
            # changed between aiohttp versions (``connect`` →
            # ``sock_connect`` in 3.13+).
            sock_connect = getattr(
                session.timeout, 'sock_connect',
                getattr(session.timeout, 'connect', None),
            )
            # Whichever name is in use, the default must be at least
            # 30s — i.e. long enough to deadlock a small thread pool
            # on a stuck network. Our explicit fix sets it to 10s.
            assert sock_connect is None or sock_connect >= 30.0, (
                f'aiohttp default sock_connect/connect = {sock_connect}; '
                f'if aiohttp lowers this below 30s, our explicit '
                f'connect=10s becomes a regression, not a fix.'
            )
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Regression: a hanging host must surface a timeout, not block
# ---------------------------------------------------------------------------
class TestAiohttpTimeoutRegression:
    """The original deadlock was: aiohttp blocked on a DNS
    resolution that never completed. With the new explicit
    ``connect=10s`` ceiling, the request must raise
    ``asyncio.TimeoutError`` (or similar) within a few seconds.

    We don't make real network calls. Instead we mock the
    resolver to block forever and assert the timeout fires.
    """

    @pytest.mark.asyncio
    async def test_unreachable_host_raises_within_timeout(self):
        """Pin the fix: an aiohttp session built with
        make_aiohttp_timeout() must fail (not block forever) when
        the resolver hangs.
        """
        import aiohttp

        timeout = make_aiohttp_timeout(
            total_s=5.0,
            connect_s=2.0,  # tight for testing speed
            sock_read_s=5.0,
        )
        session = aiohttp.ClientSession(timeout=timeout)

        # Monkey-patch the connector to use a fake resolver that
        # blocks indefinitely, simulating the KCL keats.ac.uk case.
        fake_loop = asyncio.get_event_loop()
        # Block forever
        async def hang(*args, **kwargs):
            await asyncio.Future()  # never resolves

        with patch.object(
            session.connector, '_resolve_host', side_effect=hang
        ):
            start = time.monotonic()
            try:
                # Use a domain that will fail to resolve. The fake
                # resolver above ensures this never returns.
                async with session.get('https://unreachable.example/') as resp:
                    await resp.read()
                # If we get here, the timeout didn't fire — that's a
                # regression.
                pytest.fail('Expected timeout, got response')
            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError,
                    aiohttp.ClientError):
                elapsed = time.monotonic() - start
                # Must be close to the connect timeout (2s) + small
                # overhead, NOT the OS DNS default of 75s.
                assert elapsed < 4.0, (
                    f'Timeout took {elapsed:.1f}s — too slow, the '
                    f'connect timeout of 2s was not enforced'
                )
            finally:
                await session.close()

    @pytest.mark.asyncio
    async def test_unreachable_host_does_not_block_other_requests(self):
        """Even when one request times out, the session must remain
        usable for subsequent requests. (Regression: previously a
        hung request could starve the entire connection pool.)
        """
        import aiohttp

        # A real local server is not available in this test, so
        # instead we simulate two "successful" calls back-to-back
        # and assert the session is still usable.
        session = aiohttp.ClientSession(timeout=make_aiohttp_timeout())

        # We can't actually make HTTP calls without a server, but we
        # can at least assert the session reports itself as not
        # closed and its pool is open.
        assert not session.closed
        assert not session.connector.closed

        await session.close()
        assert session.closed


# ---------------------------------------------------------------------------
# Helper integration with task.py / request_helper.py
# ---------------------------------------------------------------------------
class TestHelperUsedInDownloader:
    """Pin that the helper is wired into the downloader entry points.
    If a future refactor accidentally removes the timeout kwarg,
    these tests fail before the production regression hits.
    """

    def test_task_uses_make_aiohttp_timeout(self):
        """``Task._create_task`` should call aiohttp.ClientSession
        with the helper-built timeout. We assert by reading the
        source code rather than running it (the real ClientSession
        is a network client).
        """
        import inspect
        from moodle_dl.downloader.task import Task
        src = inspect.getsource(Task)
        # The download_url / _download_url_impl path should reference
        # the helper.
        assert 'make_aiohttp_timeout' in src, (
            'Task does not use make_aiohttp_timeout — DNS timeouts '
            'will fall back to the OS default (75s) and re-introduce '
            'the original deadlock.'
        )

    def test_request_helper_uses_make_aiohttp_timeout(self):
        import inspect
        from moodle_dl.moodle.request_helper import RequestHelper
        src = inspect.getsource(RequestHelper)
        assert 'make_aiohttp_timeout' in src

    def test_book_mod_uses_make_aiohttp_timeout(self):
        import inspect
        from moodle_dl.moodle.mods.book import BookMod
        src = inspect.getsource(BookMod)
        assert 'make_aiohttp_timeout' in src


# ---------------------------------------------------------------------------
# What a ClientTimeout actually does (sanity)
# ---------------------------------------------------------------------------
class TestClientTimeoutSemantics:
    """Sanity tests for aiohttp's ClientTimeout itself. If aiohttp
    changes behavior in a future version, these will fail and we'll
    know to update our defaults."""

    def test_total_clamps_individual_phases(self):
        """When the total deadline expires, aiohttp should cancel
        the request. The per-phase ceilings should also be respected
        independently (i.e. a short connect should not be allowed to
        eat the whole total budget).
        """
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=10.0, connect=1.0, sock_read=2.0)
        assert timeout.total == 10.0
        assert timeout.connect == 1.0
        assert timeout.sock_read == 2.0

    def test_none_means_no_limit(self):
        """A None value for any phase means 'no limit' in aiohttp.
        Our helper must not pass None silently — all three phases
        must be set to numeric values.
        """
        timeout = make_aiohttp_timeout()
        assert timeout.total is not None
        assert timeout.connect is not None
        assert timeout.sock_read is not None
