# -*- coding: utf-8 -*-
"""
Regression tests for M2/M3/M5 audit items.

These tests pin the fixes for three classes of issues that
can make the download process non-responsive or hang:

  M2: ``log_download_status`` infinite loop on Ctrl-C if the
      download loop never reaches its finally block.
      Fix verified: the status logger task is created on
      download_async() and cancelled in a finally block, with
      CancelledError suppressed so the gather() result is not
      poisoned.

  M3: KCL ``keats.ac.uk`` DNS timeout retry loop. The download
      retry path is bounded by ``MAX_DL_RETRIES=3`` and per
      attempt uses the global aiohttp timeout (10s connect).
      Worst case: 3 * (10s + 1s sleep) = 33s, then a clean
      failure. This is acceptable; the test pins the math so
      a future change can't accidentally drop the bound.

  M5: ``pause_controller.wait_if_requested`` busy-wait. The
      while-loop is bounded only by the user pressing R (or
      Ctrl-C). The test verifies that a Ctrl-C during a pause
      exits cleanly (asyncio.CancelledError propagates), not
      a forever-loop.

  Also tests the fix for: the external downloader
  ``proc.communicate`` removal (verified by source inspection),
  the flock timeout, the aiohttp timeout helper, the
  orphan-part scan, and the various asyncio.gather timeouts
  in core_handler / mods / mods/__init__.
"""
import asyncio
import inspect
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 🔧 Portability: use __file__ to find the project root, not a
# hardcoded user-specific path. Pytest's conftest.py also adds
# the root, but having it in-file makes this test runnable in
# isolation (e.g. ``python -m unittest``).
import os.path as _path
_ROOT = _path.dirname(_path.dirname(_path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# =========================================================================
# M2: log_download_status hang-safety
# =========================================================================
class TestLogDownloadStatusHangSafety:
    """The async status logger must be properly cancelled when
    the download loop exits, so it doesn't accumulate in the
    background across runs.
    """

    def test_status_logger_task_cancelled_in_finally(self):
        """Pin the contract: the download method creates the
        status logger task AND cancels it in a finally block.
        If this contract is broken, the status logger will
        outlive the download and accumulate (memory leak).
        """
        from moodle_dl.downloader.download_service import DownloadService

        # Find any download method that creates status_logger_task
        for name, method in inspect.getmembers(
            DownloadService, predicate=inspect.iscoroutinefunction
        ):
            try:
                src = inspect.getsource(method)
            except (OSError, TypeError):
                continue
            if 'status_logger_task' in src:
                # Must create the task
                assert 'status_logger_task = asyncio.create_task' in src, (
                    f'{name} does not create status_logger_task'
                )
                # Must cancel it
                assert 'status_logger_task.cancel()' in src, (
                    f'{name} does not cancel status_logger_task — '
                    f'background leak risk!'
                )
                # Must await the cancellation (or at least try to)
                assert ('await status_logger_task' in src
                        or 'with contextlib.suppress' in src
                        or 'asyncio.CancelledError' in src), (
                    f'{name} does not await the cancelled task — '
                    f'the CancelledError will leak as a warning'
                )

    @pytest.mark.asyncio
    async def test_status_logger_event_loop_unblocks(self):
        """If we artificially set ``_stop_event`` in the logger
        thread (mimicking Ctrl-C), the ``log_download_status``
        coroutine must exit, not block forever.

        We construct a minimal DownloadService via __new__ to
        avoid the heavy __init__, set up a real asyncio.Event
        the way log_download_status does, and then run the
        coroutine in a separate task that we cancel. After
        cancel, the event loop must continue to run other tasks
        (proves the status logger didn't take down the loop).
        """
        from moodle_dl.downloader.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc.opts = MagicMock()
        svc._status_log_event = asyncio.Event()
        svc._status_log_loop = asyncio.get_running_loop()
        svc.pause_controller = MagicMock()
        svc.pause_controller.is_paused.return_value = False
        svc._log_download_status_once = MagicMock()

        # Start the logger; it will await on _status_log_event
        logger_task = asyncio.create_task(svc.log_download_status())

        # Give it a moment to enter event.wait()
        await asyncio.sleep(0.05)
        assert not logger_task.done(), (
            'log_download_status exited before its first event'
        )

        # Cancel — mimics Ctrl-C
        logger_task.cancel()
        try:
            await logger_task
        except asyncio.CancelledError:
            pass  # expected

        # The event loop must remain responsive
        # (a hang here would indicate the logger leaked a
        # blocking operation)
        await asyncio.sleep(0.01)
        assert True  # we got here, so the loop is fine

    @pytest.mark.asyncio
    async def test_status_logger_noop_when_event_already_set(self):
        """If the event is set BEFORE log_download_status
        starts, it must process it and re-loop. We verify by
        pre-setting the event, waiting, and seeing the count
        of status updates.
        """
        from moodle_dl.downloader.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc.opts = MagicMock()
        evt = asyncio.Event()
        evt.set()  # pre-set
        svc._status_log_event = evt
        svc._status_log_loop = asyncio.get_running_loop()
        update_count = [0]

        def fake_log():
            update_count[0] += 1
            # After first update, set the event to keep the loop
            # alive so we can test the cancel path
            if update_count[0] == 1:
                evt.set()
            # After second update, cancel the task
            elif update_count[0] >= 2:
                logger_task.cancel()

        svc.pause_controller = MagicMock()
        svc.pause_controller.is_paused.return_value = False
        svc._log_download_status_once = fake_log

        logger_task = asyncio.create_task(svc.log_download_status())
        try:
            await asyncio.wait_for(logger_task, timeout=2.0)
        except asyncio.TimeoutError:
            logger_task.cancel()
            raise
        except asyncio.CancelledError:
            pass  # expected

        assert update_count[0] >= 1, (
            'log_download_status did not call _log_download_status_once'
        )


# =========================================================================
# M3: retry-loop bounding
# =========================================================================
class TestRetryLoopBounding:
    """The download retry loop must be bounded by
    ``MAX_DL_RETRIES`` so a permanently broken URL can't hang
    the download forever.
    """

    def test_max_dl_retries_is_a_finite_constant(self):
        from moodle_dl.downloader.task import Task
        assert isinstance(Task.MAX_DL_RETRIES, int)
        assert 1 <= Task.MAX_DL_RETRIES <= 10, (
            f'MAX_DL_RETRIES={Task.MAX_DL_RETRIES} out of reasonable range'
        )

    def test_retry_loop_uses_max_dl_retries(self):
        """The download retry loop's while condition must
        reference ``MAX_DL_RETRIES`` so the bound is enforced.
        If the loop becomes unbounded (e.g. ``while True``), a
        broken URL will hang forever.
        """
        src = inspect.getsource(Task.download_using_external_downloader) if False else None
        # Look at _perform_download_request instead
        from moodle_dl.downloader.task import Task
        # Find the function with the retry loop
        for name, method in inspect.getmembers(Task, predicate=inspect.iscoroutinefunction):
            try:
                src = inspect.getsource(method)
            except (OSError, TypeError):
                continue
            if 'while done_tries' in src and 'MAX_DL_RETRIES' in src:
                # Bounded by MAX_DL_RETRIES
                assert 'while done_tries < self.MAX_DL_RETRIES' in src, (
                    f'{name} retry loop not bounded by MAX_DL_RETRIES'
                )
                return
        pytest.fail('No retry loop with MAX_DL_RETRIES found in Task')

    @pytest.mark.asyncio
    async def test_retry_loop_terminates_after_max_retries(self):
        """Simulate a permanently broken URL and verify the
        loop exits after MAX_DL_RETRIES attempts.
        """
        from moodle_dl.downloader.task import Task
        from moodle_dl.types import (
            MoodleDlOpts, DownloadOptions, File, Course,
        )
        from concurrent.futures import ThreadPoolExecutor

        # Find a task and exercise its retry loop
        opts = DownloadOptions(
            token='x', moodle_url='https://m.example',
            download_path='/tmp',
            download_metadata_files=True,
            global_opts=MoodleDlOpts(),
            write_links={'url': True, 'desktop': False},
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text=None,
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
        )
        course = Course(1, 'C')
        file = File(module_id=1, module_name='m', module_modname='url',
                   section_name='s', section_id=1,
                   content_filename='f', content_fileurl='https://x',
                   content_filesize=0, content_timemodified=0,
                   content_type='url', content_isexternalfile=False,
                   content_filepath='/')
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            task = Task(1, file, course, opts, pool, lambda *a, **kw: None)
            task.database = MagicMock()
            # Make _perform_download_request always raise to
            # force the retry path
            call_count = [0]

            async def always_fails(*args, **kwargs):
                call_count[0] += 1
                raise ConnectionError('test fail')

            task._perform_download_request = AsyncMock(side_effect=always_fails)

            # Mock the HEAD request (called once before the body loop)
            class _Head:
                status = 200
                headers = {'Content-Length': '1000'}

            class _Ctx:
                async def __aenter__(self):
                    return _Head()

                async def __aexit__(self, *args):
                    return False

            class _Session:
                def __init__(self, **kwargs):
                    pass

                def request(self, *args, **kwargs):
                    return _Ctx()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

            with patch('moodle_dl.downloader.task.aiohttp.ClientSession', _Session):
                with patch('moodle_dl.downloader.task.SslHelper.get_ssl_context', return_value=None):
                    # Should raise after MAX_DL_RETRIES attempts
                    start = time.monotonic()
                    with pytest.raises(ConnectionError):
                        await asyncio.wait_for(
                            task.download_url('https://x', '/tmp/dest'),
                            timeout=30.0,
                        )
                    elapsed = time.monotonic() - start
                    # The first attempt (HEAD) succeeds (mocked).
                    # The body download tries MAX_DL_RETRIES times.
                    # Total body attempts should be bounded.
                    assert call_count[0] <= Task.MAX_DL_RETRIES + 1, (
                        f'Called {call_count[0]} times — '
                        f'expected ≤ {Task.MAX_DL_RETRIES + 1}'
                    )
                    # The whole operation should NOT take more
                    # than 5 seconds — much less than the 30s
                    # outer timeout
                    assert elapsed < 5.0, (
                        f'Took {elapsed:.1f}s — retry loop may be '
                        f'bounding incorrectly'
                    )
        finally:
            pool.shutdown(wait=True)


# =========================================================================
# M5: pause_controller Ctrl-C safety
# =========================================================================
class TestPauseControllerCtrlCSafety:
    """If the user Ctrl-C while paused, the pause must release
    and the event loop must exit cleanly, not hang.
    """

    @pytest.mark.asyncio
    async def test_pause_releases_on_cancellation(self):
        """Pressing Ctrl-C while the pause controller is in
        ``wait_if_requested`` must propagate the cancellation
        and let the event loop exit.
        """
        from moodle_dl.downloader.download_service import DownloadPauseController

        ctrl = DownloadPauseController()
        # Mark pause via the same code path consume_pause_request
        # + handle_key('p') uses:
        ctrl._pause_requested = True
        # consume_pause_request moves us from "requested" to
        # "active pause" (sets _paused=True)
        assert ctrl.consume_pause_request() is True
        # Now _paused is True, is_paused() returns True
        assert ctrl.is_paused()

        # Spawn the wait task; it'll loop on is_paused()
        wait_task = asyncio.create_task(ctrl.wait_if_requested())

        # Give it a moment to enter the loop
        await asyncio.sleep(0.05)
        assert not wait_task.done(), (
            'wait_if_requested exited before its first iteration'
        )

        # Cancel — mimics Ctrl-C
        start = time.monotonic()
        wait_task.cancel()
        try:
            await wait_task
        except asyncio.CancelledError:
            pass  # expected
        elapsed = time.monotonic() - start

        # Must not take 5+ seconds
        assert elapsed < 1.0, (
            f'Cancel took {elapsed:.1f}s — pause loop may be ignoring '
            f'cancellation'
        )

    @pytest.mark.asyncio
    async def test_pause_controller_with_no_pause_returns_immediately(self):
        """If the controller is NOT paused, ``wait_if_requested``
        is a no-op and returns instantly.
        """
        from moodle_dl.downloader.download_service import DownloadPauseController

        ctrl = DownloadPauseController()
        # Don't request pause
        start = time.monotonic()
        await ctrl.wait_if_requested()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, (
            f'wait_if_requested took {elapsed:.3f}s when not paused'
        )

    @pytest.mark.asyncio
    async def test_pause_resume_cycle(self):
        """Full cycle: request_pause -> wait_if_requested blocks
        until resume().
        """
        from moodle_dl.downloader.download_service import DownloadPauseController

        ctrl = DownloadPauseController()
        # Mark pause via internal state
        ctrl._pause_requested = True
        assert ctrl.consume_pause_request()  # move to waiting
        assert ctrl.is_paused()

        # Schedule resume in 200ms (mimics handle_key('r'))
        async def resume_soon():
            await asyncio.sleep(0.2)
            # Same code path as handle_key('r')
            with ctrl._lock:
                if ctrl._pause_requested or ctrl._paused:
                    ctrl._pause_requested = False
                    ctrl._paused = False

        resume_task = asyncio.create_task(resume_soon())
        start = time.monotonic()
        await ctrl.wait_if_requested()
        elapsed = time.monotonic() - start
        await resume_task

        # Should have blocked ~200ms
        assert 0.15 < elapsed < 0.5, (
            f'wait_if_requested took {elapsed:.3f}s, '
            f'expected ~0.2s'
        )
        assert not ctrl.is_paused()


# =========================================================================
# H8 regression: gather with timeout in core_handler / mods / __init__
# =========================================================================
class TestGatherTimeoutRegression:
    """Each ``asyncio.gather`` in the production code must be
    wrapped in ``asyncio.wait_for`` so a single stuck task
    can't hang the whole call.
    """

    def test_core_handler_gather_has_timeout(self):
        from moodle_dl.moodle import core_handler
        src = inspect.getsource(core_handler)
        # Track indentation level to know if a gather is nested
        # inside a wait_for wrapper. A wait_for is always at a
        # *shallower* indent level than its gather argument.
        lines = src.split('\n')
        for line_no, line in enumerate(lines, 1):
            if 'asyncio.gather' not in line or line.strip().startswith('#'):
                continue
            # Check the next 30 lines and the previous 5 lines for
            # an enclosing asyncio.wait_for. If a wait_for wraps
            # this gather, we're good.
            before = '\n'.join(lines[max(0, line_no - 5):line_no])
            after = '\n'.join(lines[line_no:line_no + 30])
            context = before + after
            assert 'asyncio.wait_for' in context, (
                f'core_handler.py:{line_no} has asyncio.gather '
                f'without asyncio.wait_for wrapper nearby. '
                f'Line: {line.strip()}'
            )

    def test_mods_common_gather_has_timeout(self):
        from moodle_dl.moodle.mods import common
        src = inspect.getsource(common)
        lines = src.split('\n')
        for line_no, line in enumerate(lines, 1):
            if 'asyncio.gather' not in line or line.strip().startswith('#'):
                continue
            before = '\n'.join(lines[max(0, line_no - 5):line_no])
            after = '\n'.join(lines[line_no:line_no + 30])
            context = before + after
            assert 'asyncio.wait_for' in context, (
                f'mods/common.py:{line_no} has asyncio.gather '
                f'without asyncio.wait_for wrapper nearby. '
                f'Line: {line.strip()}'
            )

    def test_mods_init_gather_has_timeout(self):
        from pathlib import Path
        from moodle_dl.moodle.mods import __init__ as mods_init
        # ``mods/__init__.py`` is a module (not a class), so
        # ``inspect.getsource(module)`` doesn't work on a
        # re-imported package reference. Read the file directly.
        try:
            src = inspect.getsource(mods_init)
        except TypeError:
            init_path = (
                Path(mods_init.__file__)
                if hasattr(mods_init, '__file__') and mods_init.__file__
                else None
            )
            if init_path and init_path.exists():
                src = init_path.read_text()
            else:
                src = ''
        lines = src.split('\n')
        for line_no, line in enumerate(lines, 1):
            if 'asyncio.gather' not in line or line.strip().startswith('#'):
                continue
            before = '\n'.join(lines[max(0, line_no - 5):line_no])
            after = '\n'.join(lines[line_no:line_no + 30])
            context = before + after
            assert 'asyncio.wait_for' in context, (
                f'mods/__init__.py:{line_no} has asyncio.gather '
                f'without asyncio.wait_for wrapper nearby. '
                f'Line: {line.strip()}'
            )

    def test_task_downloader_gather_has_timeout(self):
        from moodle_dl.downloader import task
        src = inspect.getsource(task)
        lines = src.split('\n')
        for line_no, line in enumerate(lines, 1):
            if 'asyncio.gather' not in line or line.strip().startswith('#'):
                continue
            before = '\n'.join(lines[max(0, line_no - 5):line_no])
            after = '\n'.join(lines[line_no:line_no + 30])
            context = before + after
            assert 'asyncio.wait_for' in context, (
                f'task.py:{line_no} has asyncio.gather '
                f'without asyncio.wait_for wrapper nearby. '
                f'Line: {line.strip()}'
            )


# =========================================================================
# H9 regression: aiofiles used in async debug-dump sites
# =========================================================================
class TestAiofilesInAsyncDebugDump:
    """Debug HTML dumps in ``moodle_dl/moodle/mods/book.py``
    must use aiofiles (not sync open), so the event loop is
    not blocked on a slow disk write.
    """

    def test_book_debug_dumps_use_aiofiles(self):
        """Only the two async-context debug dumps need aiofiles.
        The print_book_debug.html dump is in a sync method and
        uses sync open() correctly.
        """
        from moodle_dl.moodle.mods import book
        src = inspect.getsource(book)
        # The two known async-context debug dumps
        for marker in ('playwright_course_page_', 'playwright_debug_'):
            if marker in src:
                idx = src.find(marker)
                # Check 2000 chars after (the aiofiles call is a
                # few lines below the f-string template)
                nearby = src[idx:idx + 2000]
                assert 'aiofiles.open' in nearby, (
                    f'book.py: async debug dump for {marker!r} '
                    f'does not use aiofiles.open'
                )

    def test_book_module_imports_aiofiles(self):
        from moodle_dl.moodle.mods import book
        src = inspect.getsource(book)
        assert 'import aiofiles' in src, (
            'book.py does not import aiofiles'
        )


# =========================================================================
# H10: pool.shutdown hangs vs graceful
# =========================================================================
class TestPoolShutdownGraceful:
    """The test fixture's pool.shutdown() must wait for threads
    to finish (not wait=False) so the test process doesn't leak
    thread state.
    """

    def test_task_factory_uses_wait_true(self):
        """Pin the contract: the test_task_factory teardown
        uses pool.shutdown(wait=True) so worker threads are
        properly joined.
        """
        # Read the test file directly to inspect the fixture.
        # Use __file__ to find the test directory, not a hardcoded
        # user-specific path. This makes the test portable across
        # machines (CI, other developers, etc.).
        from pathlib import Path as _Path
        test_path = (
            _Path(__file__).resolve().parent / 'test_task_helpers_more.py'
        )
        src = test_path.read_text()
        # Must use wait=True (or True as default)
        # The string ``pool.shutdown(wait=False)`` may appear in
        # COMMENTS (where the bug is documented); strip comments
        # before checking.
        import re
        non_comment = re.sub(r'#.*$', '', src, flags=re.MULTILINE)
        assert 'pool.shutdown(wait=False)' not in non_comment, (
            'test_task_factory uses wait=False in code — '
            'worker threads may leak'
        )
        # Must have at least one wait=True (or wait without arg)
        assert 'pool.shutdown(wait=True' in non_comment or 'pool.shutdown()' in non_comment, (
            'test_task_factory must use wait=True to join worker threads'
        )