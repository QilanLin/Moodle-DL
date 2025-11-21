# -*- coding: utf-8 -*-
"""
Unit tests for atomized retry_failed_downloads functions

Testing that each function in retry_failed_downloads has a single responsibility.
"""

import unittest
from unittest.mock import MagicMock, patch
from moodle_dl.main import (
    _get_failed_download_statistics,
    _print_failed_statistics_header,
    _print_failed_statistics_details,
    _load_failed_files_as_courses,
    _reset_failed_files_for_retry,
    _create_downloader,
    _print_retry_results,
)


class TestRetryFailedDownloadsAtomization(unittest.TestCase):
    """Test atomized retry_failed_downloads functions"""

    def test_get_failed_download_statistics(self):
        """Test getting failed download statistics from database"""
        database = MagicMock()
        expected_summary = {
            1: {
                'course_name': 'Course 1',
                'failed_count': 5,
                'total_failures': 10,
                'max_consecutive': 3
            }
        }
        database.get_failed_files_summary.return_value = expected_summary
        
        result = _get_failed_download_statistics(database)
        
        self.assertEqual(result, expected_summary)
        database.get_failed_files_summary.assert_called_once()

    def test_get_failed_download_statistics_empty(self):
        """Test getting statistics when none exist"""
        database = MagicMock()
        database.get_failed_files_summary.return_value = {}
        
        result = _get_failed_download_statistics(database)
        
        self.assertEqual(result, {})

    @patch('moodle_dl.main.logging')
    def test_print_failed_statistics_header(self, mock_logging):
        """Test printing header with statistics"""
        summary = {
            1: {
                'course_name': 'Course 1',
                'failed_count': 5,
                'total_failures': 10,
                'max_consecutive': 3
            },
            2: {
                'course_name': 'Course 2',
                'failed_count': 3,
                'total_failures': 6,
                'max_consecutive': 2
            }
        }
        
        _print_failed_statistics_header(summary)
        
        # Should call logging.info multiple times
        self.assertGreater(mock_logging.info.call_count, 0)
        # Check that total failed files (8) appears in calls
        all_calls = str(mock_logging.info.call_args_list)
        self.assertIn('8', all_calls)  # 5 + 3
        self.assertIn('16', all_calls)  # 10 + 6

    @patch('moodle_dl.main.logging')
    def test_print_failed_statistics_details(self, mock_logging):
        """Test printing detailed statistics"""
        summary = {
            1: {
                'course_name': 'Test Course',
                'failed_count': 5,
                'total_failures': 10,
                'max_consecutive': 3
            }
        }
        
        _print_failed_statistics_details(summary)
        
        # Should call logging.info multiple times with course details
        self.assertGreater(mock_logging.info.call_count, 0)
        all_calls = str(mock_logging.info.call_args_list)
        self.assertIn('Test Course', all_calls)
        self.assertIn('5', all_calls)  # failed_count

    def test_load_failed_files_as_courses_success(self):
        """Test loading failed files as courses"""
        database = MagicMock()
        courses_dict = {
            1: {
                'course_fullname': 'Course 1',
                'files': [MagicMock(file_id=10), MagicMock(file_id=11)]
            },
            2: {
                'course_fullname': 'Course 2',
                'files': [MagicMock(file_id=20)]
            }
        }
        database.get_failed_files_with_course_info.return_value = courses_dict
        
        courses = _load_failed_files_as_courses(database)
        
        self.assertEqual(len(courses), 2)
        self.assertEqual(courses[0].fullname, 'Course 1')
        self.assertEqual(courses[1].fullname, 'Course 2')
        self.assertEqual(len(courses[0].files), 2)
        self.assertEqual(len(courses[1].files), 1)

    @patch('moodle_dl.main.logging')
    def test_load_failed_files_as_courses_empty(self, mock_logging):
        """Test loading when no failed files"""
        database = MagicMock()
        database.get_failed_files_with_course_info.return_value = {}
        
        courses = _load_failed_files_as_courses(database)
        
        self.assertEqual(courses, [])
        mock_logging.warning.assert_called_once()

    @patch('moodle_dl.main.logging')
    def test_reset_failed_files_for_retry(self, mock_logging):
        """Test resetting failed files status"""
        database = MagicMock()
        
        course1 = MagicMock()
        course1.id = 1
        file1 = MagicMock()
        file2 = MagicMock()
        course1.files = [file1, file2]
        
        course2 = MagicMock()
        course2.id = 2
        file3 = MagicMock()
        course2.files = [file3]
        
        courses = [course1, course2]
        
        _reset_failed_files_for_retry(database, courses)
        
        # Should call reset_failed_file_for_retry 3 times
        self.assertEqual(database.reset_failed_file_for_retry.call_count, 3)
        mock_logging.info.assert_called()

    def test_create_downloader_real_download(self):
        """Test creating downloader for real download"""
        from moodle_dl.downloader.download_service import DownloadService
        
        courses = []
        config = MagicMock()
        config.get_restricted_filenames.return_value = []
        opts = MagicMock()
        opts.without_downloading_files = False
        opts.download_chunk_size = 8192
        opts.max_parallel_yt_dlp = 2
        opts.cookies_text = None
        opts.global_opts = MagicMock()
        opts.global_opts.skip_cert_verify = False
        database = MagicMock()
        database.get_incomplete_downloads_for_retry.return_value = []
        database.cleanup_old_incomplete_downloads.return_value = 0
        
        downloader = _create_downloader(courses, config, opts, database)
        
        self.assertIsInstance(downloader, DownloadService)

    def test_create_downloader_fake_download(self):
        """Test creating downloader for dry-run (no download)"""
        from moodle_dl.downloader.fake_download_service import FakeDownloadService
        
        courses = []
        config = MagicMock()
        config.get_restricted_filenames.return_value = []
        opts = MagicMock()
        opts.without_downloading_files = True
        opts.download_chunk_size = 8192
        opts.max_parallel_yt_dlp = 2
        opts.cookies_text = None
        opts.global_opts = MagicMock()
        opts.global_opts.skip_cert_verify = False
        database = MagicMock()
        database.get_incomplete_downloads_for_retry.return_value = []
        database.cleanup_old_incomplete_downloads.return_value = 0
        
        downloader = _create_downloader(courses, config, opts, database)
        
        self.assertIsInstance(downloader, FakeDownloadService)

    @patch('moodle_dl.main.logging')
    def test_print_retry_results_all_success(self, mock_logging):
        """Test printing results when all succeed"""
        new_failed_downloads = []
        
        _print_retry_results(new_failed_downloads)
        
        # Should print success message
        all_calls = str(mock_logging.info.call_args_list)
        self.assertIn('所有失败的文件已成功重新下载', all_calls)

    @patch('moodle_dl.main.logging')
    def test_print_retry_results_with_failures(self, mock_logging):
        """Test printing results when some files still fail"""
        task1 = MagicMock()
        task1.file.content_filename = 'file1.pdf'
        task1.status.get_error_text.return_value = 'Connection error'
        
        task2 = MagicMock()
        task2.file.content_filename = 'file2.pdf'
        task2.status.get_error_text.return_value = 'Server error'
        
        new_failed_downloads = [task1, task2]
        
        _print_retry_results(new_failed_downloads)
        
        # Should print warning with count
        all_calls = str(mock_logging.warning.call_args_list)
        self.assertIn('2', all_calls)
        self.assertIn('file1.pdf', all_calls)
        self.assertIn('file2.pdf', all_calls)


class TestRetryFailedDownloadsFlow(unittest.TestCase):
    """Test the overall flow of retry_failed_downloads"""

    @patch('moodle_dl.main._print_retry_results')
    @patch('moodle_dl.main._create_downloader')
    @patch('moodle_dl.main._reset_failed_files_for_retry')
    @patch('moodle_dl.main._load_failed_files_as_courses')
    @patch('moodle_dl.main._print_failed_statistics_details')
    @patch('moodle_dl.main._print_failed_statistics_header')
    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.logging')
    def test_retry_flow_with_failures(
        self,
        mock_logging,
        mock_state_recorder,
        mock_get_stats,
        mock_print_header,
        mock_print_details,
        mock_load_courses,
        mock_reset_files,
        mock_create_downloader,
        mock_print_results,
    ):
        """Test the complete flow when failures exist"""
        from moodle_dl.main import retry_failed_downloads
        
        config = MagicMock()
        opts = MagicMock()
        
        # Setup database mock
        database_mock = MagicMock()
        mock_state_recorder.return_value = database_mock
        
        # Setup statistics
        summary = {1: {'course_name': 'Course 1', 'failed_count': 5}}
        mock_get_stats.return_value = summary
        
        # Setup courses
        course_mock = MagicMock()
        course_mock.id = 1
        mock_load_courses.return_value = [course_mock]
        
        # Setup downloader
        downloader_mock = MagicMock()
        downloader_mock.get_failed_tasks.return_value = []
        mock_create_downloader.return_value = downloader_mock
        
        # Call function
        retry_failed_downloads(config, opts)
        
        # Verify all steps were called
        mock_state_recorder.assert_called_once()
        mock_get_stats.assert_called_once()
        mock_print_header.assert_called_once()
        mock_print_details.assert_called_once()
        mock_load_courses.assert_called_once()
        mock_reset_files.assert_called_once()
        mock_create_downloader.assert_called_once()
        downloader_mock.run.assert_called_once()
        mock_print_results.assert_called_once()

    @patch('moodle_dl.main._get_failed_download_statistics')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.logging')
    def test_retry_flow_no_failures(
        self,
        mock_logging,
        mock_state_recorder,
        mock_get_stats,
    ):
        """Test the complete flow when no failures exist"""
        from moodle_dl.main import retry_failed_downloads
        
        config = MagicMock()
        opts = MagicMock()
        
        # Setup database mock
        database_mock = MagicMock()
        mock_state_recorder.return_value = database_mock
        
        # Setup empty statistics
        mock_get_stats.return_value = None
        
        # Call function
        retry_failed_downloads(config, opts)
        
        # Verify early exit
        mock_state_recorder.assert_called_once()
        mock_get_stats.assert_called_once()
        # Should log no failures message
        mock_logging.info.assert_called()


if __name__ == '__main__':
    unittest.main()

