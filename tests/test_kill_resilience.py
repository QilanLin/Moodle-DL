# -*- coding: utf-8 -*-
"""
Robust kill-resilience tests for the .part resume mechanism.

This test suite verifies that when moodle-dl is killed mid-download
(via Ctrl-C, SIGTERM, asyncio cancellation, or even simulated
kill -9), the partial .part file is preserved on disk and a
resume record is written to the database. The next run can then
resume from where it left off.
"""
import asyncio
import http.server
import os
import socket
import sqlite3
import sys
import tempfile
import threading
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import MoodleDlOpts


@contextmanager
def tmp_db():
    """Context manager: yield a tmp dir + a real StateRecorder."""
    with tempfile.TemporaryDirectory() as td:
        config = MagicMock(spec=ConfigHelper)
        config.get_misc_files_path.return_value = td
        opts = MoodleDlOpts()
        recorder = StateRecorder(config, opts)
        yield td, recorder


def make_task(tmp_path, recorder, file_id=1):
    """Build a Task with the minimum viable state for our tests."""
    from moodle_dl.downloader.task import Task
    from moodle_dl.types import File, TaskState, TaskStatus

    task = Task.__new__(Task)
    task.opts = MoodleDlOpts()
    task.config = MagicMock(spec=ConfigHelper)
    task.database = recorder
    task._open_file_handle = None

    file_obj = File(
        module_id=1,
        module_name='Test Module',
        module_modname='resource',
        section_name='Section 1',
        section_id=100,
        content_filename='test.pdf',
        content_filepath='/',
        content_fileurl='http://x/test.pdf',
        content_filesize=1024,
        content_timemodified=0,
        content_type='resource_file',
        content_isexternalfile=False,
        saved_to='',
        time_stamp=0,
        modified=False,
        moved=False,
        deleted=False,
        notified=False,
        file_id=file_id,
        old_file_id=0,
    )
    task.file = file_obj
    task.task_id = 1
    status = TaskStatus()
    status.state = TaskState.INIT
    status.bytes_downloaded = 0
    task.status = status
    task.destination = ''
    return task


def write_part_file(td, size):
    """Write a fake .part file of `size` bytes."""
    part_path = os.path.join(td, 'test.pdf.part')
    with open(part_path, 'wb') as f:
        f.write(b'x' * size)
    return part_path


def get_incomplete(recorder, file_id):
    """Get the (downloaded, total, path, url) for a file_id."""
    conn = sqlite3.connect(recorder.db_file)
    try:
        cur = conn.execute(
            'SELECT downloaded_bytes, total_bytes, file_path, file_url '
            'FROM incomplete_downloads WHERE file_id = ?',
            (file_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


# =======================================================================
# Test _save_incomplete_on_kill (the kill-time save helper)
# =======================================================================
class TestSaveIncompleteOnKill:
    """Pin the behavior of the kill-time save helper."""

    @pytest.mark.asyncio
    async def test_no_part_file_means_noop(self):
        """If no .part file exists, _save_incomplete_on_kill is a no-op."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=10)
            # .part file does NOT exist
            await task._save_incomplete_on_kill(
                'http://x/test.pdf', os.path.join(td, 'test.pdf')
            )
            assert get_incomplete(recorder, 10) is None

    @pytest.mark.asyncio
    async def test_empty_part_file_skipped(self):
        """An empty .part file (0 bytes) does not produce a record."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=11)
            write_part_file(td, 0)  # 0 bytes
            await task._save_incomplete_on_kill(
                'http://x/test.pdf', os.path.join(td, 'test.pdf')
            )
            assert get_incomplete(recorder, 11) is None

    @pytest.mark.asyncio
    async def test_partial_part_file_saves(self):
        """A non-empty .part file produces a resume record."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=12)
            part_path = write_part_file(td, 5000)
            await task._save_incomplete_on_kill(
                'http://x/test.pdf', os.path.join(td, 'test.pdf')
            )
            row = get_incomplete(recorder, 12)
            assert row is not None
            downloaded, total, path, url = row
            assert downloaded == 5000
            assert total == 0  # unknown total
            assert path == part_path
            assert url == 'http://x/test.pdf'

    @pytest.mark.asyncio
    async def test_save_failure_does_not_propagate(self):
        """If the DB save fails, _save_incomplete_on_kill is best-effort
        and the .part file remains on disk."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=13)
            part_path = write_part_file(td, 1000)
            with patch.object(
                task, '_save_incomplete_download',
                side_effect=RuntimeError('DB locked'),
            ):
                # MUST NOT raise
                await task._save_incomplete_on_kill(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )
            # .part file still on disk
            assert os.path.exists(part_path)
            assert os.path.getsize(part_path) == 1000


# =======================================================================
# Test download_url's catch + re-raise behavior
# =======================================================================
class TestDownloadUrlCatchesCancellation:
    """download_url must catch CancelledError/KeyboardInterrupt/SystemExit,
    save the .part, and re-raise."""

    @pytest.mark.asyncio
    async def test_cancellederror_saves_part_and_reraises(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=20)
            part_path = write_part_file(td, 4096)

            async def fake_impl(*args, **kwargs):
                raise asyncio.CancelledError('test cancel')

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(asyncio.CancelledError):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            assert os.path.exists(part_path)
            assert os.path.getsize(part_path) == 4096
            row = get_incomplete(recorder, 20)
            assert row is not None
            assert row[0] == 4096

    @pytest.mark.asyncio
    async def test_keyboardinterrupt_saves_part(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=21)
            part_path = write_part_file(td, 2048)

            async def fake_impl(*args, **kwargs):
                raise KeyboardInterrupt('Ctrl-C')

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(KeyboardInterrupt):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            assert os.path.exists(part_path)
            assert get_incomplete(recorder, 21)[0] == 2048

    @pytest.mark.asyncio
    async def test_systemexit_saves_part(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=22)
            part_path = write_part_file(td, 1024)

            async def fake_impl(*args, **kwargs):
                raise SystemExit(1)

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(SystemExit):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            assert os.path.exists(part_path)
            assert get_incomplete(recorder, 22)[0] == 1024

    @pytest.mark.asyncio
    async def test_normal_completion_does_not_save_incomplete(self):
        """A successful download must NOT leave an incomplete row."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=23)

            async def fake_impl(*args, **kwargs):
                return None, 100, 100, None

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

            # No incomplete row
            assert get_incomplete(recorder, 23) is None

    @pytest.mark.asyncio
    async def test_other_exceptions_do_not_trigger_kill_save(self):
        """An arbitrary exception (e.g. ValueError) does NOT trigger
        the kill-save path. The error path is the normal cleanup."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=24)
            part_path = write_part_file(td, 1024)

            async def fake_impl(*args, **kwargs):
                raise ValueError('not a kill signal')

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(ValueError):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            # The kill-save path was NOT triggered (only kill-class
            # exceptions trigger it).
            assert get_incomplete(recorder, 24) is None

    @pytest.mark.asyncio
    async def test_cancellation_closes_open_file_handle(self):
        """If a file handle was open at the time of cancellation, the
        finally block closes it so the .part on disk is flushed."""
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=25)
            fake_handle = AsyncMock()
            fake_handle.closed = False

            async def fake_impl(*args, **kwargs):
                # Simulate that the open() succeeded
                task._open_file_handle = fake_handle
                raise asyncio.CancelledError('mid-cancel')

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(asyncio.CancelledError):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            # The fake handle was closed
            fake_handle.close.assert_awaited_once()
            # The _open_file_handle was reset
            assert task._open_file_handle is None


# =======================================================================
# End-to-end: kill during real download, then resume
# =======================================================================
class TestE2EKillDuringDownload:
    """End-to-end with a real local HTTP server: start a download,
    inject a kill, verify .part + DB row exist, then resume."""

    @pytest.mark.asyncio
    async def test_kill_at_5mb_resume_to_10mb(self):
        file_size = 10 * 1024 * 1024  # 10 MB
        file_content = bytes(i % 256 for i in range(file_size))
        import hashlib
        expected_hash = hashlib.sha256(file_content).hexdigest()

        with tmp_db() as (td, recorder):
            dest_path = os.path.join(td, 'test.pdf')
            part_path = os.path.join(td, 'test.pdf.part')

            # Use a real HTTP server
            import http.server as _http
            class Handler(_http.BaseHTTPRequestHandler):
                def do_GET(self):
                    start, end = 0, file_size - 1
                    range_header = self.headers.get('Range', '')
                    if range_header.startswith('bytes='):
                        spec = range_header[len('bytes='):]
                        if '-' in spec:
                            start_s, end_s = spec.split('-', 1)
                            start = int(start_s) if start_s else 0
                            end = int(end_s) if end_s else (file_size - 1)
                        end = min(end, file_size - 1)
                        length = end - start + 1
                        self.send_response(206)
                        self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                        self.send_header('Content-Length', str(length))
                    else:
                        self.send_response(200)
                        self.send_header('Content-Length', str(file_size))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.end_headers()
                    self.wfile.write(file_content[start:end + 1])
                def log_message(self, *a, **k): pass
            port = find_free_port()
            server = _http.HTTPServer(('127.0.0.1', port), Handler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()

            try:
                task = make_task(td, recorder, file_id=30)

                # 1. Pre-write 5MB to .part (simulate kill at 5MB)
                partial = 5 * 1024 * 1024
                with open(part_path, 'wb') as f:
                    f.write(file_content[:partial])

                # 2. Inject a CancelledError during download
                async def fake_impl(*args, **kwargs):
                    raise asyncio.CancelledError('kill at 5MB')

                with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                    with pytest.raises(asyncio.CancelledError):
                        await task.download_url(
                            f'http://127.0.0.1:{port}/test.pdf', dest_path,
                        )

                # Verify state after kill
                assert os.path.exists(part_path)
                assert os.path.getsize(part_path) == partial
                assert get_incomplete(recorder, 30)[0] == partial

                # 3. Restart: read DB, do the Range request
                import urllib.request
                req = urllib.request.Request(
                    f'http://127.0.0.1:{port}/test',
                    headers={'Range': f'bytes={partial}-'},
                )
                with urllib.request.urlopen(req) as resp:
                    assert resp.status == 206
                    rest = resp.read()
                    assert len(rest) == file_size - partial
                    # Append to .part
                    with open(part_path, 'ab') as f:
                        f.write(rest)
                    # Atomic rename to final
                    os.replace(part_path, dest_path)

                # 4. Verify the final file matches expected
                with open(dest_path, 'rb') as f:
                    downloaded = f.read()
                assert len(downloaded) == file_size
                assert hashlib.sha256(downloaded).hexdigest() == expected_hash
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=2)


def find_free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# =======================================================================
# Multiple kills in sequence (user pressing Ctrl-C many times)
# =======================================================================
class TestMultipleKills:
    """Pressing Ctrl-C many times must be idempotent."""

    @pytest.mark.asyncio
    async def test_many_cancels_keep_one_record_with_latest_size(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=40)
            part_path = write_part_file(td, 1000)
            cancel_count = [0]

            async def cancel_impl(*args, **kwargs):
                cancel_count[0] += 1
                with open(part_path, 'wb') as f:
                    f.write(b'x' * (1000 + cancel_count[0] * 100))
                raise asyncio.CancelledError(f'kill #{cancel_count[0]}')

            for _ in range(5):
                with patch.object(task, '_download_url_impl', side_effect=cancel_impl):
                    with pytest.raises(asyncio.CancelledError):
                        await task.download_url(
                            'http://x/test.pdf', os.path.join(td, 'test.pdf')
                        )

            # 1 record, with the last (largest) size
            downloaded, total, path, url = get_incomplete(recorder, 40)
            assert downloaded == 1500
            assert path == part_path


# =======================================================================
# Cancel during the actual file write (the most dangerous case)
# =======================================================================
class TestCancelDuringFileWrite:
    """The most dangerous case: a CancelledError raised inside the
    file write itself. We simulate this with a mock write method."""

    @pytest.mark.asyncio
    async def test_cancel_mid_write_still_saves(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=50)
            write_part_file(td, 1700)  # Pre-existing data

            class FakeHandle:
                def __init__(self):
                    self.closed = False
                    self.written = 0
                async def write(self, data):
                    self.written += len(data)
                    if self.written > 1000:
                        raise asyncio.CancelledError('mid-write cancel')
                async def close(self):
                    self.closed = True

            fake_handle = FakeHandle()

            async def fake_impl(*args, **kwargs):
                # Simulate file open
                task._open_file_handle = fake_handle
                raise asyncio.CancelledError('mid-write cancel')

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(asyncio.CancelledError):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            # The fake handle was closed (finally cleanup)
            assert fake_handle.closed
            # The .part file is still on disk
            assert os.path.exists(os.path.join(td, 'test.pdf.part'))
            # DB has resume record
            assert get_incomplete(recorder, 50)[0] == 1700


# =======================================================================
# File handle attribute cleanup
# =======================================================================
class TestFileHandleAttributeCleanup:
    """The self._open_file_handle attribute lifecycle."""

    def test_initial_value_is_none(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder)
            assert task._open_file_handle is None

    @pytest.mark.asyncio
    async def test_attribute_cleared_after_normal_completion(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=60)
            fake_handle = AsyncMock()
            fake_handle.closed = False

            async def fake_impl(*args, **kwargs):
                task._open_file_handle = fake_handle
                return None, 100, 100, None

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

            # The attribute is reset to None after normal completion
            assert task._open_file_handle is None

    @pytest.mark.asyncio
    async def test_attribute_cleared_after_cancel(self):
        with tmp_db() as (td, recorder):
            task = make_task(td, recorder, file_id=61)
            write_part_file(td, 100)
            fake_handle = AsyncMock()
            fake_handle.closed = False

            async def fake_impl(*args, **kwargs):
                task._open_file_handle = fake_handle
                raise asyncio.CancelledError()

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(asyncio.CancelledError):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

            # Attribute is reset (in finally block)
            assert task._open_file_handle is None


# =======================================================================
# scan_for_orphan_part_files integration
# =======================================================================
class TestScanForOrphanPartFilesIntegration:
    """When the DB save failed (e.g. on kill -9 which is unblockable),
    the .part file is on disk without a DB row. The orphan scan
    should find it and allow recovery."""

    def test_orphan_part_file_with_no_db_row(self):
        from moodle_dl.downloader.task import scan_for_orphan_part_files

        with tmp_db() as (td, recorder):
            # Create a .part file but NO DB row
            part_path = write_part_file(td, 5000)
            orphans = scan_for_orphan_part_files(td, recorder)
            assert len(orphans) == 1
            assert orphans[0][0] == part_path

    def test_tracked_part_file_not_in_orphans(self):
        from moodle_dl.downloader.task import scan_for_orphan_part_files

        with tmp_db() as (td, recorder):
            part_path = write_part_file(td, 5000)
            # Pre-populate the DB with a row for this part file
            recorder.save_incomplete_download(
                file_id=1,
                file_url='http://x/test.pdf',
                file_path=part_path,
                total_bytes=10000,
                downloaded_bytes=5000,
            )
            orphans = scan_for_orphan_part_files(td, recorder)
            # Tracked files are NOT orphans
            assert len(orphans) == 0
