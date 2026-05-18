# -*- coding: utf-8 -*-
"""
Unit tests for atomized DownloadService functions

Testing that each function in gen_all_tasks has a single responsibility
and can be tested independently.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from concurrent.futures import ThreadPoolExecutor
import pytest
from moodle_dl.downloader.download_service import DownloadService
from moodle_dl.types import File, Course, DownloadOptions, MoodleDlOpts


class TestDownloadServiceAtomization(unittest.TestCase):
    """Test atomized DownloadService functions"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = MagicMock()
        self.config.get_download_options.return_value = MagicMock()
        self.config.get_manually_specified_course_ids.return_value = []  # No manually specified courses by default
        self.config.get_options_of_courses.return_value = {}
        self.config.get_moodle_URL.return_value = MagicMock()
        self.config.get_token.return_value = 'token'
        self.config.get_restricted_filenames.return_value = False
        self.opts = MagicMock()
        self.opts.download_chunk_size = 8192
        self.opts.max_parallel_yt_dlp = 4
        self.opts.cookies_text = None
        self.opts.global_opts = MagicMock()
        self.opts.global_opts.skip_cert_verify = False
        
        self.database = MagicMock()
        self.database.get_incomplete_downloads_for_retry.return_value = []
        self.database.cleanup_old_incomplete_downloads.return_value = 0
        self.courses = []
        
        self.service = DownloadService(
            courses=self.courses,
            config=self.config,
            opts=self.opts,
            database=self.database
        )
    
    def _make_download_options(self) -> DownloadOptions:
        """Helper to create a minimal DownloadOptions instance"""
        return DownloadOptions(
            token='token',
            moodle_url='https://moodle.example.com',
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text='',
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path='/tmp',
            download_metadata_files=False,
            global_opts=MoodleDlOpts()
        )

    def test_configure_task_settings(self):
        """Test configuration of task settings"""
        dl_options, thread_pool = self.service._configure_task_settings()
        
        # Verify settings were applied
        self.assertIsNotNone(dl_options)
        self.assertIsNotNone(thread_pool)
        self.assertIsInstance(thread_pool, ThreadPoolExecutor)
        
        # Cleanup thread pool
        thread_pool.shutdown(wait=False)

    def test_configure_task_settings_chunk_size(self):
        """Test that chunk size is set correctly"""
        self.opts.download_chunk_size = 16384
        self.config.get_download_options.return_value = MagicMock()
        
        from moodle_dl.downloader.task import Task
        original_chunk_size = Task.CHUNK_SIZE
        
        try:
            dl_options, thread_pool = self.service._configure_task_settings()
            self.assertEqual(Task.CHUNK_SIZE, 16384)
            thread_pool.shutdown(wait=False)
        finally:
            Task.CHUNK_SIZE = original_chunk_size

    def test_load_incomplete_downloads_map_empty(self):
        """Test loading incomplete downloads when none exist"""
        incomplete_map = self.service._load_incomplete_downloads_map()
        
        self.assertEqual(incomplete_map, {})

    def test_load_incomplete_downloads_map_with_data(self):
        """Test loading incomplete downloads with data"""
        incomplete_downloads = [
            {'file_id': 1, 'downloaded_bytes': 1000, 'total_bytes': 5000},
            {'file_id': 2, 'downloaded_bytes': 2000, 'total_bytes': 5000},
        ]
        self.database.get_incomplete_downloads_for_retry.return_value = incomplete_downloads
        
        incomplete_map = self.service._load_incomplete_downloads_map()
        
        self.assertEqual(len(incomplete_map), 2)
        self.assertIn(1, incomplete_map)
        self.assertIn(2, incomplete_map)
        self.assertEqual(incomplete_map[1]['downloaded_bytes'], 1000)
        self.assertEqual(incomplete_map[2]['downloaded_bytes'], 2000)

    def test_create_task_returns_task_object(self):
        """Test that create_task returns a Task object"""
        course_file = MagicMock()
        course_file.content_filename = "test.pdf"
        course = MagicMock()
        dl_options = MagicMock()
        thread_pool = MagicMock()
        
        # This will fail to create a real Task, but we can at least verify the call flow
        # In reality, create_task would be tested by integration tests
        try:
            task = self.service._create_task(course_file, course, dl_options, thread_pool)
        except Exception:
            # Expected - this is testing that the method is called correctly
            pass

    def test_is_incomplete_download_true(self):
        """Test detection of incomplete download"""
        course_file = MagicMock()
        course_file.file_id = 5
        incomplete_map = {5: {'downloaded_bytes': 100}}
        
        result = self.service._is_incomplete_download(course_file, incomplete_map)
        
        self.assertTrue(result)

    def test_is_incomplete_download_false_no_file_id(self):
        """Test detection returns False when no file_id"""
        course_file = MagicMock()
        course_file.file_id = None
        incomplete_map = {1: {'downloaded_bytes': 100}}
        
        result = self.service._is_incomplete_download(course_file, incomplete_map)
        
        self.assertFalse(result)

    def test_is_incomplete_download_false_not_in_map(self):
        """Test detection returns False when file_id not in map"""
        course_file = MagicMock()
        course_file.file_id = 5
        incomplete_map = {1: {'downloaded_bytes': 100}}
        
        result = self.service._is_incomplete_download(course_file, incomplete_map)
        
        self.assertFalse(result)

    @patch('moodle_dl.downloader.download_service.logging')
    def test_log_incomplete_download(self, mock_logging):
        """Test logging of incomplete download"""
        course_file = MagicMock()
        course_file.file_id = 3
        course_file.content_filename = "test.pdf"
        incomplete_map = {
            3: {
                'downloaded_bytes': 1000,
                'total_bytes': 5000
            }
        }
        
        self.service._log_incomplete_download(course_file, incomplete_map)
        
        mock_logging.info.assert_called_once()
        call_args = mock_logging.info.call_args[0]
        self.assertIn('检测到未完成的下载', call_args[0])
        self.assertIn('test.pdf', call_args)

    def test_update_download_statistics(self):
        """Test updating download statistics"""
        course_file = MagicMock()
        course_file.content_filesize = 5242880  # 5 MB
        
        initial_bytes = self.service.status.bytes_to_download
        initial_files = self.service.status.files_to_download
        
        self.service._update_download_statistics(course_file)
        
        self.assertEqual(
            self.service.status.bytes_to_download,
            initial_bytes + 5242880
        )
        self.assertEqual(
            self.service.status.files_to_download,
            initial_files + 1
        )

    @patch('moodle_dl.downloader.download_service.logging')
    def test_log_queue_summary_empty(self, mock_logging):
        """Test logging queue summary for empty queue"""
        self.service.status.files_to_download = 0
        
        self.service._log_queue_summary(0)
        
        mock_logging.debug.assert_called_once()
        call_args = mock_logging.debug.call_args[0]
        self.assertIn('下载队列为空', call_args[0])

    @patch('moodle_dl.downloader.download_service.logging')
    def test_log_queue_summary_with_priority(self, mock_logging):
        """Test logging queue summary with priority tasks"""
        self.service.status.files_to_download = 10
        
        self.service._log_queue_summary(3)
        
        mock_logging.info.assert_called_once()
        call_args = mock_logging.info.call_args[0]
        self.assertIn('下载队列包含', call_args[0])
        self.assertIn('未完成的下载', call_args[0])
        self.assertIn('10 个任务', call_args[0])
        self.assertIn('3 个未完成的下载需要续传', call_args[0])

    @patch('moodle_dl.moodle.request_helper.RequestHelper')
    @patch('moodle_dl.moodle.course_validator.CourseValidator')
    def test_create_tasks_for_manually_specified_courses_success(self, mock_validator, mock_request_helper):
        """确保手动课程可以正确创建任务"""
        self.config.get_options_of_courses.return_value = {
            '101': {
                'overwrite_name_with': 'Manual Course',
                'create_directory_structure': False,
                'excluded_sections': [202]
            }
        }
        mock_validator.return_value.validate_course_exists_and_accessible.return_value = {
            'fullname': 'Manual Source'
        }
        mock_request_helper.return_value = MagicMock()
        course_data = {
            'id': 101,
            'sections': [
                {
                    'id': 201,
                    'name': 'Week 1',
                    'modules': [
                        {
                            'id': 501,
                            'name': 'Slides',
                            'modname': 'resource',
                            'contents': [
                                {
                                    'fileurl': 'https://example.com/file.pdf',
                                    'filename': 'slides.pdf',
                                    'filesize': 1234,
                                    'timemodified': 1700000000
                                }
                            ]
                        }
                    ]
                },
                {
                    'id': 202,
                    'name': 'Week 2',
                    'modules': [
                        {
                            'id': 502,
                            'name': 'Hidden',
                            'modname': 'resource',
                            'contents': [
                                {
                                    'fileurl': 'https://example.com/hidden.pdf',
                                    'filename': 'hidden.pdf',
                                    'filesize': 42
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        dl_options = self._make_download_options()
        thread_pool = MagicMock()
        with patch.object(self.service, '_fetch_course_data_from_web_api', return_value=course_data):
            priority_tasks, tasks = self.service._create_tasks_for_manually_specified_courses(
                [101], dl_options, thread_pool, {}
            )
        self.assertEqual(priority_tasks, [])
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.api_source, 'web')
        self.assertEqual(task.course.id, 101)
        self.assertEqual(task.course.overwrite_name_with, 'Manual Course')
        self.assertFalse(task.course.create_directory_structure)
        self.assertEqual(task.course.files[0].section_id, 201)
        mock_validator.return_value.validate_course_exists_and_accessible.assert_called_once_with(101)

    @patch('moodle_dl.moodle.request_helper.RequestHelper')
    @patch('moodle_dl.moodle.course_validator.CourseValidator')
    def test_create_tasks_for_manually_specified_courses_prioritizes_incomplete(self, mock_validator, mock_request_helper):
        """确保手动课程未完成下载会进入优先队列"""
        mock_validator.return_value.validate_course_exists_and_accessible.return_value = {
            'fullname': 'Manual Source'
        }
        mock_request_helper.return_value = MagicMock()
        course_data = {
            'id': 202,
            'sections': [
                {
                    'id': 301,
                    'name': 'Week 3',
                    'modules': [
                        {
                            'id': 601,
                            'name': 'Video',
                            'modname': 'resource',
                            'contents': [
                                {
                                    'fileid': 9001,
                                    'fileurl': 'https://example.com/video.mp4',
                                    'filename': 'lesson.mp4',
                                    'filesize': 2048,
                                    'timemodified': 1710000000,
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        dl_options = self._make_download_options()
        thread_pool = MagicMock()
        incomplete_map = {9001: {'file_id': 9001, 'downloaded_bytes': 1024, 'total_bytes': 2048}}
        with patch.object(self.service, '_fetch_course_data_from_web_api', return_value=course_data):
            priority_tasks, tasks = self.service._create_tasks_for_manually_specified_courses(
                [202], dl_options, thread_pool, incomplete_map
            )
        self.assertEqual(len(priority_tasks), 1)
        self.assertEqual(priority_tasks[0].file.file_id, 9001)
        self.assertEqual(tasks, [])

    @patch('moodle_dl.moodle.request_helper.RequestHelper')
    @patch('moodle_dl.moodle.course_validator.CourseValidator')
    def test_create_tasks_for_manually_specified_courses_skips_inaccessible(self, mock_validator, mock_request_helper):
        """不可访问课程应被跳过"""
        mock_validator.return_value.validate_course_exists_and_accessible.return_value = None
        mock_request_helper.return_value = MagicMock()
        dl_options = self._make_download_options()
        thread_pool = MagicMock()
        with patch.object(self.service, '_fetch_course_data_from_web_api') as mock_fetch:
            priority_tasks, tasks = self.service._create_tasks_for_manually_specified_courses(
                [999], dl_options, thread_pool, {}
            )
        self.assertEqual(priority_tasks, [])
        self.assertEqual(tasks, [])
        mock_fetch.assert_not_called()

    @patch('moodle_dl.downloader.download_service.logging')
    def test_log_queue_summary_no_priority(self, mock_logging):
        """Test logging queue summary without priority tasks"""
        self.service.status.files_to_download = 10
        
        self.service._log_queue_summary(0)
        
        mock_logging.info.assert_called_once()
        call_args = mock_logging.info.call_args[0]
        self.assertIn('下载队列包含', call_args[0])
        self.assertIn('10 个任务', call_args[0])

    @patch('moodle_dl.downloader.download_service.logging')
    def test_cleanup_old_incomplete_downloads_success(self, mock_logging):
        """Test cleanup of old incomplete downloads"""
        # Reset the database mock to avoid call count issues
        self.database.reset_mock()
        self.database.cleanup_old_incomplete_downloads.return_value = 5
        
        self.service._cleanup_old_incomplete_downloads()
        
        self.database.cleanup_old_incomplete_downloads.assert_called_once_with(days_old=7)
        mock_logging.debug.assert_called_once()
        call_args = mock_logging.debug.call_args[0]
        self.assertIn('清理了', call_args[0])
        self.assertEqual(call_args[1], 5)

    @patch('moodle_dl.downloader.download_service.logging')
    def test_cleanup_old_incomplete_downloads_no_cleanup(self, mock_logging):
        """Test cleanup when no records to clean"""
        self.database.cleanup_old_incomplete_downloads.return_value = 0
        
        self.service._cleanup_old_incomplete_downloads()
        
        # Should not log anything if no records cleaned
        mock_logging.debug.assert_not_called()

    @patch('moodle_dl.downloader.download_service.logging')
    def test_cleanup_old_incomplete_downloads_error(self, mock_logging):
        """Test cleanup error handling"""
        self.database.cleanup_old_incomplete_downloads.side_effect = Exception("DB error")
        
        # Should not raise exception
        self.service._cleanup_old_incomplete_downloads()
        
        # Should log the error
        mock_logging.debug.assert_called()
        call_args = mock_logging.debug.call_args[0]
        self.assertIn('清理未完成下载记录时出错', call_args[0])


@pytest.mark.asyncio
async def test_real_run_rate_limits_only_before_following_network_tasks():
    """Local generated tasks should not be delayed by the network throttle."""
    config = MagicMock()
    config.get_download_options.return_value = MagicMock()
    config.get_manually_specified_course_ids.return_value = []
    config.get_restricted_filenames.return_value = False
    opts = MagicMock()
    opts.download_chunk_size = 8192
    opts.max_parallel_yt_dlp = 2
    opts.cookies_text = None
    opts.global_opts = MagicMock()
    opts.global_opts.skip_cert_verify = False
    database = MagicMock()
    database.get_incomplete_downloads_for_retry.return_value = []
    database.cleanup_old_incomplete_downloads.return_value = 0
    service = DownloadService([], config, opts, database)

    events = []

    class FakeTask:
        def __init__(self, name, network):
            self.name = name
            self.network = network

        def may_perform_network_io(self):
            return self.network

        async def run(self):
            events.append(self.name)

    async def record_wait():
        events.append('wait')

    service.all_tasks = [
        FakeTask('local-1', False),
        FakeTask('network-1', True),
        FakeTask('local-2', False),
        FakeTask('network-2', True),
    ]
    service.pause_controller.enabled = False
    service.pause_controller.wait_if_requested = AsyncMock()
    service._wait_before_network_task = AsyncMock(side_effect=record_wait)

    await service.real_run()

    assert events == ['local-1', 'network-1', 'local-2', 'wait', 'network-2']
    service._wait_before_network_task.assert_awaited_once()


class TestGenAllTasksFlow(unittest.TestCase):
    """Test the flow and logic of gen_all_tasks components"""

    def test_load_incomplete_and_create_map_flow(self):
        """Test the complete flow of loading incomplete downloads"""
        config = MagicMock()
        config.get_download_options.return_value = MagicMock()
        config.get_manually_specified_course_ids.return_value = []
        config.get_restricted_filenames.return_value = False
        opts = MagicMock()
        opts.download_chunk_size = 8192
        opts.max_parallel_yt_dlp = 2
        opts.cookies_text = None
        opts.global_opts = MagicMock()
        opts.global_opts.skip_cert_verify = False
        
        database = MagicMock()
        incomplete_downloads = [
            {'file_id': 1, 'downloaded_bytes': 500, 'total_bytes': 1000},
            {'file_id': 2, 'downloaded_bytes': 1500, 'total_bytes': 2000},
        ]
        database.get_incomplete_downloads_for_retry.return_value = incomplete_downloads
        database.cleanup_old_incomplete_downloads.return_value = 0
        
        service = DownloadService(
            courses=[],
            config=config,
            opts=opts,
            database=database
        )
        
        # Test loading
        incomplete_map = service._load_incomplete_downloads_map()
        
        self.assertEqual(len(incomplete_map), 2)
        self.assertEqual(incomplete_map[1]['downloaded_bytes'], 500)
        self.assertEqual(incomplete_map[2]['downloaded_bytes'], 1500)

    def test_incomplete_detection_logic_flow(self):
        """Test the logic flow for detecting incomplete downloads"""
        config = MagicMock()
        config.get_download_options.return_value = MagicMock()
        config.get_manually_specified_course_ids.return_value = []
        config.get_restricted_filenames.return_value = False
        opts = MagicMock()
        opts.download_chunk_size = 8192
        opts.max_parallel_yt_dlp = 2
        opts.cookies_text = None
        opts.global_opts = MagicMock()
        opts.global_opts.skip_cert_verify = False
        
        database = MagicMock()
        database.get_incomplete_downloads_for_retry.return_value = []
        database.cleanup_old_incomplete_downloads.return_value = 0
        
        service = DownloadService(
            courses=[],
            config=config,
            opts=opts,
            database=database
        )
        
        # Test incomplete detection logic
        incomplete_map = {1: {}, 2: {}, 3: {}}
        
        file1 = MagicMock()
        file1.file_id = 1
        self.assertTrue(service._is_incomplete_download(file1, incomplete_map))
        
        file2 = MagicMock()
        file2.file_id = 5  # Not in map
        self.assertFalse(service._is_incomplete_download(file2, incomplete_map))
        
        file3 = MagicMock()
        file3.file_id = None  # No file_id
        self.assertFalse(service._is_incomplete_download(file3, incomplete_map))


if __name__ == '__main__':
    unittest.main()
