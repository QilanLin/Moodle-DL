# -*- coding: utf-8 -*-
"""
Tests for task.py functions related to Ctrl-C resume behavior
(commit 74f5532 / 6c2a331 / 6d62a331).

These were previously UNTESTED in the coverage report:
  - _save_incomplete_on_kill: records partial download in DB
  - _discard_incomplete_on_kill: deletes partial .part file
  - _scan_and_clean_orphan_parts: removes orphan .part files on startup
  - _remove_leganto_shortcut_fallbacks-related download flow

These tests exercise the contract using a minimal Task setup
without requiring a real Moodle connection or database.
"""
import os
import sys
import tempfile
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Helper: build a minimal Task for testing kill handlers
# =========================================================================
def _make_minimal_task():
    """Build a minimal Task instance for testing kill handlers.

    Uses MagicMock for everything except the file/dest_path methods
    needed by _save_incomplete_on_kill / _discard_incomplete_on_kill.
    """
    from moodle_dl.downloader.task import Task
    from moodle_dl.types import File, Course, TaskStatus, TaskState

    task = Task.__new__(Task)
    task.task_id = 1
    task.status = TaskStatus()
    task.status.state = TaskState.STARTED
    task.opts = MagicMock()
    task.opts.token = 'test_token'

    f = File(
        module_id=1, section_name='S', section_id=1,
        module_name='mod', content_filepath='/',
        content_filename='test.pdf',
        content_fileurl='https://example.com/test.pdf',
        content_filesize=1024, content_timemodified=0,
        module_modname='resource', content_type='file',
        content_isexternalfile=False,
        saved_to='/tmp/test.pdf',
    )
    f.file_id = 100  # Pre-existing DB row id
    task.file = f

    task.course = MagicMock()
    task.course.id = 123
    task.course.fullname = 'Test Course'

    return task


def _make_part_path(dest_path):
    """Compute the .part path from a destination path."""
    from moodle_dl.downloader.task import dest_path_to_part_path
    return dest_path_to_part_path(dest_path)


# =========================================================================
# _discard_incomplete_on_kill: deletes partial .part file
# =========================================================================
class TestDiscardIncompleteOnKill:
    """_discard_incomplete_on_kill deletes the .part file when the
    user opted into restart-incomplete-on-kill behavior.

    MUST be tolerant of secondary failures: if the disk is full or
    the file is locked, we still want to re-raise the original
    cancellation, not mask it with a cleanup error.
    """

    def test_discard_deletes_part_file(self):
        """If .part file exists, it should be deleted."""
        with tempfile.TemporaryDirectory() as td:
            task = _make_minimal_task()
            dest_path = os.path.join(td, 'test.pdf')
            part_path = _make_part_path(dest_path)
            # Pre-create .part file
            with open(part_path, 'wb') as f:
                f.write(b'partial bytes')

            asyncio.run(task._discard_incomplete_on_kill(dest_path))

            # .part should be gone
            assert not os.path.exists(part_path)

    def test_discard_no_part_file_is_noop(self):
        """If .part file doesn't exist, _discard is a no-op."""
        with tempfile.TemporaryDirectory() as td:
            task = _make_minimal_task()
            dest_path = os.path.join(td, 'test.pdf')
            # No .part file exists
            part_path = _make_part_path(dest_path)
            assert not os.path.exists(part_path)

            # Should not raise
            asyncio.run(task._discard_incomplete_on_kill(dest_path))

    def test_discard_tolerates_missing_file(self):
        """If .part file is already gone (race condition), _discard
        should not raise.
        """
        task = _make_minimal_task()
        # Path doesn't exist
        asyncio.run(task._discard_incomplete_on_kill('/nonexistent/path/file.pdf'))


# =========================================================================
# _save_incomplete_on_kill: records partial download in DB
# =========================================================================
class TestSaveIncompleteOnKill:
    """_save_incomplete_on_kill records the .part file size in the
    incomplete_downloads table so the next run can resume from byte N.
    """

    def test_save_no_part_file_is_noop(self):
        """If .part file doesn't exist, nothing is recorded."""
        with tempfile.TemporaryDirectory() as td:
            task = _make_minimal_task()
            dest_path = os.path.join(td, 'test.pdf')

            # Should not raise and not call DB
            with patch.object(task, '_save_incomplete_download') as mock_save:
                asyncio.run(task._save_incomplete_on_kill('https://example.com/test.pdf', dest_path))
                mock_save.assert_not_called()

    def test_save_empty_part_file_is_noop(self):
        """If .part file exists but is empty, nothing is recorded."""
        with tempfile.TemporaryDirectory() as td:
            task = _make_minimal_task()
            dest_path = os.path.join(td, 'test.pdf')
            part_path = _make_part_path(dest_path)
            # Create empty .part
            open(part_path, 'w').close()

            with patch.object(task, '_save_incomplete_download') as mock_save:
                asyncio.run(task._save_incomplete_on_kill('https://example.com/test.pdf', dest_path))
                mock_save.assert_not_called()

    def test_save_partial_download_records_size(self):
        """If .part has bytes, _save_incomplete_download is called
        with the correct size and URL.
        """
        with tempfile.TemporaryDirectory() as td:
            task = _make_minimal_task()
            dest_path = os.path.join(td, 'test.pdf')
            part_path = _make_part_path(dest_path)
            # Create partial download (1024 bytes)
            with open(part_path, 'wb') as f:
                f.write(b'x' * 1024)

            dl_url = 'https://example.com/test.pdf'
            with patch.object(task, '_save_incomplete_download') as mock_save:
                asyncio.run(task._save_incomplete_on_kill(dl_url, dest_path))
                # Should be called once with correct args
                mock_save.assert_called_once()
                args, kwargs = mock_save.call_args
                # First positional arg = part_path
                assert args[0] == part_path
                # Second = dl_url
                assert args[1] == dl_url
                # Third = downloaded_bytes
                assert args[2] == 1024
                # Fourth = total_bytes (0 = unknown)
                assert args[3] == 0

    def test_save_tolerates_db_failure(self):
        """If DB save fails, the kill handler should still complete
        (don't mask the original cancellation).
        """
        with tempfile.TemporaryDirectory() as td:
            task = _make_minimal_task()
            dest_path = os.path.join(td, 'test.pdf')
            part_path = _make_part_path(dest_path)
            with open(part_path, 'wb') as f:
                f.write(b'x' * 1024)

            # _save_incomplete_download raises an exception
            with patch.object(task, '_save_incomplete_download',
                              side_effect=Exception('DB locked')):
                # Should not raise (exception is caught and logged)
                asyncio.run(task._save_incomplete_on_kill('https://example.com/test.pdf', dest_path))

            # The .part file should still be on disk (for orphan sweep)
            assert os.path.exists(part_path)


# =========================================================================
# ResultBuilder gap: _is_system_file edge cases
# =========================================================================
class TestIsSystemFilePatterns:
    """Pin _is_system_file for filename patterns that are commonly
    generated by moodle-dl (book structure files, video notes, etc.)
    """

    def test_table_of_contents_html(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('Table of Contents.html') is True

    def test_metadata_json(self):
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('metadata.json') is True

    def test_anything_json_is_system(self):
        """Moodle file area names end with .json; those are system
        metadata files (questions.json, session_1.json, etc.).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('questions.json') is True
        assert ResultBuilder._is_system_file('session_1.json') is True

    def test_video_info_no_extension(self):
        """video_info (no extension) is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('video_info') is True

    def test_video_notes_md(self):
        """video_notes.md is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('video_notes.md') is True

    def test_book_chapter_metadata_json(self):
        """chapter_metadata.json is a system file (book internals)."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('chapter_metadata.json') is True

    def test_hidden_dotfile(self):
        """Files starting with . are hidden (system)."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('.hidden') is True
        assert ResultBuilder._is_system_file('.DS_Store') is True

    def test_real_pdf_is_not_system(self):
        """Real PDF content files are NOT system files."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('lecture.pdf') is False
        assert ResultBuilder._is_system_file('notes.docx') is False

    def test_real_index_html_is_not_system(self):
        """Real index.html is NOT a system file (only 'Table of
        Contents.html' specifically is).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('index.html') is False
        assert ResultBuilder._is_system_file('chapter.html') is False

    def test_case_insensitive_metadata(self):
        """_is_system_file should be case-insensitive (matches
        'METADATA.json', 'Metadata.Json', etc.).
        """
        from moodle_dl.moodle.result_builder import ResultBuilder
        # The implementation should handle case-insensitive matching
        assert ResultBuilder._is_system_file('METADATA.json') is True


# =========================================================================
# Coverage of untested result_builder lines
# =========================================================================
class TestResultBuilderUntouchedLines:
    """Pin behavior for lines previously uncovered in the coverage
    report.
    """

    def test_get_extension_from_mimetype_pdf(self):
        """get_extension_from_mimetype maps application/pdf to .pdf."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        ext = ResultBuilder._get_extension_from_mimetype('application/pdf')
        assert ext == '.pdf'

    def test_get_extension_from_mimetype_unknown(self):
        """Unknown mimetype returns empty string."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        ext = ResultBuilder._get_extension_from_mimetype('application/x-unknown')
        assert ext == ''

    def test_get_extension_from_mimetype_empty(self):
        """Empty mimetype returns empty string."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        ext = ResultBuilder._get_extension_from_mimetype('')
        assert ext == ''

    def test_get_extension_from_mimetype_image(self):
        """image/png → .png."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        ext = ResultBuilder._get_extension_from_mimetype('image/png')
        assert ext == '.png'

    def test_get_extension_from_mimetype_video(self):
        """video/mp4 → .mp4."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        ext = ResultBuilder._get_extension_from_mimetype('video/mp4')
        assert ext == '.mp4'

    def test_get_extension_from_mimetype_audio(self):
        """audio/mpeg → .mp3."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        ext = ResultBuilder._get_extension_from_mimetype('audio/mpeg')
        assert ext == '.mp3'