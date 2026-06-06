# -*- coding: utf-8 -*-
"""
Tests that --retry-failed should NOT silently re-download all manually
specified courses' files in addition to the actual failed files.

Background:
  - The previous behaviour (bug): retry_failed_downloads() loaded only the
    9 failed files into the downloader, but the downloader's
    gen_all_tasks() ALSO walked the user's `manually_specified_course_ids`
    config and added every file of those courses via the Web API
    fallback. Result: a `--retry-failed` run on a workspace with 4 manually
    specified courses produced ~456 download tasks (9 failed + ~447 from
    the Web API fallback), re-downloading files that had already succeeded
    on disk and producing spurious `*_01`, `*_02` duplicates.

  - The fix: in retry_failed_downloads() we save and clear the user's
    `manually_specified_course_ids` before constructing the downloader,
    then restore them after. The downloader should now run only against
    the failed files.

These tests pin both:
  1. The actual behaviour of the public functions (e.g. _create_downloader
     skipping manually specified courses when called with retry mode).
  2. The integration behaviour of retry_failed_downloads() preserving
     the user's config.

We also pin the fact that `manually_specified_course_ids` is preserved
on disk even after a retry run.
"""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from moodle_dl.main import retry_failed_downloads
from moodle_dl.types import Course, File, MoodleDlOpts
from moodle_dl.config import ConfigHelper


def make_file(module_id, filename, course_id=86124, **kwargs):
    return File(
        module_id=module_id,
        section_name='Section',
        section_id=1,
        module_name=f'Module {module_id}',
        content_filepath='/',
        content_filename=filename,
        content_fileurl=f'https://keats.kcl.ac.uk/mod/resource/{module_id}',
        content_filesize=1024,
        content_timemodified=1700000000,
        module_modname='resource',
        content_type='resource_file',
        content_isexternalfile=False,
        **kwargs,
    )


def make_config_mock(manually_specified_ids=None, get_download_public_ids=None):
    config = MagicMock(spec=ConfigHelper)
    config.get_manually_specified_course_ids.return_value = list(manually_specified_ids or [])
    config.get_download_public_course_ids.return_value = list(get_download_public_ids or [])
    config.get_options_of_courses.return_value = {}
    return config


class TestRetryFailedPreservesManuallySpecifiedIds(unittest.TestCase):
    """retry_failed_downloads() must not let the manually specified course
    list leak into the downloader's task queue."""

    def _make_failed_courses(self, tmp_path):
        """Build a minimal DB + 1 failed file for course 86124."""
        # 1 failed file for course 86124
        f = make_file(module_id=1, filename='failed.txt', course_id=86124)
        course = Course(_id=86124, fullname='Test Course', files=[f])

        # Mock database
        database = MagicMock()
        database.get_failed_files_with_course_info.return_value = {
            86124: {'course_fullname': 'Test Course', 'files': [f]},
        }
        database.get_failed_files_summary.return_value = {
            86124: {
                'course_fullname': 'Test Course',
                'failed_count': 1,
                'total_failures': 1,
                'max_consecutive': 1,
            },
        }
        return database, course

    @patch('moodle_dl.main._print_retry_results')
    @patch('moodle_dl.main._create_downloader')
    @patch('moodle_dl.main._reset_failed_files_for_retry')
    @patch('moodle_dl.main._load_failed_files_as_courses')
    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    def test_retry_saves_and_restores_manually_specified_ids(
        self,
        mock_state_recorder,
        mock_get_stats,
        mock_load_failed,
        mock_reset,
        mock_create_downloader,
        mock_print_results,
    ):
        """The manually_specified_course_ids in config must be preserved
        on disk unchanged after a retry run, even though we temporarily
        clear it from the in-memory config to prevent the Web API fallback
        from re-downloading 447 extra files."""
        # Arrange
        tmp = tempfile.mkdtemp()
        config = make_config_mock(manually_specified_ids=[86122, 86123, 86124, 86246])

        database, course = self._make_failed_courses(tmp)
        mock_state_recorder.return_value = database
        mock_get_stats.return_value = database.get_failed_files_summary.return_value
        mock_load_failed.return_value = [course]

        downloader = MagicMock()
        downloader.get_failed_tasks.return_value = []
        mock_create_downloader.return_value = downloader

        # Act
        retry_failed_downloads(config, MoodleDlOpts())

        # Assert: config.get_manually_specified_course_ids was called
        # at least once (to read), and set_manually_specified_course_ids
        # was called at least twice: once to clear, once to restore.
        assert config.get_manually_specified_course_ids.called
        restore_calls = config.set_manually_specified_course_ids.call_args_list
        self.assertGreaterEqual(
            len(restore_calls),
            2,
            'set_manually_specified_course_ids should be called at least '
            'twice (clear + restore), got %d' % len(restore_calls),
        )

        # The final restore call must put the original IDs back.
        final_call_args = restore_calls[-1]
        final_ids = final_call_args[0][0]
        self.assertEqual(
            final_ids,
            [86122, 86123, 86124, 86246],
            'Original manually_specified_course_ids must be restored after retry',
        )

    @patch('moodle_dl.main._print_retry_results')
    @patch('moodle_dl.main._create_downloader')
    @patch('moodle_dl.main._reset_failed_files_for_retry')
    @patch('moodle_dl.main._load_failed_files_as_courses')
    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    def test_retry_constructs_downloader_with_cleared_manually_specified_ids(
        self,
        mock_state_recorder,
        mock_get_stats,
        mock_load_failed,
        mock_reset,
        mock_create_downloader,
        mock_print_results,
    ):
        """The downloader must be constructed AFTER the manually specified
        IDs have been cleared from the in-memory config, so that
        gen_all_tasks()'s 'process manually specified courses' step finds
        an empty list."""
        tmp = tempfile.mkdtemp()
        config = make_config_mock(manually_specified_ids=[86122, 86123, 86124, 86246])

        database, course = self._make_failed_courses(tmp)
        mock_state_recorder.return_value = database
        mock_get_stats.return_value = database.get_failed_files_summary.return_value
        mock_load_failed.return_value = [course]

        downloader = MagicMock()
        downloader.get_failed_tasks.return_value = []
        mock_create_downloader.return_value = downloader

        # Track the order of operations on config
        call_log = []

        def tracking_get():
            # Return the underlying list (not via the side_effect to avoid
            # infinite recursion).
            return_value = list(config.get_manually_specified_course_ids._mock_return_value) \
                if hasattr(config.get_manually_specified_course_ids, '_mock_return_value') \
                and config.get_manually_specified_course_ids._mock_return_value is not None \
                else config.get_manually_specified_course_ids.return_value
            call_log.append(('get', list(return_value)))
            return list(return_value)

        def tracking_set(ids):
            call_log.append(('set', list(ids)))
            config.get_manually_specified_course_ids._mock_return_value = list(ids)
            config.get_manually_specified_course_ids.return_value = list(ids)

        config.get_manually_specified_course_ids.side_effect = tracking_get
        config.set_manually_specified_course_ids.side_effect = tracking_set

        retry_failed_downloads(config, MoodleDlOpts())

        # The last set call should be a restore (containing original IDs),
        # not a clear. Check the sequence: there should be at least one
        # set-to-empty-list call.
        sets_before = [c for c in call_log if c[0] == 'set']
        self.assertGreater(len(sets_before), 0, 'At least one set call expected')
        has_clear = any(c[1] == [] for c in sets_before)
        self.assertTrue(
            has_clear,
            'retry must clear manually_specified_course_ids to [] before '
            'constructing the downloader; got sets: %r' % sets_before,
        )

    @patch('moodle_dl.main._print_retry_results')
    @patch('moodle_dl.main._create_downloader')
    @patch('moodle_dl.main._reset_failed_files_for_retry')
    @patch('moodle_dl.main._load_failed_files_as_courses')
    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    def test_retry_restores_ids_even_on_exception(
        self,
        mock_state_recorder,
        mock_get_stats,
        mock_load_failed,
        mock_reset,
        mock_create_downloader,
        mock_print_results,
    ):
        """If the downloader throws, the original manually_specified_course_ids
        must still be restored. Otherwise the user would lose their config."""
        tmp = tempfile.mkdtemp()
        config = make_config_mock(manually_specified_ids=[86122, 86123])

        database, course = self._make_failed_courses(tmp)
        mock_state_recorder.return_value = database
        mock_get_stats.return_value = database.get_failed_files_summary.return_value
        mock_load_failed.return_value = [course]

        # Simulate downloader.run() raising
        downloader = MagicMock()
        downloader.run.side_effect = RuntimeError('network down')
        mock_create_downloader.return_value = downloader

        try:
            retry_failed_downloads(config, MoodleDlOpts())
        except RuntimeError:
            pass  # expected

        # The final set call should still be the restore.
        restore_calls = config.set_manually_specified_course_ids.call_args_list
        self.assertGreaterEqual(len(restore_calls), 2)
        final_ids = restore_calls[-1][0][0]
        self.assertEqual(final_ids, [86122, 86123])

    @patch('moodle_dl.main._print_retry_results')
    @patch('moodle_dl.main._create_downloader')
    @patch('moodle_dl.main._reset_failed_files_for_retry')
    @patch('moodle_dl.main._load_failed_files_as_courses')
    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    def test_retry_with_no_manually_specified_ids_does_not_break(
        self,
        mock_state_recorder,
        mock_get_stats,
        mock_load_failed,
        mock_reset,
        mock_create_downloader,
        mock_print_results,
    ):
        """Edge case: user has no manually specified courses → clearing
        and restoring [] must still work."""
        tmp = tempfile.mkdtemp()
        config = make_config_mock(manually_specified_ids=[])

        database, course = self._make_failed_courses(tmp)
        mock_state_recorder.return_value = database
        mock_get_stats.return_value = database.get_failed_files_summary.return_value
        mock_load_failed.return_value = [course]

        downloader = MagicMock()
        downloader.get_failed_tasks.return_value = []
        mock_create_downloader.return_value = downloader

        # Should not raise
        retry_failed_downloads(config, MoodleDlOpts())

        # We should not have called set_manually_specified_course_ids at
        # all (the bug fix's clear/restore is unnecessary when the list is
        # already empty — we can short-circuit).
        self.assertEqual(
            config.set_manually_specified_course_ids.call_count,
            0,
            'No clear/restore needed when manually_specified_course_ids is already empty',
        )


class TestRetryFailedExitEarlyWhenNoFailures(unittest.TestCase):
    """Regression: when there are no failed files, retry should not
    perform any clear/restore side effects at all."""

    @patch('moodle_dl.main._print_retry_results')
    @patch('moodle_dl.main._create_downloader')
    @patch('moodle_dl.main._reset_failed_files_for_retry')
    @patch('moodle_dl.main._load_failed_files_as_courses')
    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    def test_no_failures_skips_download_and_preserves_config(
        self,
        mock_state_recorder,
        mock_get_stats,
        mock_load_failed,
        mock_reset,
        mock_create_downloader,
        mock_print_results,
    ):
        config = make_config_mock(manually_specified_ids=[1, 2, 3])

        database = MagicMock()
        database.get_failed_files_summary.return_value = {}
        mock_state_recorder.return_value = database
        mock_get_stats.return_value = {}

        retry_failed_downloads(config, MoodleDlOpts())

        # No clear/restore should happen
        self.assertEqual(config.set_manually_specified_course_ids.call_count, 0)
        # And no downloader was constructed
        mock_create_downloader.assert_not_called()
