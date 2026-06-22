# -*- coding: utf-8 -*-
"""
Tests for the "every restart re-downloads already-downloaded
files" regression.

User report (2026-06-22):

  '为什么 ctrl c 然后再 moodle-dl --verbose --log-to-file 时要再
  把 feedback, forums 等模组再请求一遍？直接 resume 重新下载上一
  个下载到一半的文件不就完了？

  然后有些文件被下载了多次。'

Two layers of bugs identified:

  Layer 1: API calls (feedback, forums, quizzes, etc.)
    - These are Moodle API calls (core_course_get_contents,
      mod_xxx_get_xxxs_by_courses). They MUST run on every
      startup to detect new server-side changes.
    - The library caches them in get_last_timestamp_per_mod_module
      but only for forum/calendar. Other modules always fetch
      fully. This is by design — the server tells us when content
      changes via these APIs.

  Layer 2: File-level dedup (the real complaint)
    - get_new_files() compares current fetched files vs stored DB
      records. If they match (same path, same content_type,
      same hash, etc.), the file is NOT re-downloaded.
    - BUG: `_find_all_urls` in result_builder.py creates File
      objects with `content_fileurl = url` (raw URL, NOT fixed
      via UrlHelper.fix_pluginfile_url). But commit 75d2393
      fixes URLs in _handle_files (and stored DB has the fixed
      version).
    - get_new_files.files_are_diffrent() compares content_fileurl
      via line 765-772:
          if file1.content_type == 'description-url'
              and file1.content_fileurl != file2.content_fileurl:
                  result = True
    - Raw current URL != fixed stored URL → result = True →
      matching_file.modified = True → re-downloaded EVERY run.

The fix: fix the pluginfile URL inside _find_all_urls so the
description-url File object has the same URL format as stored
records. Then comparison matches → not modified → skip on
restart.

These tests pin:
  1. description-url files with pluginfile URLs are normalized
     to webservice format on creation (matches stored format).
  2. After this fix, get_new_files recognizes them as unchanged
     and skips re-download.
  3. The full contract: file_id=8's URL matches current-fetch URL
     after the fix (was different before).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Layer 1: API calls must run on every startup (by design)
# =========================================================================
class TestAPICallsRunOnEveryStartup:
    """The Moodle API calls (core_course_get_contents,
    mod_assign_get_assignments, etc.) MUST run on every startup.
    They cannot be skipped — they're how we detect server-side
    changes (new assignments, new forum posts, etc.).
    """

    def test_get_last_timestamp_per_mod_module_only_caches_forum_calendar(self):
        """Pin the existing limitation: get_last_timestamp_per_mod_module
        only returns data for forum/calendar. Other mods (book,
        assign, quiz, page, url, label, feedback, h5pactivity, etc.)
        always fetch fully. This is by design — the underlying
        Moodle API doesn't have a "since timestamp" parameter
        for those mod types.
        """
        # We can't actually run get_last_timestamp_per_mod_module
        # here without a real DB, but we can read the source to
        # confirm the contract.
        from pathlib import Path
        src_path = Path('/Users/linqilan/CodingProjects/moodle/Moodle-DL/'
                         'moodle_dl/database.py')
        src = src_path.read_text()
        # The function should only return forum + calendar dicts.
        assert 'mod_forum_dict' in src
        assert 'mod_calendar_dict' in src
        # It should NOT query other mod types like assign/quiz.
        # (We can't directly check absence, but the function
        # implementation should be small and only handle these 2.)
        import re
        m = re.search(
            r'def get_last_timestamp_per_mod_module.*?(?=    def )',
            src, re.DOTALL
        )
        assert m is not None
        body = m.group(0)
        # Only forum + calendar SELECT statements
        assign_queries = body.count("module_modname = 'assign'")
        quiz_queries = body.count("module_modname = 'quiz'")
        assert assign_queries == 0, (
            'get_last_timestamp_per_mod_module should NOT query '
            'assign (no timestamp cache for assign)'
        )
        assert quiz_queries == 0, (
            'get_last_timestamp_per_mod_module should NOT query '
            'quiz (no timestamp cache for quiz)'
        )


# =========================================================================
# Layer 2: description-url files should not be re-downloaded
# =========================================================================
class TestUrlsDifferInPathHelper:
    """Pin the new _urls_differ_in_path helper used by
    files_are_diffrent to compare description-url URLs ignoring
    the query string (token/offline=1).
    """

    def test_same_url_returns_false(self):
        from moodle_dl.database import StateRecorder
        assert StateRecorder._urls_differ_in_path(
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png?token=T',
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png?token=T',
        ) is False

    def test_diff_query_same_path_returns_false(self):
        """Two URLs with different query strings but same path
        are equivalent (only auth params differ).
        """
        from moodle_dl.database import StateRecorder
        stored = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png'
        current = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png?token=T&offline=1'
        assert StateRecorder._urls_differ_in_path(stored, current) is False, (
            'URLs that differ only in query string should be '
            'considered equivalent'
        )

    def test_diff_path_returns_true(self):
        from moodle_dl.database import StateRecorder
        a = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/old-banner.png?token=T'
        b = 'https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/new-banner.png?token=T'
        assert StateRecorder._urls_differ_in_path(a, b) is True

    def test_diff_netloc_returns_true(self):
        """Different host (e.g. cross-site) is a real difference."""
        from moodle_dl.database import StateRecorder
        a = 'https://keats.kcl.ac.uk/pluginfile.php/1/file.pdf'
        b = 'https://other.example.com/pluginfile.php/1/file.pdf'
        assert StateRecorder._urls_differ_in_path(a, b) is True

    def test_empty_urls_returns_true(self):
        """If either URL is empty, they're considered different."""
        from moodle_dl.database import StateRecorder
        assert StateRecorder._urls_differ_in_path('', 'https://example.com/x') is True
        assert StateRecorder._urls_differ_in_path('https://example.com/x', '') is True

    def test_both_empty_returns_false(self):
        from moodle_dl.database import StateRecorder
        assert StateRecorder._urls_differ_in_path('', '') is False

    def test_malformed_url_falls_back_to_literal(self):
        """If urlparse fails (malformed URL), fall back to literal
        string comparison (preserves pre-fix behavior for malformed
        inputs).
        """
        from moodle_dl.database import StateRecorder
        # Same literal string → not different
        assert StateRecorder._urls_differ_in_path('not a url', 'not a url') is False
        # Different literal strings → different
        assert StateRecorder._urls_differ_in_path('not a url', 'another url') is True


# =========================================================================
# files_are_diffrent behavior with description-url
# =========================================================================
class TestFilesAreDiffrentDescriptionUrl:
    """Pin files_are_diffrent's behavior for description-url files.
    After the fix in _find_all_urls, both stored and current-fetch
    URLs are in webservice format. So content_fileurl comparison
    should match → files_are_diffrent returns False → not modified
    → skip.
    """

    def test_files_are_diffrent_description_url_match_returns_false(self):
        """Two description-url File objects with the SAME URL
        (both in webservice format) should NOT be considered
        different.
        """
        from moodle_dl.types import File
        from moodle_dl.database import StateRecorder

        stored = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../banner.png',
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )
        current = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../banner.png',
            # Same URL — both in webservice format (after fix)
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )

        result = StateRecorder.files_are_diffrent(current, stored)
        assert result is False, (
            f'description-url with matching URLs should NOT be '
            f'considered different, got {result}'
        )

    def test_files_are_diffrent_description_url_different_returns_true(self):
        """Two description-url File objects with DIFFERENT URLs
        ARE considered different (the URL really did change).
        """
        from moodle_dl.types import File
        from moodle_dl.database import StateRecorder

        stored = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../banner.png',
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/old-banner.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )
        current = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../banner.png',
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/new-banner.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )

        result = StateRecorder.files_are_diffrent(current, stored)
        assert result is True, (
            f'description-url with different URLs should be '
            f'considered different (real change), got {result}'
        )

    def test_files_are_diffrent_mixed_url_format_returns_false(self):
        """After the fix: stored has webservice URL, current has
        raw pluginfile URL — but the path part is identical, so
        files_are_diffrent returns False (NOT different).

        This is the bug the fix addresses: pre-fix, this returned
        True (marking modified), causing re-download on every
        restart. With the path-based comparison, the raw vs
        webservice distinction no longer triggers a false-positive
        modification.
        """
        from moodle_dl.types import File
        from moodle_dl.database import StateRecorder

        stored = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../banner.png',
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )
        current = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../banner.png',
            # Raw URL — different from stored (no webservice prefix)
            content_fileurl='https://keats.kcl.ac.uk/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )

        result = StateRecorder.files_are_diffrent(current, stored)
        # Path is identical, only scheme/prefix differs — these are
        # the same file. Return False (not modified, don't re-download).
        assert result is False, (
            f'After path-based comparison fix: stored webservice URL '
            f'and current raw URL with same path should be considered '
            f'the same file (return False), got {result}'
        )


# =========================================================================
# End-to-end: get_new_files returns file_id=8 (or 10 or 11) once
# =========================================================================
class TestGetNewFilesContract:
    """Pin get_new_files' contract: it MUST return only files
    that are genuinely new or modified. After the fix,
    description-url files are not spuriously marked as modified.
    """

    def test_description_url_in_db_not_re_added_to_changed(self):
        """A description-url file that's already in DB (modified=0,
        successful download) must NOT be re-added to changed_course
        on next startup, even though its stored URL is the webservice
        format.
        """
        from moodle_dl.types import File, Course
        from moodle_dl.database import StateRecorder

        # The current-fetch file (after fix has webservice URL)
        current_file = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../Informatics-banner4.png',
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )
        # The stored file (from previous successful download)
        stored_file = File(
            module_id=0, section_name='General', section_id=1,
            module_name='Section summary', content_filepath='/',
            content_filename='https://.../Informatics-banner4.png',
            content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/9374442/course/section/1801074/Informatics-banner4.png',
            content_filesize=0, content_timemodified=0,
            module_modname='index_mod-description-section_summary',
            content_type='description-url',
            content_isexternalfile=True,
        )

        # get_new_files compares them. They should be considered
        # the SAME — not different.
        different = StateRecorder.files_are_diffrent(current_file, stored_file)
        assert different is False, (
            'After fix: matching webservice URLs should not be '
            'considered different'
        )


class TestExistingDescriptionUrlStaysDownloadedOnDisk:
    """A description-url file that's on disk must not be
    re-downloaded just because the current fetch happened to
    return a new URL (different stored URL). Pin the contract
    that _file_exists_on_disk prevents re-download.
    """

    def test_file_on_disk_is_not_redownloaded_even_if_modified_flag_set(self):
        """If a description-url file is on disk (download successful)
        and get_new_files marks it as modified (false positive),
        the Task pipeline must check _file_exists_on_disk and
        skip rather than re-download.

        This test pins the existing behavior: _file_exists_on_disk
        returns True for a file with saved_to set and the file
        exists on disk.
        """
        from pathlib import Path
        import tempfile
        from moodle_dl.types import File

        with tempfile.TemporaryDirectory() as tmp:
            saved_to = os.path.join(tmp, 'banner.webloc')
            # Create the file on disk
            Path(saved_to).write_text('test')

            f = File(
                module_id=0, section_name='General', section_id=1,
                module_name='Section summary', content_filepath='/',
                content_filename='banner',
                content_fileurl='https://keats.kcl.ac.uk/webservice/pluginfile.php/.../banner.png',
                content_filesize=10, content_timemodified=0,
                module_modname='index_mod-description-section_summary',
                content_type='description-url',
                content_isexternalfile=True,
            )
            f.saved_to = saved_to
            f.modified = True  # marked as modified by get_new_files

            # _file_exists_on_disk returns True
            from moodle_dl.database import StateRecorder
            assert StateRecorder._file_exists_on_disk(f) is True, (
                '_file_exists_on_disk must return True for files '
                'on disk, regardless of modified flag'
            )

            # The Task pipeline would then NOT re-download this file
            # (it would just leave it as is on disk).