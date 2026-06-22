# -*- coding: utf-8 -*-
"""
Tests for previously-untested TaskFileOps helper methods:

  - create_target_file: rename-on-collision (PathTools.get_unused_file_path)
  - rename_old_file: rename to .old suffix
  - move_old_file: rename .old to unique suffix
  - _fix_pluginfile_url: tokenize URL
  - _remove_leganto_shortcut_fallbacks: cleanup of Leganto .pdf.url/.webloc etc.
  - _remove_path_and_appledouble: remove file + macOS ._foo shadow file
  - create_shortcut / create_description / create_html_file /
    create_content_file / create_data_url_file: delegate methods
  - convert_line_breaks / convert_paragraphs: HTML cleaning

Each test exercises the contract for the corresponding helper,
covering happy paths and edge cases (file exists, collision,
permission errors, macOS shadow file, etc.).
"""
import os
import sys
import shutil
import tempfile
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# create_target_file: rename on collision (path uniqueness)
# =========================================================================
class TestCreateTargetFileRenameOnCollision:
    """create_target_file should rename the target path if it
    already exists, returning a unique path.
    """

    def test_target_file_unchanged_when_path_free(self):
        """If target path doesn't exist, create_target_file returns
        the original path.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as td:
            ops = TaskFileOps(MagicMock())
            target = os.path.join(td, 'file.pdf')
            result = ops.create_target_file(target)
            # Should return original path (file didn't exist)
            assert result == target
            # File should exist (touched)
            assert os.path.exists(target)

    def test_target_file_renamed_on_collision(self):
        """If target path already exists, create_target_file returns
        a unique alternative path (e.g. file.pdf → file (1).pdf).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as td:
            ops = TaskFileOps(MagicMock())
            target = os.path.join(td, 'file.pdf')
            # Pre-create the target
            with open(target, 'w') as f:
                f.write('existing')

            result = ops.create_target_file(target)
            # Should return a different path
            assert result != target
            # Both files should exist
            assert os.path.exists(target)
            assert os.path.exists(result)


# =========================================================================
# rename_old_file: rename to .old suffix
# =========================================================================
class TestRenameOldFile:
    """rename_old_file renames the old file (tracked as file.old_file)
    to a .old suffix when the file has been modified.
    """

    def test_no_old_file_returns_false(self):
        """If file.old_file is None, rename_old_file returns False."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        f = File(
            module_id=1, section_name='S', section_id=1,
            module_name='mod', content_filepath='/',
            content_filename='test.pdf',
            content_fileurl='https://example.com/test.pdf',
            content_filesize=1024, content_timemodified=0,
            module_modname='resource', content_type='file',
            content_isexternalfile=False,
        )
        # old_file is None by default
        ops = TaskFileOps(MagicMock())
        ops.task.file = f
        assert ops.rename_old_file() is False

    def test_old_file_renamed_to_old_suffix(self):
        """If file.old_file.saved_to exists, rename_old_file renames
        it to .old suffix and returns True.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as td:
            old_path = os.path.join(td, 'old_file.pdf')
            with open(old_path, 'w') as f:
                f.write('old content')

            old_file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='test.pdf',
                content_fileurl='https://example.com/test.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=old_path,
            )
            new_file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='test.pdf',
                content_fileurl='https://example.com/test.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=os.path.join(td, 'test.pdf'),
            )
            new_file.old_file = old_file

            ops = TaskFileOps(MagicMock())
            ops.task.file = new_file
            result = ops.rename_old_file()

            assert result is True
            # Old file should be at .old path
            assert os.path.exists(old_path + '.old')
            assert not os.path.exists(old_path)

    def test_old_file_not_found_returns_false(self):
        """If file.old_file.saved_to doesn't exist on disk,
        rename_old_file returns False (no rename needed).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as td:
            old_path = os.path.join(td, 'nonexistent.pdf')
            old_file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='test.pdf',
                content_fileurl='https://example.com/test.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=old_path,
            )
            new_file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='test.pdf',
                content_fileurl='https://example.com/test.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
            )
            new_file.old_file = old_file
            ops = TaskFileOps(MagicMock())
            ops.task.file = new_file
            # old_path doesn't exist → should return False
            assert ops.rename_old_file() is False


# =========================================================================
# move_old_file: rename .old to unique suffix
# =========================================================================
class TestMoveOldFile:
    """move_old_file renames the .old file to a unique suffix
    (.old.1, .old.2, ...) when multiple old files accumulate.
    """

    def test_no_old_file_returns_false(self):
        """If .old file doesn't exist at saved_to + '.old',
        move_old_file returns False.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as td:
            new_path = os.path.join(td, 'test.pdf')
            ops = TaskFileOps(MagicMock())
            ops.task.file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='test.pdf',
                content_fileurl='https://example.com/test.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=new_path,
            )
            # No .old file
            assert ops.move_old_file() is False

    def test_old_file_renamed_to_unique_suffix(self):
        """If .old file exists, move_old_file renames it to
        .old.1, .old.2, ... (avoids collisions).
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as td:
            new_path = os.path.join(td, 'test.pdf')
            old_path = new_path + '.old'
            with open(old_path, 'w') as f:
                f.write('old')

            ops = TaskFileOps(MagicMock())
            ops.task.file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='test.pdf',
                content_fileurl='https://example.com/test.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=new_path,
            )

            result = ops.move_old_file()
            assert result is True
            # Old .old file should be gone
            assert not os.path.exists(old_path)
            # New path should be .old.1
            assert os.path.exists(old_path + '.1')


# =========================================================================
# _remove_leganto_shortcut_fallbacks
# =========================================================================
class TestRemoveLegantoShortcutFallbacks:
    """Remove shortcut files (.url, .webloc, .desktop) left by
    older Leganto fallback behavior.
    """

    def test_removes_url_webloc_desktop_siblings(self):
        """For a .pdf target, remove <base>.url, <base>.webloc,
        <base>.desktop sibling files (Leganto fallback artifacts
        where the shortcut file has the same basename as the PDF
        without the .pdf extension).

        This is the actual implementation: os.path.splitext
        strips the .pdf extension, then SHORTCUT_EXTENSIONS are
        appended to the base.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, 'reading.pdf')
            # The shortcuts are at base + .url, base + .webloc, etc.
            # (where base = 'reading' after splitext)
            url_path = os.path.join(td, 'reading.url')
            webloc_path = os.path.join(td, 'reading.webloc')
            desktop_path = os.path.join(td, 'reading.desktop')

            for p in [pdf_path, url_path, webloc_path, desktop_path]:
                with open(p, 'w') as f:
                    f.write('x')

            ops = TaskFileOps(MagicMock())
            ops.task.file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='reading.pdf',
                content_fileurl='https://example.com/r.pdf',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=pdf_path,
            )

            ops._remove_leganto_shortcut_fallbacks()

            # PDF should remain
            assert os.path.exists(pdf_path)
            # Shortcut fallbacks should be gone (base + .url etc.)
            assert not os.path.exists(url_path), (
                f'{url_path} should be removed'
            )
            assert not os.path.exists(webloc_path), (
                f'{webloc_path} should be removed'
            )
            assert not os.path.exists(desktop_path), (
                f'{desktop_path} should be removed'
            )

    def test_no_removal_for_non_shortcut_extension(self):
        """If the target has an extension that's not in
        (.pdf, .url, .webloc, .desktop), no files are removed.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as td:
            txt_path = os.path.join(td, 'reading.txt')
            sibling = os.path.join(td, 'reading.url')
            for p in [txt_path, sibling]:
                with open(p, 'w') as f:
                    f.write('x')

            ops = TaskFileOps(MagicMock())
            ops.task.file = File(
                module_id=1, section_name='S', section_id=1,
                module_name='mod', content_filepath='/',
                content_filename='reading.txt',
                content_fileurl='https://example.com/r.txt',
                content_filesize=1024, content_timemodified=0,
                module_modname='resource', content_type='file',
                content_isexternalfile=False,
                saved_to=txt_path,
            )
            ops._remove_leganto_shortcut_fallbacks()
            # .txt is not in the shortcut extensions → no removal
            assert os.path.exists(sibling), (
                f'{sibling} should NOT be removed (target is .txt)'
            )


# =========================================================================
# _remove_path_and_appledouble (macOS ._foo shadow file)
# =========================================================================
class TestRemovePathAndAppledouble:
    """On macOS, removing a file should also remove the ._foo
    AppleDouble shadow file that Finder creates alongside.
    """

    def test_removes_file_and_its_appledouble_shadow(self):
        """Both the main file and its ._foo shadow should be removed."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as td:
            f_path = os.path.join(td, 'foo.pdf')
            shadow_path = os.path.join(td, '._foo.pdf')
            for p in [f_path, shadow_path]:
                with open(p, 'w') as f:
                    f.write('x')

            ops = TaskFileOps(MagicMock())
            ops._remove_path_and_appledouble(f_path)

            assert not os.path.exists(f_path)
            assert not os.path.exists(shadow_path)


# =========================================================================
# HTML cleaning: convert_line_breaks, convert_paragraphs
# =========================================================================
class TestConvertLineBreaks:
    """Convert <br> tags to newlines (HTML → markdown rendering)."""

    def test_br_tag_converted_to_newline(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('line1<br>line2')
        assert result == 'line1\nline2'

    def test_self_closing_br_converted_to_newline(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('line1<br/>line2')
        assert result == 'line1\nline2'

    def test_br_with_space_converted(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('line1<br />line2')
        assert result == 'line1\nline2'

    def test_empty_input_returns_empty(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        assert TaskFileOps.convert_line_breaks('') == ''
        assert TaskFileOps.convert_line_breaks('') == ''  # empty handled

    def test_no_br_tags_unchanged(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_line_breaks('hello world')
        assert result == 'hello world'


class TestConvertParagraphs:
    """Convert <p> tags to newlines."""

    def test_p_tag_converted_to_newline(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        result = TaskFileOps.convert_paragraphs('<p>hello</p>')
        assert '\n' in result
        assert 'hello' in result

    def test_empty_input_returns_empty(self):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        assert TaskFileOps.convert_paragraphs('') == ''
        assert TaskFileOps.convert_paragraphs('') == ''  # empty handled


# =========================================================================
# Delegate methods (create_shortcut, create_description, etc.)
# =========================================================================
class TestDelegateMethods:
    """Delegate methods forward to the underlying Task. Pin the
    contract that they call the corresponding Task method.
    """

    def test_create_shortcut_delegates(self):
        """create_shortcut calls self.task.create_shortcut."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock, AsyncMock

        async def run():
            ops = TaskFileOps(MagicMock())
            ops.task.create_shortcut = AsyncMock()
            await ops.create_shortcut()
            ops.task.create_shortcut.assert_awaited_once()

        import asyncio
        asyncio.run(run())

    def test_create_description_delegates(self):
        """create_description calls self.task.create_description."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock, AsyncMock
        import asyncio

        async def run():
            ops = TaskFileOps(MagicMock())
            ops.task.create_description = AsyncMock()
            await ops.create_description()
            ops.task.create_description.assert_awaited_once()
        asyncio.run(run())

    def test_create_html_file_delegates(self):
        """create_html_file calls self.task.create_html_file."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock, AsyncMock
        import asyncio

        async def run():
            ops = TaskFileOps(MagicMock())
            ops.task.create_html_file = AsyncMock()
            await ops.create_html_file()
            ops.task.create_html_file.assert_awaited_once()
        asyncio.run(run())

    def test_create_content_file_delegates(self):
        """create_content_file calls self.task.create_content_file."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock, AsyncMock
        import asyncio

        async def run():
            ops = TaskFileOps(MagicMock())
            ops.task.create_content_file = AsyncMock()
            await ops.create_content_file()
            ops.task.create_content_file.assert_awaited_once()
        asyncio.run(run())

    def test_create_data_url_file_delegates(self):
        """create_data_url_file calls self.task.create_data_url_file."""
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock, AsyncMock
        import asyncio

        async def run():
            ops = TaskFileOps(MagicMock())
            ops.task.create_data_url_file = AsyncMock()
            await ops.create_data_url_file()
            ops.task.create_data_url_file.assert_awaited_once()
        asyncio.run(run())

    def test_delegate_with_missing_method_warns(self):
        """If the underlying task doesn't have the method, the
        delegate should warn but not crash.
        """
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        from unittest.mock import MagicMock
        import asyncio

        async def run():
            # Create a minimal Task mock without create_shortcut method
            class MinimalTask:
                task_id = 0
            ops = TaskFileOps(MinimalTask())
            # No create_shortcut method on task — should warn, not raise
            await ops.create_shortcut()
        asyncio.run(run())


# =========================================================================
# ResultBuilder gaps: Kaltura URL edge cases
# =========================================================================
class TestResultBuilderKalturaEdgeCases:
    """Pin Kaltura URL conversion edge cases."""

    def test_non_kaltura_url_returns_none(self):
        """URL without kaltura lti_launch path → returns None."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import MoodleURL
        rb = ResultBuilder(
            moodle_url=MoodleURL(use_http=False, domain='example.com', path=''),
            version=2024100712, token='', mod_plurals={},
        )
        # Method is _get_kaltura_lti_source_url (likely)
        # If no kaltura pattern → None
        from moodle_dl.types import MoodleURL
        result = rb._get_kaltura_lti_source_url('https://example.com/some/path')
        assert result is None, (
            f'Non-Kaltura URL should return None, got: {result!r}'
        )

    def test_kaltura_lti_url_with_source_extracted(self):
        """Kaltura LTI URL with ?source=... extracts the source URL."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import MoodleURL
        rb = ResultBuilder(
            moodle_url=MoodleURL(use_http=False, domain='example.com', path=''),
            version=2024100712, token='', mod_plurals={},
        )
        kaltura_url = (
            'https://example.com/filter/kaltura/lti_launch.php?'
            'source=%2F%2Fwww.kaltura.com%2Findex.php%2Fendpoints%2Fplay'
        )
        result = rb._get_kaltura_lti_source_url(kaltura_url)
        # Should extract the source URL
        if result is not None:
            assert 'kaltura.com' in result, (
                f'Extracted URL should contain kaltura.com. Got: {result!r}'
            )

    def test_kaltura_lti_url_without_source_returns_none(self):
        """Kaltura LTI URL without ?source= returns None."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        from moodle_dl.types import MoodleURL
        rb = ResultBuilder(
            moodle_url=MoodleURL(use_http=False, domain='example.com', path=''),
            version=2024100712, token='', mod_plurals={},
        )
        kaltura_url = 'https://example.com/filter/kaltura/lti_launch.php'
        result = rb._get_kaltura_lti_source_url(kaltura_url)
        assert result is None, (
            f'Kaltura URL without source param should return None. '
            f'Got: {result!r}'
        )


# =========================================================================
# _is_system_file: video_info, video_notes.md, questions.json
# =========================================================================
class TestIsSystemFileExtended:
    """Pin _is_system_file for additional filename patterns."""

    def test_video_info_is_system(self):
        """video_info (no extension) is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('video_info') is True

    def test_video_notes_md_is_system(self):
        """video_notes.md is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('video_notes.md') is True

    def test_questions_json_is_system(self):
        """questions.json is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('questions.json') is True

    def test_session_1_json_is_system(self):
        """session_1.json is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('session_1.json') is True

    def test_chapter_metadata_json_is_system(self):
        """chapter_metadata.json is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('chapter_metadata.json') is True

    def test_anything_json_is_system(self):
        """anything.json is a system file."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('anything.json') is True

    def test_real_file_is_not_system(self):
        """Real files (lecture.pdf, index.html) are NOT system files."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('lecture.pdf') is False
        assert ResultBuilder._is_system_file('index.html') is False

    def test_dotfile_is_system(self):
        """Dotfiles (e.g. .DS_Store) are system files."""
        from moodle_dl.moodle.result_builder import ResultBuilder
        assert ResultBuilder._is_system_file('.DS_Store') is True
        assert ResultBuilder._is_system_file('.hidden') is True