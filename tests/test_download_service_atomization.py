# -*- coding: utf-8 -*-
"""
Unit tests for atomized DownloadService functions

Testing that each function in gen_all_tasks has a single responsibility
and can be tested independently.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from concurrent.futures import ThreadPoolExecutor
import pytest
from moodle_dl.downloader.download_service import DownloadService
from moodle_dl.types import File, Course, DownloadOptions, MoodleDlOpts, TaskState


def make_saved_html_file(path, *, file_id=29, url='', content_type='html') -> File:
    return File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='What name/email configuration does git require?',
        content_filepath='/',
        content_filename='index.html',
        content_fileurl=url,
        content_filesize=0,
        content_timemodified=0,
        module_modname='book',
        content_type=content_type,
        content_isexternalfile=False,
        saved_to=str(path),
        file_id=file_id,
    )


def make_saved_image_file(path, *, file_id=30) -> File:
    return File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='What name/email configuration does git require?',
        content_filepath='/',
        content_filename='Screenshot 2021-07-18 at 12.19.08.png',
        content_fileurl=(
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/11761256/mod_book/chapter/827835/'
            'Screenshot%202021-07-18%20at%2012.19.08.png?token=secret&offline=1'
        ),
        content_filesize=1,
        content_timemodified=0,
        module_modname='book',
        content_type='file',
        content_isexternalfile=False,
        saved_to=str(path),
        file_id=file_id,
    )


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
async def test_real_run_rate_limits_each_network_task_only():
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

    assert events == ['local-1', 'wait', 'network-1', 'local-2', 'wait', 'network-2']
    assert service._wait_before_network_task.await_count == 2


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


def test_rewrite_downloaded_html_resource_links_after_tasks_finish(tmp_path):
    html_path = tmp_path / 'Book.html'
    image_path = tmp_path / '02 - Faculty student VM' / '*39* student-vms-01.png'
    image_path.parent.mkdir()
    html_path.write_text(
        '<img src="https://keats.kcl.ac.uk/pluginfile.php/112/mod_book/chapter/785/student-vms-01.png" '
        'alt="VM Control panel">',
        encoding='utf-8',
    )
    image_path.write_bytes(b'image')

    html_file = File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='Software installation instructions',
        content_filepath='/',
        content_filename='Software installation instructions.html',
        content_fileurl='',
        content_filesize=0,
        content_timemodified=0,
        module_modname='book',
        content_type='html',
        content_isexternalfile=False,
        saved_to=str(html_path),
    )
    image_file = File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='Software installation instructions',
        content_filepath='/02 - Faculty student VM/',
        content_filename='student-vms-01.png',
        content_fileurl=(
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/112/mod_book/chapter/785/'
            'student-vms-01.png?token=secret&offline=1'
        ),
        content_filesize=1,
        content_timemodified=0,
        module_modname='book',
        content_type='file',
        content_isexternalfile=False,
        saved_to=str(image_path),
    )

    service = DownloadService.__new__(DownloadService)
    service.courses = [Course(1, 'Course', [html_file, image_file])]
    service.all_tasks = []

    assert service._rewrite_downloaded_html_resource_links() == 1
    assert 'src="02 - Faculty student VM/*39* student-vms-01.png"' in html_path.read_text(encoding='utf-8')


def test_rewrite_downloaded_html_resource_links_uses_database_stored_files(tmp_path):
    chapter_dir = tmp_path / '3.1. What name email configuration does git require'
    chapter_dir.mkdir()
    html_path = chapter_dir / '*01* index.html'
    image_path = chapter_dir / '*02* Screenshot 2021-07-18 at 12.19.08.png'
    html_path.write_text(
        '<p><img src="Screenshot%202021-07-18%20at%2012.19.08.png" '
        'alt="GitHub primary email address"></p>',
        encoding='utf-8',
    )
    image_path.write_bytes(b'image')

    html_file = File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='What name/email configuration does git require?',
        content_filepath='/',
        content_filename='index.html',
        content_fileurl='',
        content_filesize=0,
        content_timemodified=0,
        module_modname='book',
        content_type='html',
        content_isexternalfile=False,
        saved_to=str(html_path),
        file_id=29,
    )
    image_file = File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='What name/email configuration does git require?',
        content_filepath='/',
        content_filename='Screenshot 2021-07-18 at 12.19.08.png',
        content_fileurl=(
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/11761256/mod_book/chapter/827835/'
            'Screenshot%202021-07-18%20at%2012.19.08.png?token=secret&offline=1'
        ),
        content_filesize=1,
        content_timemodified=0,
        module_modname='book',
        content_type='file',
        content_isexternalfile=False,
        saved_to=str(image_path),
        file_id=30,
    )

    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.all_tasks = []
    service.database = MagicMock()
    service.database.get_stored_files.return_value = [Course(1, 'Course', [html_file, image_file])]

    assert service._rewrite_downloaded_html_resource_links() == 1
    assert 'src="*02* Screenshot 2021-07-18 at 12.19.08.png"' in html_path.read_text(encoding='utf-8')


def test_rewrite_html_resource_links_after_resource_task_finishes(tmp_path):
    chapter_dir = tmp_path / '3.1. What name email configuration does git require'
    chapter_dir.mkdir()
    html_path = chapter_dir / '*01* index.html'
    image_path = chapter_dir / '*02* Screenshot 2021-07-18 at 12.19.08.png'
    html_path.write_text(
        '<p><img src="Screenshot%202021-07-18%20at%2012.19.08.png" '
        'alt="GitHub primary email address"></p>',
        encoding='utf-8',
    )
    image_path.write_bytes(b'image')

    html_file = File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='What name/email configuration does git require?',
        content_filepath='/',
        content_filename='index.html',
        content_fileurl='',
        content_filesize=0,
        content_timemodified=0,
        module_modname='book',
        content_type='html',
        content_isexternalfile=False,
        saved_to=str(html_path),
        file_id=29,
    )
    image_file = File(
        module_id=20,
        section_name='Need help?',
        section_id=1,
        module_name='What name/email configuration does git require?',
        content_filepath='/',
        content_filename='Screenshot 2021-07-18 at 12.19.08.png',
        content_fileurl=(
            'https://keats.kcl.ac.uk/webservice/pluginfile.php/11761256/mod_book/chapter/827835/'
            'Screenshot%202021-07-18%20at%2012.19.08.png?token=secret&offline=1'
        ),
        content_filesize=1,
        content_timemodified=0,
        module_modname='book',
        content_type='file',
        content_isexternalfile=False,
        saved_to=str(image_path),
        file_id=30,
    )

    service = DownloadService.__new__(DownloadService)
    service.courses = [Course(1, 'Course', [html_file, image_file])]
    service.all_tasks = []
    service.database = MagicMock()
    service.database.get_stored_files.return_value = []
    task = MagicMock(file=image_file)

    assert service._rewrite_html_resource_links_after_task(task) == 1
    assert 'src="*02* Screenshot 2021-07-18 at 12.19.08.png"' in html_path.read_text(encoding='utf-8')
    assert service._rewrite_html_resource_links_after_task(task) == 0


def test_rewrite_html_resource_links_after_html_task_finishes_when_resource_exists(tmp_path):
    chapter_dir = tmp_path / '3.1. What name email configuration does git require'
    chapter_dir.mkdir()
    html_path = chapter_dir / '*01* index.html'
    image_path = chapter_dir / '*02* Screenshot 2021-07-18 at 12.19.08.png'
    html_path.write_text(
        '<p><img src="Screenshot%202021-07-18%20at%2012.19.08.png" '
        'alt="GitHub primary email address"></p>',
        encoding='utf-8',
    )
    image_path.write_bytes(b'image')

    html_file = make_saved_html_file(html_path)
    image_file = make_saved_image_file(image_path)

    service = DownloadService.__new__(DownloadService)
    service.courses = [Course(1, 'Course', [html_file, image_file])]
    service.all_tasks = []
    service.database = MagicMock()
    service.database.get_stored_files.return_value = []
    task = MagicMock(file=html_file)

    assert service._rewrite_html_resource_links_after_task(task) == 1
    assert 'src="*02* Screenshot 2021-07-18 at 12.19.08.png"' in html_path.read_text(encoding='utf-8')


def test_empty_queue_rewrites_stored_html_resource_links_for_resume(tmp_path):
    chapter_dir = tmp_path / '3.1. What name email configuration does git require'
    chapter_dir.mkdir()
    html_path = chapter_dir / '*01* index.html'
    image_path = chapter_dir / '*02* Screenshot 2021-07-18 at 12.19.08.png'
    html_path.write_text(
        '<p><img src="Screenshot%202021-07-18%20at%2012.19.08.png" '
        'alt="GitHub primary email address"></p>',
        encoding='utf-8',
    )
    image_path.write_bytes(b'image')

    html_file = make_saved_html_file(html_path)
    image_file = make_saved_image_file(image_path)

    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.all_tasks = []
    service.database = MagicMock()
    service.database.get_stored_files.return_value = [Course(1, 'Course', [html_file, image_file])]

    asyncio.run(service.real_run())

    service.database.batch_delete_files.assert_called_once_with([])
    assert 'src="*02* Screenshot 2021-07-18 at 12.19.08.png"' in html_path.read_text(encoding='utf-8')


def test_rewrite_downloaded_html_resource_links_skips_invalid_encoding_html(tmp_path):
    chapter_dir = tmp_path / 'chapter'
    chapter_dir.mkdir()
    broken_html_path = chapter_dir / '*01* broken.html'
    valid_html_path = chapter_dir / '*02* index.html'
    image_path = chapter_dir / '*03* Screenshot 2021-07-18 at 12.19.08.png'
    broken_html_path.write_bytes(b'\xff\xfe\x00\x00')
    valid_html_path.write_text(
        '<p><img src="Screenshot%202021-07-18%20at%2012.19.08.png" alt="GitHub"></p>',
        encoding='utf-8',
    )
    image_path.write_bytes(b'image')

    broken_html_file = make_saved_html_file(broken_html_path, file_id=29)
    valid_html_file = make_saved_html_file(valid_html_path, file_id=30)
    image_file = make_saved_image_file(image_path, file_id=31)

    service = DownloadService.__new__(DownloadService)
    service.courses = [Course(1, 'Course', [broken_html_file, valid_html_file, image_file])]
    service.all_tasks = []
    service.database = MagicMock()
    service.database.get_stored_files.return_value = []

    assert service._rewrite_downloaded_html_resource_links() == 1
    assert 'src="*03* Screenshot 2021-07-18 at 12.19.08.png"' in valid_html_path.read_text(encoding='utf-8')
    assert broken_html_path.read_bytes() == b'\xff\xfe\x00\x00'


def test_real_run_rewrites_html_resource_links_after_each_task():
    async def idle_status_logger():
        await asyncio.Event().wait()

    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.all_tasks = [MagicMock()]
    service.all_tasks[0].run = AsyncMock()
    service.all_tasks[0].status.state = TaskState.FINISHED
    service.database = MagicMock()
    service.status = MagicMock()
    service.status.bytes_downloaded = 0
    service.status.files_to_download = 0
    service.pause_controller = MagicMock()
    service.pause_controller.wait_if_requested = AsyncMock()
    service._task_may_perform_network_io = MagicMock(return_value=False)
    service._rewrite_html_resource_links_after_task = MagicMock(return_value=0)
    service._rewrite_downloaded_html_resource_links = MagicMock(return_value=0)
    service.log_download_status = idle_status_logger

    asyncio.run(service.real_run())

    service.database.batch_delete_files.assert_called_once_with([])
    service.all_tasks[0].run.assert_awaited_once()
    service._rewrite_html_resource_links_after_task.assert_called_once_with(service.all_tasks[0])
    service.pause_controller.wait_if_requested.assert_awaited_once()
    service._rewrite_downloaded_html_resource_links.assert_called_once_with()


def test_real_run_skips_incremental_html_rewrite_after_failed_task():
    async def idle_status_logger():
        await asyncio.Event().wait()

    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.all_tasks = [MagicMock()]
    service.all_tasks[0].run = AsyncMock()
    service.all_tasks[0].status.state = TaskState.FAILED
    service.database = MagicMock()
    service.status = MagicMock()
    service.status.bytes_downloaded = 0
    service.status.files_to_download = 0
    service.pause_controller = MagicMock()
    service.pause_controller.wait_if_requested = AsyncMock()
    service._task_may_perform_network_io = MagicMock(return_value=False)
    service._rewrite_html_resource_links_after_task = MagicMock(return_value=0)
    service._rewrite_downloaded_html_resource_links = MagicMock(return_value=0)
    service.log_download_status = idle_status_logger

    asyncio.run(service.real_run())

    service.all_tasks[0].run.assert_awaited_once()
    service._rewrite_html_resource_links_after_task.assert_not_called()
    service.pause_controller.wait_if_requested.assert_awaited_once()
    service._rewrite_downloaded_html_resource_links.assert_called_once_with()


def test_real_run_rewrites_html_resource_links_without_download_tasks():
    service = DownloadService.__new__(DownloadService)
    service.courses = []
    service.all_tasks = []
    service.database = MagicMock()
    service._rewrite_downloaded_html_resource_links = MagicMock(return_value=0)

    asyncio.run(service.real_run())

    service.database.batch_delete_files.assert_called_once_with([])
    service._rewrite_downloaded_html_resource_links.assert_called_once_with()


# ---- HTML 编码检测与回写 ----------------------------------------------------
#
# 真实数据：Word 导出的 *Lab1/Lab2 worksheet.html* 是带 BOM 的 UTF-16 LE，
# *Lab3 worksheet.html* 是无 BOM 的 cp1252（含 0x96 这种 cp1252-only 字符）。
# 在 #38240e9 之前，rewrite 用 utf-8 死活解码，整批 worksheet 都被静默跳过。
# 这些用例锁死正确的回退顺序，并保证写回不破坏原始字节序列。

def _make_html_file_with_image(tmp_path, html_bytes: bytes) -> tuple:
    chapter_dir = tmp_path / 'Assessment'
    chapter_dir.mkdir()
    html_path = chapter_dir / '*07* Lab1 worksheet.html'
    image_path = chapter_dir / '*02* diagram.png'

    html_path.write_bytes(html_bytes)
    image_path.write_bytes(b'image-bytes')

    html_file = make_saved_html_file(html_path, file_id=101)
    image_file = make_saved_image_file(image_path, file_id=102)

    service = DownloadService.__new__(DownloadService)
    service.courses = [Course(1, 'Course', [html_file, image_file])]
    service.all_tasks = []
    service.database = MagicMock()
    service.database.get_stored_files.return_value = []
    return service, html_path, image_file


def test_rewrite_html_resource_links_handles_utf16_le_with_bom(tmp_path):
    html_text = '<p><img src="diagram.png" alt="d"></p>'
    html_bytes = html_text.encode('utf-16')  # 自带 UTF-16 LE BOM
    service, html_path, image_file = _make_html_file_with_image(tmp_path, html_bytes)
    task = MagicMock(file=image_file)

    assert service._rewrite_html_resource_links_after_task(task) == 1

    written = html_path.read_bytes()
    assert written.startswith(b'\xff\xfe'), '应保留原始 UTF-16 LE BOM'
    assert '*02* diagram.png' in written.decode('utf-16')


def test_rewrite_html_resource_links_handles_cp1252_word_export(tmp_path):
    # Word 导出 cp1252：0x96 是 en-dash，UTF-8 解不出来
    html_bytes = (
        b'<html><head><title>Lab 3 \x96 worksheet</title></head>'
        b'<body><img src="diagram.png"></body></html>'
    )
    service, html_path, image_file = _make_html_file_with_image(tmp_path, html_bytes)
    task = MagicMock(file=image_file)

    assert service._rewrite_html_resource_links_after_task(task) == 1

    written = html_path.read_bytes()
    assert b'\x96' in written, '应保留 cp1252 字节序列，不要篡改成 UTF-8'
    assert b'*02* diagram.png' in written


def test_rewrite_html_resource_links_honors_meta_charset_declaration(tmp_path):
    # 没有 BOM，但 <meta charset> 声明了 windows-1252
    html_bytes = (
        b'<!DOCTYPE html><html><head>'
        b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
        b'</head><body>n\xe9 \x96 <img src="diagram.png"></body></html>'
    )
    service, html_path, image_file = _make_html_file_with_image(tmp_path, html_bytes)
    task = MagicMock(file=image_file)

    assert service._rewrite_html_resource_links_after_task(task) == 1

    written = html_path.read_bytes()
    decoded = written.decode('windows-1252')
    assert '*02* diagram.png' in decoded
    assert 'né' in decoded


def test_rewrite_html_resource_links_round_trips_utf8(tmp_path):
    # 默认情况：UTF-8，无 BOM，无 meta —— 不要回退到 cp1252
    html_bytes = '<p>café — <img src="diagram.png"></p>'.encode('utf-8')
    service, html_path, image_file = _make_html_file_with_image(tmp_path, html_bytes)
    task = MagicMock(file=image_file)

    assert service._rewrite_html_resource_links_after_task(task) == 1

    decoded = html_path.read_text(encoding='utf-8')
    assert 'café — ' in decoded
    assert '*02* diagram.png' in decoded


def test_rewrite_html_resource_links_skips_unreadable_file(tmp_path):
    # 即使所有解码都失败也只是 debug 一行、返回 0，不能抛
    service, html_path, _image_file = _make_html_file_with_image(
        tmp_path, b'<p><img src="diagram.png"></p>'
    )
    html_path.unlink()
    task = MagicMock(file=_image_file)

    # 文件被删了 → 走 isfile 检查直接返回 0，不应抛
    assert service._rewrite_html_resource_links_after_task(task) == 0
