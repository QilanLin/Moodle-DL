# -*- coding: utf-8 -*-
"""
Robust kill-resilience tests for the .part resume mechanism.

This test suite verifies that when moodle-dl is killed mid-download
(via Ctrl-C, SIGTERM, asyncio cancellation), the partial .part
file is preserved on disk and a resume record is written to the
database. The next run can then resume from where it left off.

The HTTP server, DB, and task construction are all shared via
the conftest.py fixtures in tests/_support/fixtures.py. They
are imported explicitly here for clarity.
"""
import asyncio
import hashlib
import os
import sys
import urllib.parse
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')
# The tests/ dir is added to sys.path automatically by pytest. We
# import using the relative package name `_support` (since the
# tests/ dir is the parent and conftest.py already set up sys.path).
from _support.fixtures import (  # noqa: E402
    make_task_for_tests,
    query_one,
    range_http_server,
    tmp_db,
    write_part_file,
)


# -----------------------------------------------------------------------
# _save_incomplete_on_kill (the kill-time save helper)
# -----------------------------------------------------------------------
class TestSaveIncompleteOnKill:
    """Pin the behavior of the kill-time save helper."""

    @pytest.mark.asyncio
    async def test_no_part_file_means_noop(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=10)
        await task._save_incomplete_on_kill(
            'http://x/test.pdf', os.path.join(td, 'test.pdf')
        )
        assert query_one(
            recorder,
            'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 10',
        ) is None

    @pytest.mark.asyncio
    async def test_empty_part_file_skipped(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=11)
        write_part_file(td, 0)  # 0 bytes
        await task._save_incomplete_on_kill(
            'http://x/test.pdf', os.path.join(td, 'test.pdf')
        )
        assert query_one(
            recorder,
            'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 11',
        ) is None

    @pytest.mark.asyncio
    async def test_partial_part_file_saves(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=12)
        part_path = write_part_file(td, 5000)
        await task._save_incomplete_on_kill(
            'http://x/test.pdf', os.path.join(td, 'test.pdf')
        )
        row = query_one(
            recorder,
            'SELECT downloaded_bytes, total_bytes, file_path, file_url '
            'FROM incomplete_downloads WHERE file_id = 12',
        )
        assert row is not None
        downloaded, total, path, url = row
        assert downloaded == 5000
        assert total == 0
        assert path == part_path
        assert url == 'http://x/test.pdf'

    @pytest.mark.asyncio
    async def test_save_failure_does_not_propagate(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=13)
        part_path = write_part_file(td, 1000)
        with patch.object(
            task, '_save_incomplete_download',
            side_effect=RuntimeError('DB locked'),
        ):
            await task._save_incomplete_on_kill(
                'http://x/test.pdf', os.path.join(td, 'test.pdf')
            )
        # .part file still on disk
        assert os.path.exists(part_path)
        assert os.path.getsize(part_path) == 1000


# -----------------------------------------------------------------------
# download_url's catch + re-raise behavior
# -----------------------------------------------------------------------
class TestDownloadUrlCatchesCancellation:
    """download_url must catch CancelledError/KeyboardInterrupt/SystemExit,
    save the .part, and re-raise."""

    @pytest.mark.asyncio
    async def test_cancellederror_saves_part_and_reraises(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=20)
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
        row = query_one(
            recorder,
            'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 20',
        )
        assert row[0] == 4096

    @pytest.mark.asyncio
    async def test_keyboardinterrupt_saves_part(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=21)
        part_path = write_part_file(td, 2048)

        async def fake_impl(*args, **kwargs):
            raise KeyboardInterrupt('Ctrl-C')

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            with pytest.raises(KeyboardInterrupt):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

        assert os.path.exists(part_path)
        row = query_one(
            recorder,
            'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 21',
        )
        assert row[0] == 2048

    @pytest.mark.asyncio
    async def test_systemexit_saves_part(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=22)
        part_path = write_part_file(td, 1024)

        async def fake_impl(*args, **kwargs):
            raise SystemExit(1)

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            with pytest.raises(SystemExit):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

        assert os.path.exists(part_path)
        row = query_one(
            recorder,
            'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 22',
        )
        assert row[0] == 1024

    @pytest.mark.asyncio
    async def test_normal_completion_does_not_save_incomplete(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=23)

        async def fake_impl(*args, **kwargs):
            return None, 100, 100, None

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            await task.download_url(
                'http://x/test.pdf', os.path.join(td, 'test.pdf')
            )

        # No incomplete row
        row = query_one(
            recorder,
            'SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = 23',
        )
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_other_exceptions_do_not_trigger_kill_save(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=24)
        part_path = write_part_file(td, 1024)

        async def fake_impl(*args, **kwargs):
            raise ValueError('not a kill signal')

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            with pytest.raises(ValueError):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

        # Kill-save path NOT triggered
        row = query_one(
            recorder,
            'SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = 24',
        )
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_cancellation_closes_open_file_handle(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=25)
        fake_handle = AsyncMock()
        fake_handle.closed = False

        async def fake_impl(*args, **kwargs):
            task._open_file_handle = fake_handle
            raise asyncio.CancelledError('mid-cancel')

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            with pytest.raises(asyncio.CancelledError):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

        # Handle closed
        fake_handle.close.assert_awaited_once()
        # Attribute reset
        assert task._open_file_handle is None


# -----------------------------------------------------------------------
# End-to-end: kill during real download, then resume
# -----------------------------------------------------------------------
class TestE2EKillDuringDownload:
    """End-to-end with a real local HTTP server: start a download,
    inject a kill, verify .part + DB row exist, then resume."""

    @pytest.mark.asyncio
    async def test_kill_at_5mb_resume_to_10mb(self, tmp_db):
        td, recorder = tmp_db
        file_size = 10 * 1024 * 1024  # 10 MB
        file_content = bytes(i % 256 for i in range(file_size))
        expected_hash = hashlib.sha256(file_content).hexdigest()
        dest_path = os.path.join(td, 'test.pdf')
        part_path = os.path.join(td, 'test.pdf.part')

        with range_http_server(file_content) as (base_url, server):
            task, _ = make_task_for_tests(recorder, file_id=30)

            # 1. Pre-write 5MB to .part (simulate kill at 5MB)
            partial = 5 * 1024 * 1024
            with open(part_path, 'wb') as f:
                f.write(file_content[:partial])

            # 2. Inject CancelledError during download
            async def fake_impl(*args, **kwargs):
                raise asyncio.CancelledError('kill at 5MB')

            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(asyncio.CancelledError):
                    await task.download_url(f'{base_url}/test.pdf', dest_path)

            # Verify state after kill
            assert os.path.exists(part_path)
            assert os.path.getsize(part_path) == partial
            row = query_one(
                recorder,
                'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 30',
            )
            assert row[0] == partial

            # 3. Restart: do the Range request
            req = urllib.request.Request(
                f'{base_url}/test',
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


# -----------------------------------------------------------------------
# Multiple kills in sequence
# -----------------------------------------------------------------------
class TestMultipleKills:
    """Pressing Ctrl-C many times must be idempotent."""

    @pytest.mark.asyncio
    async def test_many_cancels_keep_one_record_with_latest_size(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=40)
        part_path = write_part_file(td, 1000)
        cancel_count = [0]

        async def fake_impl(*args, **kwargs):
            cancel_count[0] += 1
            with open(part_path, 'wb') as f:
                f.write(b'x' * (1000 + cancel_count[0] * 100))
            raise asyncio.CancelledError(f'kill #{cancel_count[0]}')

        for _ in range(5):
            with patch.object(task, '_download_url_impl', side_effect=fake_impl):
                with pytest.raises(asyncio.CancelledError):
                    await task.download_url(
                        'http://x/test.pdf', os.path.join(td, 'test.pdf')
                    )

        # 1 record with the last (largest) size
        row = query_one(
            recorder,
            'SELECT downloaded_bytes, file_path FROM incomplete_downloads WHERE file_id = 40',
        )
        assert row[0] == 1500
        assert row[1] == part_path


# -----------------------------------------------------------------------
# Cancel during the actual file write
# -----------------------------------------------------------------------
class TestCancelDuringFileWrite:
    """The most dangerous case: a CancelledError raised inside the
    file write itself."""

    @pytest.mark.asyncio
    async def test_cancel_mid_write_still_saves(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=50)
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
            task._open_file_handle = fake_handle
            raise asyncio.CancelledError('mid-write cancel')

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            with pytest.raises(asyncio.CancelledError):
                await task.download_url(
                    'http://x/test.pdf', os.path.join(td, 'test.pdf')
                )

        # Handle closed
        assert fake_handle.closed
        # .part on disk
        assert os.path.exists(os.path.join(td, 'test.pdf.part'))
        # DB record
        row = query_one(
            recorder,
            'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 50',
        )
        assert row[0] == 1700


# -----------------------------------------------------------------------
# File handle attribute cleanup
# -----------------------------------------------------------------------
class TestFileHandleAttributeCleanup:
    """The self._open_file_handle attribute lifecycle."""

    def test_initial_value_is_none(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder)
        assert task._open_file_handle is None

    @pytest.mark.asyncio
    async def test_attribute_cleared_after_normal_completion(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=60)
        fake_handle = AsyncMock()
        fake_handle.closed = False

        async def fake_impl(*args, **kwargs):
            task._open_file_handle = fake_handle
            return None, 100, 100, None

        with patch.object(task, '_download_url_impl', side_effect=fake_impl):
            await task.download_url(
                'http://x/test.pdf', os.path.join(td, 'test.pdf')
            )
        # Attribute reset
        assert task._open_file_handle is None

    @pytest.mark.asyncio
    async def test_attribute_cleared_after_cancel(self, tmp_db):
        td, recorder = tmp_db
        task, _ = make_task_for_tests(recorder, file_id=61)
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
        # Attribute reset
        assert task._open_file_handle is None


# -----------------------------------------------------------------------
# scan_for_orphan_part_files integration
# -----------------------------------------------------------------------
class TestScanForOrphanPartFilesIntegration:
    """When the DB save failed (e.g. on kill -9), the .part file is
    on disk without a DB row. The orphan scan should find it."""

    def test_orphan_part_file_with_no_db_row(self, tmp_db):
        from moodle_dl.downloader.task import scan_for_orphan_part_files
        td, recorder = tmp_db
        part_path = write_part_file(td, 5000)
        orphans = scan_for_orphan_part_files(td, recorder)
        assert len(orphans) == 1
        assert orphans[0][0] == part_path

    def test_tracked_part_file_not_in_orphans(self, tmp_db):
        from moodle_dl.downloader.task import scan_for_orphan_part_files
        td, recorder = tmp_db
        part_path = write_part_file(td, 5000)
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
